"""Travel blog parsers for award chart data."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from scrapers.ana_pricing import get_ow_miles
from scrapers.base import ScrapedRow, parse_miles_text

log = logging.getLogger(__name__)

CABIN_KEYWORDS = {
    "economy": ("economy", "coach", "y class"),
    "business": ("business", "j class", "premium"),
    "first": ("first", "f class"),
}

REGION_PAIRS = [
    (("north america", "us", "usa", "jfk"), ("japan", "north asia", "tokyo", "nrt", "hnd", "asia")),
    (("north america", "us"), ("europe", "london", "paris")),
]


def _source_name(url: str) -> str:
    return urlparse(url).netloc.lstrip("www.")


def _match_cabin(text: str) -> str | None:
    t = text.lower()
    for cabin, keywords in CABIN_KEYWORDS.items():
        if any(k in t for k in keywords):
            return cabin
    return None


def _match_regions(text: str) -> tuple[str, str] | None:
    t = text.lower()
    for origins, dests in REGION_PAIRS:
        if any(o in t for o in origins) and any(d in t for d in dests):
            return ("North America", "North Asia")
    return None


def parse_blog_table(
    html: str,
    program: str,
    url: str,
    *,
    rt_ow_context: str | None = None,
    source_updated_at: datetime | None = None,
) -> list[ScrapedRow]:
    soup = BeautifulSoup(html, "lxml")
    rows: list[ScrapedRow] = []
    now = datetime.now(timezone.utc)
    source = _source_name(url)

    for table in soup.find_all("table"):
        header_cells = table.find_all("th")
        if not header_cells:
            first = table.find("tr")
            header_cells = first.find_all(["td", "th"]) if first else []

        headers = [h.get_text(" ", strip=True).lower() for h in header_cells]
        cabin_cols: dict[int, str] = {}
        for idx, h in enumerate(headers):
            cabin = _match_cabin(h)
            if cabin:
                cabin_cols[idx] = cabin

        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            row_text = tr.get_text(" ", strip=True)
            regions = _match_regions(row_text)
            origin_zone = cells[0].get_text(" ", strip=True) if cells else ""
            dest_zone = cells[1].get_text(" ", strip=True) if len(cells) > 1 else ""

            if regions:
                origin_zone, dest_zone = regions
            elif not origin_zone or origin_zone.lower() in ("from", "origin", "zone"):
                continue

            economy = business = first = None
            for idx, cell in enumerate(cells):
                text = cell.get_text(" ", strip=True)
                miles = parse_miles_text(text)
                if miles is None:
                    range_m = re.search(r"([\d,]+)\s*[–\-]\s*([\d,]+)", text)
                    if range_m:
                        lo = parse_miles_text(range_m.group(1))
                        hi = parse_miles_text(range_m.group(2))
                        if lo and hi:
                            miles = (lo + hi) // 2

                if miles is None:
                    continue

                cabin = cabin_cols.get(idx) or _match_cabin(row_text)
                if program == "ana_mileage" and cabin:
                    context = row_text + " " + text
                    miles, basis = get_ow_miles(miles, context, rt_ow_context=rt_ow_context)
                    if basis == "UNKNOWN":
                        log.warning("ANA RT/OW unclear for %d miles at %s", miles, url)

                if cabin == "economy":
                    economy = miles
                elif cabin == "business":
                    business = miles
                elif cabin == "first":
                    first = miles
                elif business is None and miles >= 20000:
                    business = miles

            if economy is None and business is None and first is None:
                continue

            rows.append(
                ScrapedRow(
                    source_name=source,
                    source_url=url,
                    scraped_at=now,
                    source_updated_at=source_updated_at,
                    from_program=program,
                    to_program="award",
                    origin_zone=origin_zone,
                    destination_zone=dest_zone,
                    economy_miles=economy,
                    business_miles=business,
                    first_miles=first,
                    raw_cell_text=row_text[:200],
                    selector_matched=True,
                )
            )

    return rows


PROSE_PATTERN = re.compile(
    r"(?:(?:north america|us|jfk).*?(?:japan|asia|tokyo|nrt|north asia)|"
    r"(?:japan|asia|tokyo).*?(?:north america|us|jfk))"
    r".{0,80}?(?:business|economy|first)?"
    r".{0,40}?"
    r"([\d,]+(?:\.\d+)?\s*[kK]?|[\d,]+)"
    r"\s*(?:–|-)\s*"
    r"([\d,]+(?:\.\d+)?\s*[kK]?|[\d,]+)"
    r"\s*miles?",
    re.IGNORECASE | re.DOTALL,
)

PROSE_SINGLE = re.compile(
    r"(?:(?:north america|us|jfk|north asia|japan|tokyo|nrt).*?)"
    r"(?:business|economy|first|class)?"
    r".{0,60}?"
    r"([\d,]+(?:\.\d+)?\s*[kK]?|[\d,]+)\s*miles?",
    re.IGNORECASE,
)


def parse_blog_prose(
    html: str,
    program: str,
    url: str,
    *,
    rt_ow_context: str | None = None,
    source_updated_at: datetime | None = None,
) -> list[ScrapedRow]:
    soup = BeautifulSoup(html, "lxml")
    now = datetime.now(timezone.utc)
    source = _source_name(url)
    rows: list[ScrapedRow] = []

    texts: list[str] = []
    for el in soup.find_all(["p", "li", "h2", "h3"]):
        t = el.get_text(" ", strip=True)
        if len(t) > 20:
            texts.append(t)

    for text in texts:
        regions = _match_regions(text)
        if not regions and program not in ("lifemiles", "turkish_miles"):
            continue

        cabin = _match_cabin(text) or "business"
        miles: int | None = None
        range_note = ""

        if m := PROSE_PATTERN.search(text):
            lo = parse_miles_text(m.group(1))
            hi = parse_miles_text(m.group(2))
            if lo and hi:
                miles = (lo + hi) // 2
                range_note = f"range {lo}-{hi}"
        elif m := PROSE_SINGLE.search(text):
            miles = parse_miles_text(m.group(1))

        if miles is None:
            continue

        origin_zone, dest_zone = regions or ("North America", "North Asia")

        if program == "ana_mileage":
            miles, basis = get_ow_miles(miles, text, rt_ow_context=rt_ow_context)

        flags: list[str] = []
        if range_note:
            flags.append(range_note)

        row = ScrapedRow(
            source_name=source,
            source_url=url,
            scraped_at=now,
            source_updated_at=source_updated_at,
            from_program=program,
            to_program="award",
            origin_zone=origin_zone,
            destination_zone=dest_zone,
            economy_miles=miles if cabin == "economy" else None,
            business_miles=miles if cabin == "business" else None,
            first_miles=miles if cabin == "first" else None,
            raw_cell_text=text[:200],
            selector_matched=True,
            flags=flags,
        )
        rows.append(row)

    return rows
