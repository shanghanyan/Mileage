"""Devaluation signal watcher via RSS feeds."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import feedparser

log = logging.getLogger(__name__)

PROGRAM_KEYWORDS = {
    "lifemiles": ["lifemiles", "avianca"],
    "aeroplan": ["aeroplan", "air canada"],
    "turkish_miles": ["turkish", "miles & smiles", "miles and smiles"],
    "ana_mileage": ["ana", "mileage club"],
    "krisflyer": ["krisflyer", "singapore"],
}


@dataclass
class DevaluationAlert:
    feed_name: str
    title: str
    url: str
    programs: list[str] = field(default_factory=list)
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _match_programs(title: str, keywords: list[str]) -> list[str]:
    title_lower = title.lower()
    matched: list[str] = []
    for program, prog_keywords in PROGRAM_KEYWORDS.items():
        if any(kw in title_lower for kw in prog_keywords):
            matched.append(program)
    if not matched:
        for kw in keywords:
            if kw.lower() in title_lower:
                for program, prog_keywords in PROGRAM_KEYWORDS.items():
                    if kw.lower() in " ".join(prog_keywords):
                        matched.append(program)
    return list(dict.fromkeys(matched))


async def check_devaluation_feeds(config: dict) -> list[DevaluationAlert]:
    alerts: list[DevaluationAlert] = []
    trigger_words = {"devaluation", "change", "update", "increase", "new chart"}

    for feed_cfg in config.get("devaluation_feeds", []):
        try:
            parsed = feedparser.parse(feed_cfg["url"])
        except Exception as exc:
            log.warning("RSS feed %s failed: %s", feed_cfg["name"], exc)
            continue

        for entry in parsed.entries[:20]:
            title = entry.get("title", "")
            title_lower = title.lower()
            if not any(w in title_lower for w in trigger_words):
                continue
            programs = _match_programs(title, feed_cfg.get("keywords", []))
            if not programs:
                continue
            alerts.append(
                DevaluationAlert(
                    feed_name=feed_cfg["name"],
                    title=title,
                    url=entry.get("link", feed_cfg["url"]),
                    programs=programs,
                )
            )

    return alerts
