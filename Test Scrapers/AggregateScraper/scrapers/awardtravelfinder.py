"""Multi-program zone chart scraper for aggregator sites."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from scrapers.base import ScrapedRow, parse_miles_text
from scrapers.exceptions import SelectorMissError
from scrapers.http_util import fetch_html

log = logging.getLogger(__name__)

_HEADER_ALIASES = {
    "from": "origin",
    "origin": "origin",
    "to": "destination",
    "destination": "destination",
    "economy": "economy",
    "econ": "economy",
    "business": "business",
    "first": "first",
}


def _classify_header(text: str) -> str | None:
    t = text.strip().lower()
    for key, col in _HEADER_ALIASES.items():
        if key in t:
            return col
    return None


def parse_zone_table(
    html: str,
    program: str,
    url: str,
    source_name: str,
    *,
    table_selector: str = "table",
) -> list[ScrapedRow]:
    soup = BeautifulSoup(html, "lxml")
    tables = soup.select(table_selector)
    if not tables:
        raise SelectorMissError(f"award-chart table not found for {program} at {url}")

    best: list[ScrapedRow] = []
    now = datetime.now(timezone.utc)

    for table in tables:
        header_cells = table.find_all("th")
        if not header_cells:
            first_row = table.find("tr")
            header_cells = first_row.find_all(["td", "th"]) if first_row else []

        col_map: dict[int, str] = {}
        for idx, th in enumerate(header_cells):
            col = _classify_header(th.get_text(" ", strip=True))
            if col:
                col_map[idx] = col

        if "origin" not in col_map.values() or "destination" not in col_map.values():
            continue
        if not any(c in col_map.values() for c in ("economy", "business", "first")):
            continue

        parsed: list[ScrapedRow] = []
        for tr in table.select("tbody tr") or table.find_all("tr"):
            cells = tr.select("td")
            if len(cells) < 3:
                continue

            values: dict[str, str] = {}
            for idx, cell in enumerate(cells):
                col = col_map.get(idx)
                if col:
                    values[col] = cell.get_text(" ", strip=True)

            origin = values.get("origin", "").strip()
            dest = values.get("destination", "").strip()
            if not origin or not dest:
                continue

            economy_raw = values.get("economy", "")
            business_raw = values.get("business", "")
            first_raw = values.get("first", "")

            economy = parse_miles_text(economy_raw) if economy_raw else None
            business = parse_miles_text(business_raw) if business_raw else None
            first = parse_miles_text(first_raw) if first_raw else None

            if economy is None and business is None and first is None:
                log.warning("Skipped ambiguous row %s→%s in %s", origin, dest, program)
                continue

            parsed.append(
                ScrapedRow(
                    source_name=source_name,
                    source_url=url,
                    scraped_at=now,
                    from_program=program,
                    to_program="united",
                    origin_zone=origin,
                    destination_zone=dest,
                    economy_miles=economy,
                    business_miles=business,
                    first_miles=first,
                    raw_cell_text=economy_raw or business_raw or first_raw,
                    selector_matched=True,
                )
            )

        if len(parsed) > len(best):
            best = parsed

    if not best:
        raise SelectorMissError(f"no usable rows in chart for {program} at {url}")
    return best


async def scrape_program_chart(
    program: str,
    chart_config: dict,
    block_signatures: list[str],
) -> list[ScrapedRow]:
    """Try sources in order; return rows from all successful fetches for cross-check."""
    all_rows: list[ScrapedRow] = []
    for src in chart_config.get("sources", []):
        try:
            html = await fetch_html(src["url"], block_signatures=block_signatures)
            rows = parse_zone_table(
                html,
                program,
                src["url"],
                src["name"],
                table_selector=src.get("table_selector", "table"),
            )
            all_rows.extend(rows)
            log.info("%s: %d rows from %s", program, len(rows), src["name"])
        except Exception as exc:
            log.warning("%s source %s failed: %s", program, src["name"], exc)
    return all_rows


async def scrape_all_charts(config: dict) -> dict[str, list[ScrapedRow]]:
    block_sigs = config.get("block_signatures", [])
    results: dict[str, list[ScrapedRow]] = {}
    for key, chart_cfg in config.get("award_charts", {}).items():
        program = chart_cfg["program"]
        results[program] = await scrape_program_chart(program, chart_cfg, block_sigs)
    return results
