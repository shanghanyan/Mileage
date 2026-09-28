"""Per-site scrape contracts and alias → canonical URL mapping."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REDIRECTS_PATH = PROJECT_ROOT / "redirects.yaml"


@dataclass(frozen=True)
class SiteContract:
    key: str
    url: str
    alias: str
    locale: str
    wait_selectors: list[str]
    js_settle_seconds: float
    chart_keywords: list[str]
    content_keywords: list[str]
    block_title_fragments: list[str]
    cookie_button_texts: list[str]
    scroll_to_bottom: bool
    min_page_text_length: int
    login_markers: list[str] = field(default_factory=list)
    region_fail_fragments: list[str] = field(default_factory=list)
    extra_urls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "url": self.url,
            "alias": self.alias,
            "locale": self.locale,
            "wait_selectors": list(self.wait_selectors),
            "js_settle_seconds": self.js_settle_seconds,
            "chart_keywords": list(self.chart_keywords),
            "content_keywords": list(self.content_keywords),
            "scroll_to_bottom": self.scroll_to_bottom,
            "min_page_text_length": self.min_page_text_length,
        }


_DEFAULT_COOKIE_BUTTONS = [
    "Accept",
    "Accept All",
    "Accept all",
    "Accept all cookies",
    "Agree",
    "I Agree",
    "Allow all",
    "Allow All",
    "Got it",
    "OK",
]

_DEFAULT_BLOCK_TITLES = [
    "Access Denied",
    "Access denied",
    "Forbidden",
    "Attention Required",
    "Just a moment",
    "Pardon Our Interruption",
    "Unusual traffic",
    "Robot or human",
]

_DEFAULT_LOGIN_MARKERS = [
    "sign in",
    "log in",
    "login required",
    "please log in",
    "create an account",
    "member login",
]


def load_redirects(path: Path | None = None) -> dict[str, dict[str, str]]:
    redirects_file = path or REDIRECTS_PATH
    if not redirects_file.exists():
        return {}
    data = yaml.safe_load(redirects_file.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"redirects.yaml must be a mapping, got {type(data)}")
    return data


def resolve_url(alias_or_key: str, redirects: dict[str, dict[str, str]] | None = None) -> str:
    """Map contract key or scrape:// alias to the canonical URL."""
    mapping = redirects if redirects is not None else load_redirects()
    if alias_or_key in mapping:
        return mapping[alias_or_key]["canonical"]
    for key, entry in mapping.items():
        if entry.get("alias") == alias_or_key or entry.get("canonical") == alias_or_key:
            return entry["canonical"]
    if alias_or_key in SITE_CONTRACTS:
        return SITE_CONTRACTS[alias_or_key].url
    raise KeyError(f"Unknown site alias or key: {alias_or_key}")


def alias_for_key(key: str, redirects: dict[str, dict[str, str]] | None = None) -> str:
    mapping = redirects if redirects is not None else load_redirects()
    entry = mapping.get(key) or {}
    return entry.get("alias") or f"scrape://{key}"


SITE_CONTRACTS: dict[str, SiteContract] = {
    "capitalone_venture_partners": SiteContract(
        key="capitalone_venture_partners",
        url="https://www.capitalone.com/learn-grow/money-management/venture-miles-transfer-partnerships/",
        alias="scrape://capitalone/venture-partners",
        locale="en-US",
        wait_selectors=["shared-article-body", "shared-article-detail", "h1", "main", "article", "table"],
        js_settle_seconds=3.0,
        chart_keywords=["transfer", "conversion", "mile", "point", "ratio", "award"],
        content_keywords=["venture", "miles", "transfer", "partner", "capital one"],
        block_title_fragments=list(_DEFAULT_BLOCK_TITLES),
        cookie_button_texts=list(_DEFAULT_COOKIE_BUTTONS)
        + ["Allow all cookies", "Accept All Cookies"],
        scroll_to_bottom=True,
        min_page_text_length=800,
        login_markers=list(_DEFAULT_LOGIN_MARKERS),
        region_fail_fragments=["not available in your region"],
    ),
    "cathay_mega_miles_2026": SiteContract(
        key="cathay_mega_miles_2026",
        url="https://www.cathaypacific.com/cx/en_US/offers/Mega-Miles-2026.html",
        alias="scrape://cathay/mega-miles-2026",
        locale="en-US",
        wait_selectors=["h1", "h2", ".c-accordion", ".cmp-pillarBlock", "main", ".offer", "table"],
        js_settle_seconds=4.0,
        chart_keywords=["mile", "point", "conversion", "earn", "award", "bonus"],
        content_keywords=["cathay", "mega", "miles", "asia miles", "offer"],
        block_title_fragments=list(_DEFAULT_BLOCK_TITLES),
        cookie_button_texts=list(_DEFAULT_COOKIE_BUTTONS)
        + ["Accept Cookies", "Accept All Cookies", "Allow all cookies"],
        scroll_to_bottom=True,
        min_page_text_length=600,
        login_markers=list(_DEFAULT_LOGIN_MARKERS),
        region_fail_fragments=["select your country", "choose your region"],
        extra_urls=[
            "https://www.cathaypacific.com/cx/en_US/our-partners.html?LOCATION=US&PILLAR=holidays&CXEXCLUSIVE=MILESCONVERSIONPARTNERS",
        ],
    ),
    "jal_partner_point": SiteContract(
        key="jal_partner_point",
        url="https://www.jal.co.jp/arl/en/sr/jalmile/partner/point/",
        alias="scrape://jal/partner-point",
        locale="en-US",
        wait_selectors=["#wrapper", "#contents", "dl", "main", "table"],
        js_settle_seconds=4.0,
        chart_keywords=["point", "mile", "conversion", "partner", "award", "ratio"],
        content_keywords=["jal", "mileage", "partner", "point", "award"],
        block_title_fragments=list(_DEFAULT_BLOCK_TITLES),
        cookie_button_texts=list(_DEFAULT_COOKIE_BUTTONS) + ["Allow all cookies"],
        scroll_to_bottom=True,
        min_page_text_length=500,
        login_markers=list(_DEFAULT_LOGIN_MARKERS),
        region_fail_fragments=["/jp/", "日本語"],
    ),
}


def get_contract(key: str) -> SiteContract:
    if key not in SITE_CONTRACTS:
        raise KeyError(f"Unknown site contract: {key}. Known: {sorted(SITE_CONTRACTS)}")
    return SITE_CONTRACTS[key]
