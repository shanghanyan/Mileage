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
from scrapers.vision_scraper import VisionScraper, TURKISH_PROMPT
from optimizer.models import ScraperMethod, TransferEdge, Currency

TURKISH_REFERENCE_PRICES = {
    ("US", "US"):       {"economy": 200,  "business": 450},
    ("US", "Atlantic"): {"economy": 600,  "business": 2800},
    ("US", "Europe"):   {"economy": 800,  "business": 3200},
    ("US", "Asia"):     {"economy": 1000, "business": 4200},
}

DOMESTIC_ECO_MIN = 5000
DOMESTIC_ECO_MAX = 15000


class TurkishScraper(BaseScraper):
    def __init__(self) -> None:
        cfg = scraper_config("turkish")
        self.name = "turkish_scraper"
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
        crop = tuple(self._cfg.get("screenshot_crop", [0, 150, 1920, 1400]))

        # turkishairlines.com rejects the headless h2 handshake with
        # ERR_HTTP2_PROTOCOL_ERROR; forcing HTTP/1.1 sidesteps the block.
        async with async_playwright() as p:
            browser = await launch_stealth_browser(
                p, headless=headless, extra_args=["--disable-http2"]
            )
            page = await new_stealth_page(browser)
            try:
                await goto_resilient(page, self.url, timeout_ms=timeout_ms, logger=self.logger)
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await wait_for_selector_safe(
                    page, self._cfg.get("wait_selector", "table"),
                    timeout_ms=min(timeout_ms, 15000), logger=self.logger
                )
                await page.wait_for_timeout(600)
                return await vision.extract_table(page, crop, TURKISH_PROMPT, self.name)
            finally:
                await browser.close()

    def to_edges(self, data: dict) -> list[TransferEdge]:
        edges: list[TransferEdge] = []
        now = datetime.utcnow()

        edges.append(TransferEdge(
            from_currency=Currency.C1_MILES,
            to_currency=Currency.TURKISH_MILES,
            ratio=1.0,
            edge_cpp=1.0,
            label="C1 Miles → Turkish Miles & Smiles (1:1)",
            source_name="capital_one_scraper",
            source_url=self.url,
            scraped_at=now,
        ))

        for row in data.get("rows", []):
            origin = row.get("region_from", "US")
            dest = row.get("region_to", "US")
            miles = row.get("economy_miles")
            if not miles:
                continue
            prices = TURKISH_REFERENCE_PRICES.get(
                (origin, dest),
                TURKISH_REFERENCE_PRICES.get(("US", "US"), {"economy": 200}),
            )
            cash = prices["economy"]
            cash_cents = cash * 100
            edge_cpp = cash_cents / miles
            suspicious = False
            if origin == "US" and dest == "US":
                if miles < DOMESTIC_ECO_MIN or miles > DOMESTIC_ECO_MAX:
                    if miles >= 8000:
                        suspicious = False
                    else:
                        suspicious = True
            edges.append(TransferEdge(
                from_currency=Currency.TURKISH_MILES,
                to_currency=Currency.USD,
                ratio=edge_cpp,
                edge_cpp=edge_cpp,
                label=(
                    f"Turkish {origin}→{dest} economy "
                    f"({miles:,} miles, ~${cash} est.)"
                ),
                source_name=self.name,
                source_url=self.url,
                scraped_at=now,
                suspicious=suspicious,
            ))
        return edges
