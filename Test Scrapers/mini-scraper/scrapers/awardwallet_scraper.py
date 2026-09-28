import os
from datetime import datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from scrapers import scraper_config
from optimizer.models import ScraperMethod

DEVALUATION_KEYWORDS = [
    "devaluation", "award chart", "miles increase", "points increase",
    "award change", "United", "LifeMiles", "Turkish Miles", "Capital One",
    "KrisFlyer", "Aeroplan", "miles and smiles",
]

PROGRAM_KEYWORDS = {
    "lifemiles": "lifemiles_scraper",
    "avianca": "lifemiles_scraper",
    "turkish": "turkish_scraper",
    "miles and smiles": "turkish_scraper",
    "krisflyer": "krisflyer_scraper",
    "singapore": "krisflyer_scraper",
    "aeroplan": "aeroplan_scraper",
    "air canada": "aeroplan_scraper",
    "capital one": "capital_one_scraper",
    "united": "lifemiles_scraper",
}


class AwardWalletScraper(BaseScraper):
    def __init__(self) -> None:
        cfg = scraper_config("awardwallet")
        self.name = "awardwallet_scraper"
        self.url = cfg["url"]
        self.method = ScraperMethod.HTTPX_BS4
        self._cfg = cfg
        super().__init__()

    async def _fetch(self) -> dict:
        timeout = self._cfg.get("timeout_seconds", 20)
        headers = {"User-Agent": os.getenv("USER_AGENT", "")}
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(self.url, headers=headers)
            resp.raise_for_status()
            return {"articles": _parse_articles(resp.text, self.url)}

    def to_edges(self, data: dict) -> list:
        return []


def _parse_articles(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    articles: list[dict] = []

    for article in soup.select("article, .post, .news-item, li")[:30]:
        headline_el = article.find(["h1", "h2", "h3", "a"])
        if not headline_el:
            continue
        headline = headline_el.get_text(strip=True)
        if len(headline) < 10:
            continue
        link = headline_el.get("href") if headline_el.name == "a" else None
        if not link:
            a = article.find("a", href=True)
            link = a["href"] if a else base_url
        url = urljoin(base_url, link)
        date_el = article.find("time")
        date = date_el.get_text(strip=True) if date_el else ""
        snippet = article.get_text(" ", strip=True)[:300]
        combined = f"{headline} {snippet}".lower()
        is_alert = any(kw.lower() in combined for kw in DEVALUATION_KEYWORDS)
        programs = [
            prog for kw, prog in PROGRAM_KEYWORDS.items() if kw in combined
        ]
        programs = list(dict.fromkeys(programs))
        articles.append({
            "headline": headline,
            "url": url,
            "date": date,
            "programs": programs,
            "is_alert": is_alert,
        })
        if len(articles) >= 10:
            break
    return articles
