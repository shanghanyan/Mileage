"""Source freshness date extraction from HTTP responses."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from bs4 import BeautifulSoup

UPDATED_TEXT_RE = re.compile(
    r"(?:Updated?\s+\w+\s+\d{1,2},?\s+\d{4}|Last updated:?\s+\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


def extract_source_updated_at(
    html: str,
    *,
    last_modified: str | None = None,
    page_url: str | None = None,
) -> datetime | None:
    """Extract page last-updated date using priority chain."""
    if last_modified:
        try:
            dt = parsedate_to_datetime(last_modified)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (TypeError, ValueError, IndexError):
            pass

    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                for key in ("dateModified", "datePublished"):
                    if val := item.get(key):
                        return _parse_iso(val)
        except (json.JSONDecodeError, TypeError):
            continue

    for prop in ("article:modified_time", "article:published_time"):
        tag = soup.find("meta", property=prop)
        if tag and tag.get("content"):
            dt = _parse_iso(tag["content"])
            if dt:
                return dt

    body_text = soup.get_text(" ", strip=True)[:3000]
    if m := UPDATED_TEXT_RE.search(body_text):
        raw = m.group(0)
        iso = re.search(r"(\d{4}-\d{2}-\d{2})", raw)
        if iso:
            return _parse_iso(iso.group(1))
        month = re.search(
            r"(?:Updated?\s+)?(\w+)\s+(\d{1,2}),?\s+(\d{4})", raw, re.IGNORECASE
        )
        if month:
            try:
                dt = datetime.strptime(
                    f"{month.group(1)} {month.group(2)} {month.group(3)}",
                    "%B %d %Y",
                )
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                pass

    return None


def _parse_iso(val: str) -> datetime | None:
    try:
        val = val.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(val)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None
