import os
import re
from datetime import datetime

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from scrapers.base_scraper import (
    BaseScraper,
    launch_stealth_browser,
    new_stealth_page,
    goto_resilient,
    wait_for_selector_safe,
)
from scrapers import scraper_config
from scrapers.vision_scraper import VisionScraper, KRISFLYER_PROMPT
from optimizer.models import ScraperMethod, TransferEdge, Currency

KRISFLYER_REFERENCE_PRICES = {
    ("US", "US"):         {"economy": 300,  "business": 700},
    ("US", "Europe"):     {"economy": 950,  "business": 4000},
    ("US", "S.E. Asia"):  {"economy": 1100, "business": 5000},
    ("US", "N.E. Asia"):  {"economy": 1200, "business": 5500},
}


class KrisFlyerScraper(BaseScraper):
    def __init__(self) -> None:
        cfg = scraper_config("krisflyer")
        self.name = "krisflyer_scraper"
        self.url = cfg["url"]
        self.method = ScraperMethod.PLAYWRIGHT_BS4
        self._cfg = cfg
        super().__init__()

    async def _fetch(self) -> dict:
        headless = os.getenv("HEADLESS", "true").lower() == "true"
        timeout_ms = self._cfg.get("timeout_seconds", 40) * 1000

        async with async_playwright() as p:
            browser = await launch_stealth_browser(p, headless=headless)
            page = await new_stealth_page(browser)
            try:
                await goto_resilient(page, self.url, timeout_ms=timeout_ms, logger=self.logger)
                await wait_for_selector_safe(
                    page, "table", timeout_ms=min(timeout_ms, 15000), logger=self.logger
                )
                await page.wait_for_timeout(600)
                html = await page.content()
                rows = _parse_krisflyer_table(html)

                if len(rows) < 3:
                    vision = VisionScraper()
                    if vision.is_available():
                        crop = tuple(self._cfg.get("screenshot_crop", [0, 200, 1920, 1200]))
                        data = await vision.extract_table(
                            page, crop, KRISFLYER_PROMPT, self.name
                        )
                        rows = _normalize_vision_rows(data.get("rows", []))
                return {"rows": rows}
            finally:
                await browser.close()

    def to_edges(self, data: dict) -> list[TransferEdge]:
        edges: list[TransferEdge] = []
        now = datetime.utcnow()

        edges.append(TransferEdge(
            from_currency=Currency.C1_MILES,
            to_currency=Currency.KRISFLYER,
            ratio=1.0,
            edge_cpp=1.0,
            label="C1 Miles → Singapore KrisFlyer (1:1)",
            source_name="capital_one_scraper",
            source_url=self.url,
            scraped_at=now,
        ))

        for row in data.get("rows", []):
            origin = row.get("origin_region", row.get("origin", "US"))
            dest = row.get("destination_region", row.get("destination", "Europe"))
            miles = row.get("economy_saver", row.get("economy_miles"))
            if not miles:
                continue
            prices = KRISFLYER_REFERENCE_PRICES.get(
                (origin, dest),
                KRISFLYER_REFERENCE_PRICES.get(("US", "Europe"), {"economy": 950}),
            )
            cash = prices["economy"]
            cash_cents = cash * 100
            edge_cpp = cash_cents / miles
            edges.append(TransferEdge(
                from_currency=Currency.KRISFLYER,
                to_currency=Currency.USD,
                ratio=edge_cpp,
                edge_cpp=edge_cpp,
                label=(
                    f"KrisFlyer {origin}→{dest} saver economy "
                    f"({miles:,} miles, ~${cash} est.)"
                ),
                source_name=self.name,
                source_url=self.url,
                scraped_at=now,
            ))
        return edges


def _parse_krisflyer_table(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    for table in soup.find_all("table"):
        header_text = " ".join(
            th.get_text(strip=True).lower() for th in table.find_all("th")
        )
        if "economy" not in header_text:
            continue
        rows: list[dict] = []
        for tr in table.find_all("tr")[1:]:
            cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) < 3:
                continue
            eco = _parse_int(cells[2] if len(cells) > 2 else "")
            if eco:
                rows.append({
                    "origin_region": cells[0],
                    "destination_region": cells[1],
                    "economy_saver": eco,
                    "business_saver": _parse_int(cells[3]) if len(cells) > 3 else None,
                })
        if rows:
            return rows
    return []


def _normalize_vision_rows(rows: list[dict]) -> list[dict]:
    return [
        {
            "origin_region": r.get("origin_region", "US"),
            "destination_region": r.get("destination_region", "Europe"),
            "economy_saver": r.get("economy_saver"),
            "business_saver": r.get("business_saver"),
        }
        for r in rows
    ]


def _parse_int(text: str) -> int | None:
    m = re.search(r"[\d,]+", text.replace(",", ""))
    return int(m.group()) if m else None
