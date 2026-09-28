import os
from datetime import datetime

from playwright.async_api import async_playwright

from scrapers.base_scraper import (
    BaseScraper,
    launch_stealth_browser,
    new_stealth_page,
    goto_resilient,
    wait_for_selector_safe,
)
from scrapers import scraper_config
from scrapers.vision_scraper import VisionScraper, LIFEMILES_PROMPT
from optimizer.models import ScraperMethod, TransferEdge, Currency

LIFEMILES_REFERENCE_PRICES = {
    ("US", "US"):       {"economy": 200,  "business": 500},
    ("US", "Canada"):   {"economy": 250,  "business": 600},
    ("US", "Mexico"):   {"economy": 250,  "business": 600},
    ("US", "Europe"):   {"economy": 900,  "business": 3500},
    ("US", "Asia"):     {"economy": 1100, "business": 4500},
    ("US", "S.America"): {"economy": 700, "business": 2500},
}

ECONOMY_MILES_MIN = 5000
ECONOMY_MILES_MAX = 100000


class LifeMilesScraper(BaseScraper):
    def __init__(self) -> None:
        cfg = scraper_config("lifemiles")
        self.name = "lifemiles_scraper"
        self.url = cfg["url"]
        self.method = ScraperMethod.VISION
        self._cfg = cfg
        super().__init__()

    async def _fetch(self) -> dict:
        vision = VisionScraper()
        if not vision.is_available():
            raise RuntimeError("Ollama unavailable")

        headless = os.getenv("HEADLESS", "true").lower() == "true"
        timeout_ms = self._cfg.get("timeout_seconds", 45) * 1000
        crop = tuple(self._cfg.get("screenshot_crop", [0, 250, 1920, 1200]))

        async with async_playwright() as p:
            browser = await launch_stealth_browser(p, headless=headless)
            page = await new_stealth_page(browser)
            try:
                await goto_resilient(page, self.url, timeout_ms=timeout_ms, logger=self.logger)
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await wait_for_selector_safe(
                    page, self._cfg.get("wait_selector", "table"),
                    timeout_ms=min(timeout_ms, 15000), logger=self.logger
                )
                await page.wait_for_timeout(600)
                data = await vision.extract_table(page, crop, LIFEMILES_PROMPT, self.name)
                rows = data.get("rows", [])
                for row in rows:
                    eco = row.get("economy_miles")
                    if eco is not None and not (ECONOMY_MILES_MIN <= eco <= ECONOMY_MILES_MAX):
                        row["_suspicious"] = True
                return {"rows": rows}
            finally:
                await browser.close()

    def to_edges(self, data: dict) -> list[TransferEdge]:
        edges: list[TransferEdge] = []
        now = datetime.utcnow()
        seen_transfer: set[tuple] = set()

        transfer_key = (Currency.C1_MILES, Currency.LIFEMILES)
        if transfer_key not in seen_transfer:
            edges.append(TransferEdge(
                from_currency=Currency.C1_MILES,
                to_currency=Currency.LIFEMILES,
                ratio=1.0,
                edge_cpp=1.0,
                label="C1 Miles → Avianca LifeMiles (1:1)",
                source_name="capital_one_scraper",
                source_url=self.url,
                scraped_at=now,
            ))
            seen_transfer.add(transfer_key)

        for row in data.get("rows", []):
            origin = row.get("origin_zone", "US")
            dest = row.get("destination_zone", "US")
            miles = row.get("economy_miles")
            if not miles:
                continue
            prices = LIFEMILES_REFERENCE_PRICES.get(
                (origin, dest),
                LIFEMILES_REFERENCE_PRICES.get(("US", "US"), {"economy": 200}),
            )
            cash = prices["economy"]
            cash_cents = cash * 100
            edge_cpp = cash_cents / miles
            suspicious = row.get("_suspicious", False) or not (
                ECONOMY_MILES_MIN <= miles <= ECONOMY_MILES_MAX
            )
            edges.append(TransferEdge(
                from_currency=Currency.LIFEMILES,
                to_currency=Currency.USD,
                ratio=edge_cpp,
                edge_cpp=edge_cpp,
                label=(
                    f"LifeMiles {origin}→{dest} economy "
                    f"({miles:,} miles, ~${cash} est.)"
                ),
                source_name=self.name,
                source_url=self.url,
                scraped_at=now,
                suspicious=suspicious,
            ))
        return edges
