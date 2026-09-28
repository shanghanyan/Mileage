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
from optimizer.models import ScraperMethod, TransferEdge, Currency

AEROPLAN_FALLBACK_BANDS = [
    ("Under 500 miles", 6000, 15000, 150),
    ("500–1,500 miles", 12500, 25000, 250),
    ("1,500–2,750 miles", 20000, 40000, 400),
    ("2,750–4,000 miles", 30000, 55000, 550),
    ("4,000–5,500 miles", 40000, 70000, 700),
    ("5,500–7,000 miles", 45000, 80000, 900),
    ("7,000+ miles", 60000, 105000, 1200),
]


class AeroplanScraper(BaseScraper):
    def __init__(self) -> None:
        cfg = scraper_config("aeroplan")
        self.name = "aeroplan_scraper"
        self.url = cfg["url"]
        self.method = ScraperMethod.PLAYWRIGHT_BS4
        self._cfg = cfg
        super().__init__()

    async def _fetch(self) -> dict:
        headless = os.getenv("HEADLESS", "true").lower() == "true"
        timeout_ms = self._cfg.get("timeout_seconds", 35) * 1000

        async with async_playwright() as p:
            browser = await launch_stealth_browser(p, headless=headless)
            page = await new_stealth_page(browser)
            try:
                await goto_resilient(page, self.url, timeout_ms=timeout_ms, logger=self.logger)
                await wait_for_selector_safe(
                    page, self._cfg.get("wait_selector", "table"),
                    timeout_ms=min(timeout_ms, 15000), logger=self.logger
                )
                await page.wait_for_timeout(600)
                html = await page.content()
                bands = _parse_aeroplan_bands(html)
                from_fallback = not bands
                if from_fallback:
                    bands = [
                        {
                            "band": label,
                            "economy_miles": eco,
                            "business_miles": biz,
                            "reference_cash_usd": cash,
                        }
                        for label, eco, biz, cash in AEROPLAN_FALLBACK_BANDS
                    ]
                return {"bands": bands, "from_fallback": from_fallback}
            finally:
                await browser.close()

    def to_edges(self, data: dict) -> list[TransferEdge]:
        edges: list[TransferEdge] = []
        now = datetime.utcnow()

        edges.append(TransferEdge(
            from_currency=Currency.C1_MILES,
            to_currency=Currency.AEROPLAN,
            ratio=1.0,
            edge_cpp=1.0,
            label="C1 Miles → Air Canada Aeroplan (1:1)",
            source_name="capital_one_scraper",
            source_url=self.url,
            scraped_at=now,
        ))

        for band in data.get("bands", []):
            miles = band.get("economy_miles")
            cash = band.get("reference_cash_usd", 200)
            if not miles:
                continue
            cash_cents = cash * 100
            edge_cpp = cash_cents / miles
            label = band.get("band", "distance band")
            edges.append(TransferEdge(
                from_currency=Currency.AEROPLAN,
                to_currency=Currency.USD,
                ratio=edge_cpp,
                edge_cpp=edge_cpp,
                label=f"Aeroplan {label} economy ({miles:,} mi, ~${cash} est.)",
                source_name=self.name,
                source_url=self.url,
                scraped_at=now,
                stale=data.get("from_fallback", False),
            ))
        return edges


def _parse_aeroplan_bands(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    bands: list[dict] = []
    cash_map = {b[0]: b[3] for b in AEROPLAN_FALLBACK_BANDS}

    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            nums = [int(re.sub(r"[^\d]", "", x)) for x in cells if re.search(r"\d", x)]
            if len(nums) >= 2:
                label = cells[0]
                bands.append({
                    "band": label,
                    "economy_miles": nums[0],
                    "business_miles": nums[1] if len(nums) > 1 else None,
                    "reference_cash_usd": _estimate_cash(label, cash_map),
                })
    return bands


def _estimate_cash(label: str, cash_map: dict) -> int:
    for key, cash in cash_map.items():
        if key.lower() in label.lower():
            return cash
    return 200
