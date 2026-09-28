"""RSS feed polling for award chart data — parallel to HTTP scraping."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import yaml
from bs4 import BeautifulSoup

from scrapers.base import ScrapedRow
from scrapers.blog_parsers import parse_blog_prose, parse_blog_table
from scrapers.scraper_logger import record_failure, record_success

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
RSS_FEEDS_PATH = ROOT / "config" / "rss_feeds.yaml"
SEEN_PATH = ROOT / "logs" / "rss_seen.json"
MAX_ENTRIES_PER_FEED = 30


def load_rss_feeds(path: Path | None = None) -> dict:
    p = path or RSS_FEEDS_PATH
    if not p.exists():
        return {}
    with open(p) as f:
        return yaml.safe_load(f) or {}


def _load_seen() -> dict[str, list[str]]:
    if not SEEN_PATH.exists():
        return {}
    try:
        with open(SEEN_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_seen(seen: dict[str, list[str]]) -> None:
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SEEN_PATH, "w") as f:
        json.dump(seen, f, indent=2)


def _entry_matches(entry: dict, keywords: list[str]) -> bool:
    text = " ".join(
        str(entry.get(k, "")) for k in ("title", "summary", "description", "content")
    ).lower()
    return any(kw.lower() in text for kw in keywords)


def _entry_html(entry: dict) -> str:
    for key in ("content", "summary", "description"):
        val = entry.get(key)
        if isinstance(val, list) and val:
            return val[0].get("value", "")
        if isinstance(val, str) and val:
            return val
    return entry.get("title", "")


def _entry_published(entry: dict) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                pass
    return None


def poll_program_feeds(
    program: str,
    feed_configs: list[dict],
    *,
    rt_ow_context: str | None = None,
) -> list[ScrapedRow]:
    """Poll RSS feeds for a program; parse matching entries into ScrapedRows."""
    seen = _load_seen()
    program_seen = set(seen.get(program, []))
    new_guids: list[str] = []
    rows: list[ScrapedRow] = []
    now = datetime.now(timezone.utc)

    for feed_cfg in feed_configs:
        feed_url = feed_cfg["url"]
        keywords = feed_cfg.get("keywords", [program.replace("_", " ")])
        feed_host = urlparse(feed_url).netloc.lstrip("www.")

        try:
            parsed = feedparser.parse(feed_url)
        except Exception as exc:
            record_failure(program, feed_url, "rss_parse_error", response_snippet=str(exc)[:200])
            log.warning("RSS feed %s failed: %s", feed_url, exc)
            continue

        if parsed.bozo and not parsed.entries:
            record_failure(program, feed_url, "rss_invalid", response_snippet=str(parsed.bozo_exception)[:200])
            continue

        matched = 0
        for entry in parsed.entries[:MAX_ENTRIES_PER_FEED]:
            guid = entry.get("id") or entry.get("link") or entry.get("title", "")
            if not _entry_matches(entry, keywords):
                continue

            html = _entry_html(entry)
            if not html or len(html) < 40:
                continue

            link = entry.get("link", feed_url)
            published = _entry_published(entry)

            # Try table parse first (full-content feeds), then prose
            wrapped = f"<html><body>{html}</body></html>"
            parsed_rows = parse_blog_table(
                wrapped, program, link,
                rt_ow_context=rt_ow_context,
                source_updated_at=published,
            )
            if not parsed_rows:
                parsed_rows = parse_blog_prose(
                    wrapped, program, link,
                    rt_ow_context=rt_ow_context,
                    source_updated_at=published,
                )

            if not parsed_rows:
                continue

            for row in parsed_rows:
                row.source_name = f"rss:{feed_host}"
                row.source_url = link
                row.scraped_at = now
                if published and row.source_updated_at is None:
                    row.source_updated_at = published
                row.flags.append("rss_feed")
                rows.append(row)

            matched += 1
            if guid not in program_seen:
                new_guids.append(guid)

        if matched:
            record_success()
            log.info("%s: %d RSS rows from %s", program, matched, feed_host)
        else:
            log.debug("%s: no matching RSS entries in %s", program, feed_host)

    if new_guids:
        seen[program] = list(program_seen | set(new_guids))[-500:]
        _save_seen(seen)

    return rows


def poll_all_programs(
    programs: list[str] | None = None,
    *,
    rt_ow_by_program: dict[str, str] | None = None,
) -> dict[str, list[ScrapedRow]]:
    feeds_cfg = load_rss_feeds()
    rt_ow_by_program = rt_ow_by_program or {}
    results: dict[str, list[ScrapedRow]] = {}

    for program, feed_list in feeds_cfg.items():
        if programs and program not in programs:
            continue
        if not isinstance(feed_list, list):
            continue
        rows = poll_program_feeds(
            program,
            feed_list,
            rt_ow_context=rt_ow_by_program.get(program),
        )
        if rows:
            results[program] = rows

    return results
