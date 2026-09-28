"""Write scrape artifacts (JSON / HTML / PNG) under artifacts/."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scrapers.contracts import PROJECT_ROOT, SiteContract
from scrapers.validation import ValidationResult


def artifacts_root() -> Path:
    raw = os.environ.get("SCRAPE_ARTIFACTS_DIR", "artifacts")
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp_slug(when: datetime | None = None) -> str:
    when = when or utc_now()
    return when.strftime("%Y%m%dT%H%M%SZ")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


@dataclass
class ArtifactBundle:
    json_path: Path
    html_path: Path
    screenshot_path: Path | None
    payload: dict[str, Any] = field(default_factory=dict)


def write_artifacts(
    contract: SiteContract,
    tier: int,
    validation: ValidationResult,
    html: str,
    screenshot_bytes: bytes | None = None,
    extra: dict[str, Any] | None = None,
    browser: str = "",
    proxy_type: str = "vps",
    egress_ip: str = "",
    machine_id: str = "",
    worker_id: str = "",
    timezone_name: str = "America/Los_Angeles",
    inspect_hits: list[dict[str, Any]] | None = None,
    selenium_xpath: str = "",
    selenium_css: str = "",
) -> ArtifactBundle:
    when = utc_now()
    slug = timestamp_slug(when)
    subdir = "chrome" if tier == 2 else "tor" if tier == 3 else f"tier{tier}"
    out_dir = artifacts_root() / subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{contract.key}_{tier}_{slug}"
    html_path = out_dir / f"{stem}.html"
    json_path = out_dir / f"{stem}.json"
    screenshot_path: Path | None = None

    html_path.write_text(html, encoding="utf-8")
    if screenshot_bytes:
        screenshot_path = out_dir / f"{stem}.png"
        screenshot_path.write_bytes(screenshot_bytes)

    chart = validation.chart
    payload: dict[str, Any] = {
        "alias": contract.alias,
        "key": contract.key,
        "tier": tier,
        "browser": browser,
        "url": contract.url,
        "final_url": validation.final_url,
        "scraped_at": when.isoformat(),
        "timezone": timezone_name,
        "outcome": validation.outcome,
        "deliverable_met": validation.deliverable_met,
        "chart_confidence": validation.chart_confidence,
        "messages": validation.messages,
        "bot_vendor": validation.bot_vendor,
        "bot_signals": validation.bot_signals,
        "text_length": validation.text_length,
        "title": validation.title,
        "chart_headers": chart.headers if chart else [],
        "chart_rows": chart.rows if chart else [],
        "lxml_path": chart.lxml_path if chart else "",
        "selenium_xpath": selenium_xpath,
        "selenium_css": selenium_css,
        "table_index": chart.table_index if chart else None,
        "egress_ip": egress_ip,
        "proxy_type": proxy_type,
        "machine_id": machine_id,
        "worker_id": worker_id,
        "html_path": str(html_path.relative_to(PROJECT_ROOT)),
        "screenshot_path": str(screenshot_path.relative_to(PROJECT_ROOT)) if screenshot_path else "",
        "content_hash": sha256_text(html),
        "inspect_hits": inspect_hits or [],
        "contract": contract.to_dict(),
    }
    if extra:
        payload.update(extra)
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return ArtifactBundle(
        json_path=json_path,
        html_path=html_path,
        screenshot_path=screenshot_path,
        payload=payload,
    )
