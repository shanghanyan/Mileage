"""Chart scraper driven by config/scrape_targets.yaml + RSS feeds."""

from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml

from scrapers.awardtravelfinder import parse_zone_table
from scrapers.base import ScrapedRow
from scrapers.blog_parsers import parse_blog_prose, parse_blog_table
from scrapers.exceptions import RateLimitError, WaybackSnapshotMissingError
from scrapers.freshness_extract import extract_source_updated_at
from scrapers.http_util import fetch_html_resilient, is_bot_blocked
from scrapers.pdf_chart import parse_pdf_chart
from scrapers.rss_awards import poll_all_programs
from scrapers.scraper_logger import record_attempt, record_failure, record_success
from scrapers.wayback import wayback_source_name
from verify.trust import attach_trust

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
TARGETS_PATH = ROOT / "config" / "scrape_targets.yaml"

RT_OW_BY_PROGRAM = {"ana_mileage": "round_trip"}


def load_scrape_targets(path: Path | None = None) -> dict:
    p = path or TARGETS_PATH
    with open(p) as f:
        data = yaml.safe_load(f) or {}
    return {k: (v if isinstance(v, list) else []) for k, v in data.items()}


def _sort_targets(targets: list[dict]) -> list[dict]:
    def sort_key(t: dict) -> tuple:
        if t.get("last_404"):
            return (1, t["last_404"])
        return (0, "")

    return sorted(targets, key=sort_key)


def _clone_rows_for_programs(rows: list[ScrapedRow], programs: list[str]) -> list[ScrapedRow]:
    cloned: list[ScrapedRow] = []
    for extra in programs:
        for row in rows:
            copy = deepcopy(row)
            copy.from_program = extra
            copy.flags.append(f"shared_from:{row.from_program}")
            cloned.append(copy)
    return cloned


async def _fetch_pdf(url: str, timeout: float = 30.0) -> bytes:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


def _parse_target_content(
    content: str | bytes,
    program: str,
    url: str,
    target: dict,
    *,
    source_updated_at,
    fetch_method: str,
) -> list[ScrapedRow]:
    parser = target.get("parser", "blog_table")
    rt_ow = target.get("rt_ow_context")
    if fetch_method in ("wayback", "wayback_only"):
        source_name = wayback_source_name(url)
    else:
        source_name = urlparse(url).netloc.lstrip("www.")

    if parser == "pdf_chart":
        if not isinstance(content, bytes):
            raise ValueError("PDF parser requires bytes content")
        return parse_pdf_chart(content, program, url, source_name)

    html = content if isinstance(content, str) else content.decode("utf-8", errors="replace")

    if parser == "awardtravelfinder":
        rows = parse_zone_table(html, program, url, source_name)
    elif parser == "blog_prose":
        rows = parse_blog_prose(
            html, program, url, rt_ow_context=rt_ow, source_updated_at=source_updated_at
        )
    else:
        rows = parse_blog_table(
            html, program, url, rt_ow_context=rt_ow, source_updated_at=source_updated_at
        )

    also = target.get("also_programs") or []
    if also:
        rows = rows + _clone_rows_for_programs(rows, also)

    return rows


async def scrape_program(
    program: str,
    targets: list[dict],
    block_signatures: list[str],
) -> list[ScrapedRow]:
    """Try HTTP/Wayback/PDF targets, then merge RSS feed rows."""
    all_rows: list[ScrapedRow] = []
    sorted_targets = _sort_targets(targets)

    for target in sorted_targets:
        url = target["url"]
        if target.get("last_404"):
            log.debug("Skipping URL with last_404: %s", url)
            continue

        record_attempt(program)
        fetch_via = target.get("fetch_via")
        parser = target.get("parser", "blog_table")

        try:
            if parser == "pdf_chart":
                pdf_bytes = await _fetch_pdf(url)
                updated_at = None
                fetch_method = "live"
                content: str | bytes = pdf_bytes
            else:
                html, last_modified, status, snapshot_dt, fetch_method = await fetch_html_resilient(
                    url,
                    block_signatures=block_signatures,
                    fetch_via=fetch_via,
                )
                if fetch_method == "live" and is_bot_blocked(html, status):
                    record_failure(program, url, "bot_block", http_status=status, response_snippet=html[:200])
                    continue
                updated_at = snapshot_dt or extract_source_updated_at(
                    html, last_modified=last_modified, page_url=url
                )
                content = html

            rows = _parse_target_content(
                content, program, url, target,
                source_updated_at=updated_at,
                fetch_method=fetch_method,
            )

            if not rows:
                record_failure(program, url, "parse_empty")
                continue

            for row in rows:
                if row.source_updated_at is None:
                    row.source_updated_at = updated_at
                if fetch_method in ("wayback", "wayback_only"):
                    row.flags.append(f"wayback:{fetch_method}")
                if fetch_method == "live":
                    row.flags.append("live")
                attach_trust(row)
                all_rows.append(row)

            record_success(program)
            log.info("%s: %d rows from %s (%s)", program, len(rows), url, fetch_method)

        except RateLimitError as exc:
            record_failure(program, url, "wayback_rate_limit", response_snippet=str(exc)[:200])
            log.warning("%s source %s: %s", program, url, exc)
        except WaybackSnapshotMissingError as exc:
            record_failure(program, url, "no_wayback_snapshot", response_snippet=str(exc)[:200])
            log.warning("%s source %s: %s", program, url, exc)
        except Exception as exc:
            error_type = "unknown"
            http_status = None
            snippet = str(exc)[:200]
            if "404" in str(exc):
                error_type = "404"
            elif "bot" in str(exc).lower() or "block" in str(exc).lower():
                error_type = "bot_block"
            elif "timeout" in str(exc).lower():
                error_type = "timeout"
            record_failure(program, url, error_type, http_status=http_status, response_snippet=snippet)
            log.warning("%s source %s failed: %s", program, url, exc)

    rss_rows = poll_all_programs(
        [program],
        rt_ow_by_program=RT_OW_BY_PROGRAM,
    ).get(program, [])
    for row in rss_rows:
        attach_trust(row)
    all_rows.extend(rss_rows)

    return all_rows


async def scrape_all_programs(
    config: dict,
    programs: list[str] | None = None,
) -> dict[str, list[ScrapedRow]]:
    targets_cfg = load_scrape_targets()
    block_sigs = config.get("block_signatures", [])
    results: dict[str, list[ScrapedRow]] = {}

    program_list = programs or [
        k for k, v in targets_cfg.items()
        if k != "c1_transfer_partners" and (v or k in load_rss_programs())
    ]

    for program in program_list:
        targets = targets_cfg.get(program, [])
        results[program] = await scrape_program(program, targets, block_sigs)

    # Re-bucket rows tagged for other programs (e.g. qatar cloned from avios chart)
    merged: dict[str, list[ScrapedRow]] = {}
    for rows in results.values():
        for row in rows:
            merged.setdefault(row.from_program, []).append(row)
    return merged


def load_rss_programs() -> set[str]:
    from scrapers.rss_awards import load_rss_feeds
    return set(load_rss_feeds().keys())
