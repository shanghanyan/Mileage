"""Capital One transfer partner ratios — all 22 partners from partners.yaml."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from graph.partners import get_airline_partners, load_partners_config, partner_by_id
from scrapers.base import ScrapedRow
from scrapers.chart_scraper import load_scrape_targets
from scrapers.exceptions import SelectorMissError
from scrapers.http_util import fetch_html_resilient, is_bot_blocked
from scrapers.scraper_logger import record_attempt, record_failure, record_success

log = logging.getLogger(__name__)

PARTNER_NAME_MAP: dict[str, str] = {
    "avianca lifemiles": "lifemiles",
    "lifemiles": "lifemiles",
    "air canada aeroplan": "aeroplan",
    "aeroplan": "aeroplan",
    "turkish miles & smiles": "turkish_miles",
    "turkish miles and smiles": "turkish_miles",
    "turkish airlines miles&smiles": "turkish_miles",
    "ana mileage club": "ana_mileage",
    "ana": "ana_mileage",
    "singapore krisflyer": "krisflyer",
    "krisflyer": "krisflyer",
    "british airways avios": "avios",
    "avios": "avios",
    "cathay pacific asia miles": "asia_miles",
    "asia miles": "asia_miles",
    "emirates skywards": "emirates",
    "etihad guest": "etihad",
    "eva air infinity mileagelands": "eva_air",
    "eva air": "eva_air",
    "finnair plus": "finnair",
    "finnair": "finnair",
    "japan airlines mileage bank": "jal",
    "jal mileage bank": "jal",
    "jal": "jal",
    "jetblue trueblue": "jetblue",
    "jetblue": "jetblue",
    "qantas frequent flyer": "qantas",
    "qantas": "qantas",
    "qatar airways privilege club": "qatar",
    "qatar privilege club": "qatar",
    "qatar": "qatar",
    "tap air portugal miles&go": "tap",
    "tap miles&go": "tap",
    "tap": "tap",
    "air france-klm flying blue": "flying_blue",
    "flying blue": "flying_blue",
    "aeromexico club premier": "aeromexico",
    "aeromexico": "aeromexico",
    "virgin red": "virgin_red",
}


def _match_program(name: str, known_ids: set[str]) -> str | None:
    key = name.lower().strip()
    if key in PARTNER_NAME_MAP:
        pid = PARTNER_NAME_MAP[key]
        return pid if pid in known_ids else None
    for alias, program in PARTNER_NAME_MAP.items():
        if alias in key and program in known_ids:
            return program
    return None


def _parse_ratio(text: str) -> float | None:
    text = text.strip().lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)", text)
    if m:
        left, right = float(m.group(1)), float(m.group(2))
        if left > 0:
            return right / left
    if re.fullmatch(r"1\s*:\s*1|1:1", text):
        return 1.0
    return None


def parse_transfer_table(
    html: str,
    source_name: str,
    url: str,
    known_ids: set[str],
    *,
    selector: str = "table",
) -> list[ScrapedRow]:
    soup = BeautifulSoup(html, "lxml")
    tables = soup.select(selector) if selector else soup.find_all("table")
    if not tables:
        raise SelectorMissError(f"transfer partner table not found at {url}")

    rows: list[ScrapedRow] = []
    now = datetime.now(timezone.utc)

    for table in tables:
        for tr in table.select("tr"):
            cells = [c.get_text(strip=True) for c in tr.select("td, th")]
            if len(cells) < 2:
                continue
            name = cells[0]
            if name.lower().startswith("partner"):
                continue
            program = _match_program(name, known_ids)
            if program is None:
                continue
            ratio = _parse_ratio(cells[1])
            if ratio is None:
                for cell in cells[1:]:
                    ratio = _parse_ratio(cell)
                    if ratio:
                        break
            if ratio is None:
                log.debug("Skipped ambiguous ratio for %s", name)
                continue
            rows.append(
                ScrapedRow(
                    source_name=source_name,
                    source_url=url,
                    scraped_at=now,
                    from_program="capital_one",
                    to_program=program,
                    transfer_ratio=ratio,
                    raw_cell_text=cells[1],
                    selector_matched=True,
                )
            )

    if not rows:
        raise SelectorMissError(f"no partners parsed at {url}")
    return rows


def seed_from_partners_yaml() -> list[ScrapedRow]:
    """Seed transfer ratios from partners.yaml when scraping fails."""
    partners = get_airline_partners()
    now = datetime.now(timezone.utc)
    rows: list[ScrapedRow] = []
    for p in partners:
        rows.append(
            ScrapedRow(
                source_name="partners.yaml",
                source_url="config/partners.yaml",
                scraped_at=now,
                from_program="capital_one",
                to_program=p["id"],
                transfer_ratio=p["effective_ratio"],
                raw_cell_text=p.get("c1_ratio", "1:1"),
                selector_matched=True,
                confidence="medium",
                flags=["seeded_from_config"],
            )
        )
    return rows


async def scrape_transfer_partners(config: dict) -> list[ScrapedRow]:
    block_sigs = config.get("block_signatures", [])
    partners_cfg = load_partners_config()
    known_ids = {p["id"] for p in partners_cfg.get("airlines", [])}
    all_rows: list[ScrapedRow] = []

    targets = load_scrape_targets().get("c1_transfer_partners", [])
    if not targets:
        tp = config.get("transfer_partners", {})
        if primary := tp.get("primary"):
            targets = [{"url": primary["url"], "parser": "c1_official"}]
        for fb in tp.get("fallbacks", []):
            targets.append({"url": fb["url"], "parser": "c1_official"})

    for target in targets:
        url = target["url"]
        if target.get("last_404") or target.get("bot_blocked"):
            continue
        record_attempt("c1_transfer_partners")
        try:
            fetch_via = target.get("fetch_via")
            html, _, status, snapshot_dt, fetch_method = await fetch_html_resilient(
                url, block_signatures=block_sigs, fetch_via=fetch_via,
            )
            if fetch_method == "live" and is_bot_blocked(html, status):
                record_failure("c1_transfer_partners", url, "bot_block", http_status=status)
                continue
            source_name = (
                f"wayback:{urlparse(url).netloc.lstrip('www.')}"
                if fetch_method in ("wayback", "wayback_only")
                else urlparse(url).netloc.lstrip("www.")
            )
            rows = parse_transfer_table(
                html, source_name, url, known_ids,
                selector=target.get("selector", "table"),
            )
            all_rows.extend(rows)
            record_success("c1_transfer_partners")
            log.info("C1 partners: %d rows from %s", len(rows), url)
        except Exception as exc:
            error_type = "404" if "404" in str(exc) else "unknown"
            record_failure("c1_transfer_partners", url, error_type, response_snippet=str(exc)[:200])
            log.warning("C1 source %s failed: %s", url, exc)

    if not all_rows:
        log.warning("All C1 scrape sources failed — seeding from partners.yaml")
        all_rows = seed_from_partners_yaml()

    return all_rows
