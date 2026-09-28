"""Tier 1 — static HTTP fetch (no browser)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from scrapers.artifacts import write_artifacts
from scrapers.chart_detection import detect_chart
from scrapers.contracts import SiteContract
from scrapers.fingerprint import VmProfile, direct_proxy_type, load_vm_profile
from scrapers.inspect import inspect_keyword_hits
from scrapers.rate_limit import wait_for_domain
from scrapers.robots import check_robots
from scrapers.validation import ValidationResult, validate_scrape


@dataclass
class Tier1Result:
    validation: ValidationResult
    html: str
    artifact: dict[str, Any]


def fetch_egress_ip(timeout: float = 8.0) -> str:
    try:
        response = httpx.get("https://api.ipify.org", timeout=timeout)
        response.raise_for_status()
        return response.text.strip()
    except Exception:
        return ""


def tier1_http_fetch(
    contract: SiteContract,
    profile: VmProfile | None = None,
    timeout: float = 25.0,
    rate_limit: bool = True,
) -> Tier1Result:
    profile = profile or load_vm_profile()
    robots = check_robots(contract.url)
    if rate_limit:
        wait_for_domain(contract.url)
    headers = {
        "User-Agent": profile.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    http_status = None
    try:
        response = httpx.get(contract.url, headers=headers, timeout=timeout, follow_redirects=True)
        http_status = response.status_code
        html = response.text
        title = ""
        lower = html.lower()
        if "<title" in lower:
            start = lower.find("<title")
            start = lower.find(">", start) + 1
            end = lower.find("</title>", start)
            if end > start:
                title = html[start:end].strip()
        visible = " ".join(BeautifulSoup_text(html).split())
        if not detect_chart(html, contract.chart_keywords).found:
            for extra_url in contract.extra_urls:
                try:
                    extra = httpx.get(extra_url, headers=headers, timeout=timeout, follow_redirects=True)
                    html += "\n" + extra.text
                except Exception:
                    continue
            visible = " ".join(BeautifulSoup_text(html).split())
        validation = validate_scrape(
            contract,
            html=html,
            visible_text=visible,
            title=title,
            final_url=str(response.url),
        )
    except Exception as exc:
        html = ""
        validation = validate_scrape(
            contract,
            html="",
            visible_text="",
            error=f"Tier 1 HTTP error: {exc}",
        )

    inspect_hits = inspect_keyword_hits(html) if html else []
    bundle = write_artifacts(
        contract,
        tier=1,
        validation=validation,
        html=html,
        extra={"robots": robots.to_dict(), "http_status": http_status},
        browser="httpx",
        proxy_type=direct_proxy_type(),
        egress_ip=fetch_egress_ip(),
        machine_id=profile.machine_id,
        timezone_name=profile.timezone,
        inspect_hits=inspect_hits,
    )
    return Tier1Result(validation=validation, html=html, artifact=bundle.payload)


def BeautifulSoup_text(html: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)
