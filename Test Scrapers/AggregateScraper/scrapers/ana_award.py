"""ANA partner award chart scraper."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from scrapers.base import ScrapedRow, parse_miles_text
from scrapers.exceptions import SelectorMissError
from scrapers.http_util import fetch_html

log = logging.getLogger(__name__)


def parse_ana_chart(html: str, url: str, source_name: str) -> list[ScrapedRow]:
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    if not tables:
        raise SelectorMissError(f"ANA chart not found at {url}")

    rows: list[ScrapedRow] = []
    now = datetime.now(timezone.utc)

    for table in tables:
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) < 3:
                continue
            origin = cells[0].strip()
            dest = cells[1].strip() if len(cells) > 1 else ""
            if not origin or origin.lower() in ("from", "zone"):
                continue
            if not dest:
                dest = origin

            economy = parse_miles_text(cells[2]) if len(cells) > 2 else None
            business = parse_miles_text(cells[3]) if len(cells) > 3 else None
            first = parse_miles_text(cells[4]) if len(cells) > 4 else None

            if economy is None and business is None:
                continue

            rows.append(
                ScrapedRow(
                    source_name=source_name,
                    source_url=url,
                    scraped_at=now,
                    from_program="ana_mileage",
                    to_program="united",
                    origin_zone=origin,
                    destination_zone=dest,
                    economy_miles=economy,
                    business_miles=business,
                    first_miles=first,
                    raw_cell_text=cells[2] if len(cells) > 2 else "",
                    selector_matched=True,
                )
            )

    if not rows:
        raise SelectorMissError(f"no ANA rows parsed at {url}")
    return rows


async def scrape_ana(config: dict) -> list[ScrapedRow]:
    block_sigs = config.get("block_signatures", [])
    chart_cfg = config.get("award_charts", {}).get("ana_mileage", {})
    all_rows: list[ScrapedRow] = []
    for src in chart_cfg.get("sources", []):
        if src["name"] != "ana.co.jp":
            continue
        try:
            html = await fetch_html(src["url"], block_signatures=block_sigs)
            all_rows.extend(parse_ana_chart(html, src["url"], src["name"]))
        except Exception as exc:
            log.warning("ANA primary %s failed: %s", src["name"], exc)
    return all_rows
