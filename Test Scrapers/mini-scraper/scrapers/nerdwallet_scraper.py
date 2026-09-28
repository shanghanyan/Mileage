import os
import re

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
from optimizer.models import ScraperMethod


class NerdWalletScraper(BaseScraper):
    def __init__(self) -> None:
        cfg = scraper_config("nerdwallet")
        self.name = "nerdwallet_scraper"
        self.url = cfg["url"]
        self.method = ScraperMethod.PLAYWRIGHT_BS4
        self._cfg = cfg
        super().__init__()

    async def _fetch(self) -> dict:
        headless = os.getenv("HEADLESS", "true").lower() == "true"
        timeout_ms = self._cfg.get("timeout_seconds", 30) * 1000

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
                valuations, element_type = _extract_nerdwallet(html)
                self.logger.info(f"matched element type: {element_type}")
                if not valuations:
                    valuations = {
                        "statement_credit": 0.5,
                        "travel_portal": 1.0,
                        "transfer_partners": 1.5,
                    }
                return {"nerdwallet_valuations": valuations}
            finally:
                await browser.close()

    def to_edges(self, data: dict) -> list:
        return []


def _extract_nerdwallet(html: str) -> tuple[dict[str, float], str]:
    soup = BeautifulSoup(html, "lxml")
    for element_type, finder in [
        ("table", lambda s: s.find_all("table")),
        ("dl", lambda s: s.find_all("dl")),
        ("li", lambda s: s.find_all("li")),
    ]:
        valuations: dict[str, float] = {}
        for block in finder(soup):
            text = block.get_text("\n", strip=True)
            for line in text.split("\n"):
                cpp = _parse_cpp_line(line)
                if cpp:
                    key = _key_from_line(line)
                    if key:
                        valuations[key] = cpp
        if valuations:
            return valuations, element_type
    return {}, "none"


def _parse_cpp_line(line: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:¢|cents?|cpp)", line, re.I)
    return float(m.group(1)) if m else None


def _key_from_line(line: str) -> str | None:
    lower = line.lower()
    if "statement" in lower:
        return "statement_credit"
    if "travel" in lower or "portal" in lower:
        return "travel_portal"
    if "transfer" in lower:
        return "transfer_partners"
    if "gift" in lower or "amazon" in lower:
        return "gift_cards"
    return re.sub(r"[^a-z0-9]+", "_", lower[:40]).strip("_") or None
