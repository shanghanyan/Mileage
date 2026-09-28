"""Shared HTTP fetch utilities with block detection and retry."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime
from pathlib import Path

import httpx
import yaml

from scrapers.exceptions import BlockDetectedError

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
}

BOT_BLOCK_SIGNATURES = [
    "challenge-platform",
    "cf-browser-verification",
    "_cf_chl_",
    "Please Wait... | Cloudflare",
    "Just a moment...",
    "Enable JavaScript and cookies",
    "Access denied",
    "403 Forbidden",
    "Akamai",
    "Please verify you are a human",
]

RETRY_DELAYS = [1.0, 3.0, 9.0]


def load_sources_config(config_path: Path | None = None) -> dict:
    path = config_path or Path(__file__).resolve().parent.parent / "config" / "sources.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


def is_bot_blocked(response_text: str, status_code: int) -> bool:
    if status_code in (403, 429):
        return True
    lower = response_text.lower()
    return any(sig.lower() in lower for sig in BOT_BLOCK_SIGNATURES)


def check_block(html: str, signatures: list[str]) -> None:
    for sig in signatures:
        if sig.lower() in html.lower():
            raise BlockDetectedError(f"Block signature detected: {sig!r}")


async def fetch_html(
    url: str,
    *,
    timeout: float = 25.0,
    block_signatures: list[str] | None = None,
    return_meta: bool = False,
    skip_live: bool = False,
) -> str | tuple[str, str | None, int]:
    """Fetch URL with exponential backoff for transient failures."""
    if skip_live:
        raise BlockDetectedError(f"Live fetch skipped for {url}")

    last_exc: Exception | None = None

    for attempt, delay in enumerate([0.0] + RETRY_DELAYS):
        if delay > 0:
            jitter = random.uniform(0, 1)
            await asyncio.sleep(delay + jitter)

        try:
            async with httpx.AsyncClient(
                headers=_HEADERS, timeout=timeout, follow_redirects=True
            ) as client:
                resp = await client.get(url)

                if resp.status_code == 404:
                    resp.raise_for_status()

                if resp.status_code in (403, 429):
                    if return_meta:
                        return resp.text, resp.headers.get("Last-Modified"), resp.status_code
                    raise BlockDetectedError(f"HTTP {resp.status_code} for {url}")

                if resp.status_code >= 500:
                    last_exc = httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}", request=resp.request, response=resp
                    )
                    continue

                resp.raise_for_status()
                text = resp.text

                if is_bot_blocked(text, resp.status_code):
                    raise BlockDetectedError(f"Bot block detected at {url}")

                if block_signatures:
                    check_block(text, block_signatures)

                if return_meta:
                    return text, resp.headers.get("Last-Modified"), resp.status_code
                return text

        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            last_exc = exc
            log.debug("Attempt %d for %s failed: %s", attempt + 1, url, exc)
            continue
        except BlockDetectedError:
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise
            if exc.response.status_code >= 500:
                last_exc = exc
                continue
            raise

    if last_exc:
        raise last_exc
    raise RuntimeError(f"Failed to fetch {url}")


async def fetch_html_resilient(
    url: str,
    *,
    timeout: float = 25.0,
    block_signatures: list[str] | None = None,
    fetch_via: str | None = None,
) -> tuple[str, str | None, int, datetime | None, str]:
    """
    Fetch page content with Wayback fallback on bot-block or when fetch_via=wayback_only.

    Returns (html, last_modified_header, status_code, source_updated_at, fetch_method).
    fetch_method is 'live', 'wayback', or 'wayback_only'.
    """
    from scrapers.exceptions import RateLimitError, WaybackSnapshotMissingError
    from scrapers.wayback import fetch_wayback_html

    use_wayback_only = fetch_via == "wayback_only"

    if not use_wayback_only:
        try:
            html, last_modified, status = await fetch_html(
                url, timeout=timeout, block_signatures=block_signatures, return_meta=True
            )
            if is_bot_blocked(html, status):
                raise BlockDetectedError(f"Bot block at {url}")
            return html, last_modified, status, None, "live"
        except BlockDetectedError:
            log.info("Live fetch blocked for %s — trying Wayback", url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise
            log.info("Live fetch failed for %s (%s) — trying Wayback", url, exc)
        except Exception as exc:
            if "404" in str(exc):
                raise
            log.info("Live fetch error for %s — trying Wayback: %s", url, exc)

    try:
        html, snapshot_dt, _snapshot_url = await fetch_wayback_html(url, timeout=timeout)
    except RateLimitError:
        raise
    except WaybackSnapshotMissingError:
        raise

    method = "wayback_only" if use_wayback_only else "wayback"
    return html, None, 200, snapshot_dt, method
