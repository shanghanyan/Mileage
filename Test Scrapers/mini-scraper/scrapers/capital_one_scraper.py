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

PARTNER_CURRENCY_MAP = {
    "avianca lifemiles": Currency.LIFEMILES,
    "lifemiles": Currency.LIFEMILES,
    "turkish miles & smiles": Currency.TURKISH_MILES,
    "turkish miles and smiles": Currency.TURKISH_MILES,
    "singapore krisflyer": Currency.KRISFLYER,
    "krisflyer": Currency.KRISFLYER,
    "air canada aeroplan": Currency.AEROPLAN,
    "aeroplan": Currency.AEROPLAN,
}

KNOWN_PARTNERS = [
    ("Avianca LifeMiles", 1.0),
    ("Turkish Miles & Smiles", 1.0),
    ("Singapore KrisFlyer", 1.0),
    ("Air Canada Aeroplan", 1.0),
    ("Air France/KLM Flying Blue", 1.0),
    ("British Airways Avios", 1.0),
    ("Cathay Pacific", 1.0),
    ("Emirates Skywards", 0.75),
    ("EVA Air", 0.75),
    ("Japan Airlines", 0.75),
    ("JetBlue", 0.6),
    ("Accor", 0.5),
    ("Wyndham", 1.0),
    ("Choice Hotels", 1.0),
]


class CapitalOneScraper(BaseScraper):
    def __init__(self) -> None:
        cfg = scraper_config("capital_one")
        self.name = "capital_one_scraper"
        self.url = cfg["url"]
        self.method = ScraperMethod.PLAYWRIGHT
        self._cfg = cfg
        super().__init__()

    async def _fetch(self) -> dict:
        headless = os.getenv("HEADLESS", "true").lower() == "true"
        timeout_ms = self._cfg.get("timeout_seconds", 30) * 1000
        wait_sel = self._cfg.get("wait_selector", "table")

        async with async_playwright() as p:
            browser = await launch_stealth_browser(p, headless=headless)
            page = await new_stealth_page(browser)
            try:
                await goto_resilient(page, self.url, timeout_ms=timeout_ms, logger=self.logger)
                await wait_for_selector_safe(
                    page, wait_sel, timeout_ms=min(timeout_ms, 15000), logger=self.logger
                )
                await page.wait_for_timeout(600)
                html = await page.content()
                partners = _parse_partners_html(html)

                if not partners:
                    from scrapers.vision_scraper import VisionScraper
                    vision = VisionScraper()
                    if vision.is_available():
                        crop = tuple(self._cfg.get("screenshot_crop", [0, 0, 1920, 1080]))
                        parsed = await vision.extract_table(
                            page, crop,
                            "Extract Capital One transfer partner names and ratios as JSON. "
                            'Schema: {"partners": [{"name": "string", "ratio": number}]}',
                            self.name,
                        )
                        partners = parsed.get("partners", [])

                if not partners:
                    partners = [{"name": n, "ratio": r} for n, r in KNOWN_PARTNERS]
                return {"partners": partners}
            finally:
                await browser.close()

    def to_edges(self, data: dict) -> list[TransferEdge]:
        edges: list[TransferEdge] = []
        now = datetime.utcnow()
        for partner in data.get("partners", []):
            name = partner.get("name", "")
            ratio = float(partner.get("ratio", 1.0))
            key = name.lower().strip()
            currency = PARTNER_CURRENCY_MAP.get(key)
            if not currency:
                for alias, cur in PARTNER_CURRENCY_MAP.items():
                    if alias in key:
                        currency = cur
                        break
            if currency is None:
                self.logger.info(f"skipping {name}, not in Currency enum")
                continue
            if "united" in key and "mileageplus" in key:
                self.logger.warning(
                    f"WARNING: {name} suggests direct C1→United — not adding edge"
                )
                continue
            edges.append(TransferEdge(
                from_currency=Currency.C1_MILES,
                to_currency=currency,
                ratio=ratio,
                edge_cpp=ratio,
                label=f"C1 Miles → {name} ({ratio:.2f}:1)",
                source_name=self.name,
                source_url=self.url,
                scraped_at=now,
            ))
        return edges


def _parse_partners_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    partners: list[dict] = []
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            name = cells[0]
            ratio = _parse_ratio(cells[1])
            if name and ratio and not name.lower().startswith("partner"):
                partners.append({"name": name, "ratio": ratio})
    if partners:
        return partners

    text = soup.get_text(" ", strip=True).lower()
    for display, ratio in KNOWN_PARTNERS:
        if display.lower() in text:
            partners.append({"name": display, "ratio": ratio})
    return partners


def _parse_ratio(text: str) -> float | None:
    text = text.strip().lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)", text)
    if m:
        left, right = float(m.group(1)), float(m.group(2))
        if left > 0:
            return right / left
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    if m:
        val = float(m.group(1))
        if val <= 2:
            return val
    return None
