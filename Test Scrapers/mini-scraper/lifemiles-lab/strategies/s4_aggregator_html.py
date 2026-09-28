"""S4 — Static aggregator HTML (httpx + BeautifulSoup).

Insight from research: LifeMiles does **not** publish a complete machine-readable
award chart, and its own site is Akamai-protected. But several travel sites
republish the current region-based chart as plain static HTML tables that return
HTTP 200 to a normal request. Parsing those is fast, dependency-light, and
robust — no browser, no vision model, no bot-detection arms race.

We try sources in priority order and use the first that yields a usable chart.
"""

from __future__ import annotations

import httpx
from bs4 import BeautifulSoup

from common import AwardRow, StrategyResult, parse_miles, save_artifact, looks_like_miles
from . import Strategy

# Ordered list of (label, url). First usable hit wins; the rest are fallbacks.
SOURCES = [
    ("awardtravelfinder", "https://awardtravelfinder.com/award-charts/lifemiles"),
    ("10xtravel", "https://10xtravel.com/avianca-lifemiles-award-chart/"),
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Map fuzzy header text -> our canonical column.
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


def _parse_table(table, source: str) -> list[AwardRow]:
    """Parse an HTML table whose header row names From/To/Economy/Business/First."""
    header_cells = table.find_all("th")
    if not header_cells:
        first_row = table.find("tr")
        header_cells = first_row.find_all(["td", "th"]) if first_row else []
    col_map: dict[int, str] = {}
    for idx, th in enumerate(header_cells):
        col = _classify_header(th.get_text(" ", strip=True))
        if col:
            col_map[idx] = col
    # Need at least origin + destination + one cabin column to be a chart table.
    if "origin" not in col_map.values() or "destination" not in col_map.values():
        return []
    if not any(c in col_map.values() for c in ("economy", "business", "first")):
        return []

    rows: list[AwardRow] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if not cells:
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
        eco_lo, _ = parse_miles(values.get("economy"))
        biz_lo, _ = parse_miles(values.get("business"))
        first_lo, _ = parse_miles(values.get("first"))
        row = AwardRow(
            origin=origin,
            destination=dest,
            economy_miles=eco_lo if looks_like_miles(eco_lo) else None,
            business_miles=biz_lo if looks_like_miles(biz_lo) else None,
            first_miles=first_lo if looks_like_miles(first_lo) else None,
            one_way=True,
            source=source,
            raw={k: values.get(k) for k in ("economy", "business", "first")},
        )
        if row.has_any_price():
            rows.append(row)
    return rows


class AggregatorHtmlStrategy(Strategy):
    name = "aggregator_html"
    label = "Static aggregator HTML (httpx + BeautifulSoup)"
    cost = "low"

    async def run(self) -> StrategyResult:
        result = StrategyResult(name=self.name, label=self.label, cost=self.cost)
        timeout = self.ctx.get("http_timeout", 25)

        async with httpx.AsyncClient(
            headers=_HEADERS, timeout=timeout, follow_redirects=True
        ) as client:
            for source, url in SOURCES:
                try:
                    self.logger.info(f"fetching {source} :: {url}")
                    resp = await client.get(url)
                except Exception as exc:  # noqa: BLE001
                    self.logger.warning(f"{source} request failed: {exc}")
                    result.notes.append(f"{source}: request error {exc}")
                    continue

                self.logger.info(
                    f"{source} http_status={resp.status_code} bytes={len(resp.content)}"
                )
                if resp.status_code != 200:
                    result.notes.append(f"{source}: HTTP {resp.status_code}")
                    continue

                art = save_artifact(f"{self.name}_{source}.html", resp.text)
                result.artifacts.append(art)

                soup = BeautifulSoup(resp.text, "lxml")
                best: list[AwardRow] = []
                for table in soup.find_all("table"):
                    parsed = _parse_table(table, source)
                    if len(parsed) > len(best):
                        best = parsed
                self.logger.info(f"{source} parsed_rows={len(best)}")

                if best:
                    result.rows = best
                    result.ok = True
                    # High confidence: a structured, labeled, current chart table.
                    result.confidence = 0.9
                    result.notes.append(
                        f"chart from {source}: {len(best)} routes "
                        f"(low-end of each range)"
                    )
                    return result

        result.error = "no usable chart table found in any aggregator source"
        return result
