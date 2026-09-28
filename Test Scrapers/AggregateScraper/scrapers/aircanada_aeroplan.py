"""Air Canada Aeroplan distance-based partner chart scraper."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from scrapers.base import ScrapedRow, parse_miles_text
from scrapers.exceptions import SelectorMissError
from scrapers.http_util import fetch_html

log = logging.getLogger(__name__)


def parse_aeroplan_chart(html: str, url: str, source_name: str) -> list[ScrapedRow]:
    """Parse Aeroplan partner award tables (zone or distance bands)."""
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    if not tables:
        raise SelectorMissError(f"Aeroplan chart not found at {url}")

    rows: list[ScrapedRow] = []
    now = datetime.now(timezone.utc)

    for table in tables:
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) < 3:
                continue

            label = cells[0]
            if not label or label.lower() in ("from", "to", "zone"):
                continue

            economy = None
            business = None
            for cell in cells[1:]:
                miles = parse_miles_text(cell)
                if miles is None:
                    m = re.search(r"([\d,]+)", cell.replace(",", ""))
                    if m:
                        miles = int(m.group(1).replace(",", ""))
                if miles is None:
                    continue
                if economy is None:
                    economy = miles
                elif business is None:
                    business = miles

            if economy is None:
                continue

            rows.append(
                ScrapedRow(
                    source_name=source_name,
                    source_url=url,
                    scraped_at=now,
                    from_program="aeroplan",
                    to_program="united",
                    origin_zone=label,
                    destination_zone=label,
                    economy_miles=economy,
                    business_miles=business,
                    raw_cell_text=cells[1] if len(cells) > 1 else "",
                    selector_matched=True,
                )
            )

    if not rows:
        raise SelectorMissError(f"no Aeroplan rows parsed at {url}")
    return rows


async def scrape_aeroplan(config: dict) -> list[ScrapedRow]:
    block_sigs = config.get("block_signatures", [])
    chart_cfg = config.get("award_charts", {}).get("aeroplan", {})
    all_rows: list[ScrapedRow] = []
    for src in chart_cfg.get("sources", []):
        if src["name"] != "aircanada.com":
            continue
        try:
            html = await fetch_html(src["url"], block_signatures=block_sigs)
            all_rows.extend(parse_aeroplan_chart(html, src["url"], src["name"]))
        except Exception as exc:
            log.warning("Aeroplan primary %s failed: %s", src["name"], exc)
    return all_rows
