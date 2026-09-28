"""Wayback Machine fallback with throttling, caching, and explicit rate-limit handling."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx

from scrapers.exceptions import RateLimitError, WaybackSnapshotMissingError

log = logging.getLogger(__name__)

CDX_API = "https://archive.org/wayback/available"
WAYBACK_RETRY_DELAYS = [2.0, 5.0, 15.0]
CACHE_MAX_AGE_DAYS = int(os.getenv("WAYBACK_CACHE_DAYS", "7"))


class RateLimiter:
    """Enforce minimum interval between Wayback API calls (~1 req/s)."""

    def __init__(self, min_interval: float = 1.2):
        self.min_interval = min_interval
        self._last_call = 0.0

    async def wait(self) -> None:
        loop = asyncio.get_event_loop()
        elapsed = loop.time() - self._last_call
        if elapsed < self.min_interval:
            await asyncio.sleep(self.min_interval - elapsed)
        self._last_call = loop.time()


_wayback_limiter = RateLimiter(min_interval=float(os.getenv("WAYBACK_MIN_INTERVAL", "1.2")))


def _get_cache():
    from db.store import Database

    db_path = os.getenv("SQLITE_PATH", str(
        __import__("pathlib").Path(__file__).resolve().parent.parent / "storage" / "aggregator.db"
    ))
    db = Database(db_path)
    db.init()
    return db


async def get_wayback_snapshot(
    client: httpx.AsyncClient,
    url: str,
    *,
    use_cache: bool = True,
) -> tuple[str | None, datetime | None]:
    """
    Return (snapshot_url, snapshot_datetime). Raises RateLimitError on 429.
    Raises WaybackSnapshotMissingError when API succeeds but no snapshot exists.
    """
    if use_cache:
        cached = _get_cache().get_wayback_cache(url, max_age_days=CACHE_MAX_AGE_DAYS)
        if cached is not None:
            snapshot_url, snapshot_ts, checked_at = cached
            if snapshot_url is None:
                raise WaybackSnapshotMissingError(f"No archived snapshot for {url} (cached miss)")
            log.debug("Wayback cache hit for %s (checked %s)", url, checked_at.date())
            dt = None
            if snapshot_ts:
                try:
                    dt = datetime.strptime(snapshot_ts[:14], "%Y%m%d%H%M%S").replace(
                        tzinfo=timezone.utc
                    )
                except ValueError:
                    pass
            return snapshot_url, dt

    await _wayback_limiter.wait()

    resp = await client.get(CDX_API, params={"url": url})

    if resp.status_code == 429:
        raise RateLimitError(f"Wayback rate-limited for {url}")

    resp.raise_for_status()
    data = resp.json()

    closest = data.get("archived_snapshots", {}).get("closest")
    if not closest or not closest.get("available"):
        _get_cache().save_wayback_cache(url, None, None)
        raise WaybackSnapshotMissingError(f"No archived snapshot for {url}")

    snapshot_url = closest.get("url")
    if not snapshot_url:
        raise WaybackSnapshotMissingError(f"No archived snapshot URL for {url}")

    ts = closest.get("timestamp", "")
    snapshot_dt: datetime | None = None
    if ts and len(ts) >= 8:
        try:
            snapshot_dt = datetime.strptime(ts[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    _get_cache().save_wayback_cache(url, snapshot_url, ts)
    log.info("Wayback snapshot for %s → %s (%s)", url, snapshot_url, ts)
    return snapshot_url, snapshot_dt


async def fetch_wayback_html(
    url: str,
    *,
    timeout: float = 30.0,
) -> tuple[str, datetime | None, str]:
    """Fetch HTML from Wayback with rate-limit retry and cache."""
    last_rate_limit: RateLimitError | None = None

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for attempt, delay in enumerate([0.0] + WAYBACK_RETRY_DELAYS):
            if delay > 0:
                log.info("Wayback rate-limited, retrying in %.0fs (attempt %d)", delay, attempt + 1)
                await asyncio.sleep(delay)

            try:
                snapshot_url, snapshot_dt = await get_wayback_snapshot(client, url)
                if not snapshot_url:
                    raise WaybackSnapshotMissingError(f"No Wayback snapshot for {url}")

                await _wayback_limiter.wait()
                resp = await client.get(snapshot_url)
                resp.raise_for_status()
                return resp.text, snapshot_dt, snapshot_url

            except RateLimitError as exc:
                last_rate_limit = exc
                log.warning("%s", exc)
                continue

        if last_rate_limit:
            raise last_rate_limit
        raise WaybackSnapshotMissingError(f"Wayback fetch exhausted retries for {url}")


def wayback_source_name(original_url: str) -> str:
    host = urlparse(original_url).netloc.lstrip("www.")
    return f"wayback:{host}"
