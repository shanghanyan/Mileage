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
from optimizer.models import ScraperMethod

TPG_URLS = [
    ("tpg", "https://thepointsguy.com/loyalty-programs/capital-one-transfer-partners/"),
    ("tpg_valuations", "https://thepointsguy.com/loyalty-programs/monthly-valuations/"),
]


class TPGScraper(BaseScraper):
    def __init__(self) -> None:
        cfg = scraper_config("tpg")
        self.name = "tpg_scraper"
        self.url = cfg["url"]
        self.method = ScraperMethod.PLAYWRIGHT_BS4
        self._cfg = cfg
        super().__init__()

    async def _fetch(self) -> dict:
        headless = os.getenv("HEADLESS", "true").lower() == "true"
        valuations: dict[str, float] = {}

        async with async_playwright() as p:
            browser = await launch_stealth_browser(p, headless=headless)
            page = await new_stealth_page(browser)
            try:
                for key, url in TPG_URLS:
                    cfg = scraper_config(key) if key != "tpg" else self._cfg
                    timeout_ms = cfg.get("timeout_seconds", 30) * 1000
                    # Scope to main-content tables: a broad "table, article" selector
                    # matched a hidden nav-dropdown <article> first and stalled the
                    # visible-state wait until timeout.
                    wait_sel = cfg.get("wait_selector", "main table, article table")
                    await goto_resilient(page, url, timeout_ms=timeout_ms, logger=self.logger)
                    await wait_for_selector_safe(
                        page, wait_sel, timeout_ms=min(timeout_ms, 15000), logger=self.logger
                    )
                    await page.wait_for_timeout(600)
                    html = await page.content()
                    valuations.update(_extract_cpp_table(html))
            finally:
                await browser.close()

        if not valuations:
            valuations = _default_tpg_valuations()
        return {"tpg_valuations": valuations}

    def to_edges(self, data: dict) -> list:
        return []


def _extract_cpp_table(html: str) -> dict[str, float]:
    soup = BeautifulSoup(html, "lxml")
    result: dict[str, float] = {}
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            cpp = _parse_cpp(cells[-1]) or _parse_cpp(cells[1])
            if cpp is not None:
                key = _normalize_program(cells[0])
                if key:
                    result[key] = cpp
    return result


def _parse_cpp(text: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:¢|cents?|cpp)?", text, re.I)
    return float(m.group(1)) if m else None


def _normalize_program(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _default_tpg_valuations() -> dict[str, float]:
    return {
        "capital_one_miles": 1.7,
        "avianca_lifemiles": 1.5,
        "turkish_miles_smiles": 1.6,
        "singapore_krisflyer": 1.4,
        "air_canada_aeroplan": 1.5,
    }


def sanity_check_tpg(computed_cpp: float, program_key: str, tpg_data: dict) -> None:
    from scrapers.base_scraper import get_logger
    logger = get_logger("tpg_scraper")
    vals = tpg_data.get("tpg_valuations", {})
    estimate = vals.get(program_key)
    if estimate and computed_cpp > estimate * 1.4:
        pct = int((computed_cpp / estimate - 1) * 100)
        logger.warning(
            f"computed CPP {computed_cpp:.2f}¢ is {pct}% above TPG estimate "
            f"{estimate:.2f}¢ for {program_key}"
        )
