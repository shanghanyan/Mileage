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


class AwardFaresScraper(BaseScraper):
    def __init__(self) -> None:
        cfg = scraper_config("awardfares")
        self.name = "awardfares_scraper"
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
                avg_miles, note = _parse_awardfares(html)
                return {
                    "awardfares_avg_miles": avg_miles,
                    "awardfares_note": note,
                }
            finally:
                await browser.close()

    def to_edges(self, data: dict) -> list:
        return []


def _parse_awardfares(html: str) -> tuple[int | None, str]:
    """Extract an AwardFares headline mileage figure.

    Only returns a value when it is anchored to an explicit qualifier
    (average / from / as low as / starting at). The previous behaviour grabbed
    the first "<n> miles" string on the page — an unanchored, route-agnostic
    aggregate — which then triggered spurious >40% divergence warnings against
    specific long-haul chart paths. When no qualified figure is found we return
    None so the divergence check is skipped rather than fed misleading data.
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)

    qualified = re.search(
        r"(?:average|avg|from|as low as|starting at|cheapest)[:\s]+"
        r"([\d,]{4,6})\s*miles",
        text,
        re.I,
    )
    if qualified:
        val = int(qualified.group(1).replace(",", ""))
        return val, "parsed qualified figure (average/from) from public summary"

    return (
        None,
        "no route-scoped figure found (aggregate page data not comparable; "
        "login likely required for detailed AwardFares data)",
    )
