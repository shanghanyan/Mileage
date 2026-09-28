"""Tier 4 login mimicry — reserved; cookies must be captured on the VPS."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from selenium.webdriver.remote.webdriver import WebDriver

from scrapers.base_scraper import ScrapeResult, capture_page
from scrapers.chrome_scraper import build_chrome_driver
from scrapers.contracts import SiteContract
from scrapers.fingerprint import direct_proxy_type, load_vm_profile
from scrapers.session import make_temp_profile


class LoginMimicryDisabled(RuntimeError):
    pass


def session_cookie_path() -> Path | None:
    raw = os.environ.get("SESSION_COOKIE_PATH")
    return Path(raw) if raw else None


def load_cookies(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "cookies" in data:
        data = data["cookies"]
    if not isinstance(data, list):
        raise ValueError("SESSION_COOKIE_PATH must be a JSON list of cookie dicts")
    return data


def apply_cookies(driver: WebDriver, cookies: list[dict[str, Any]], first_url: str) -> None:
    """Open the site origin, then inject cookies captured on the VPS."""
    from urllib.parse import urlparse

    parsed = urlparse(first_url)
    origin = f"{parsed.scheme}://{parsed.netloc}/"
    driver.get(origin)
    for cookie in cookies:
        payload = {k: v for k, v in cookie.items() if k in {"name", "value", "path", "domain", "secure", "httpOnly", "expiry", "sameSite"}}
        payload.setdefault("path", "/")
        try:
            driver.add_cookie(payload)
        except Exception:
            continue


def scrape_with_login(contract: SiteContract, rate_limit: bool = True) -> ScrapeResult:
    path = session_cookie_path()
    if path is None or not path.exists():
        raise LoginMimicryDisabled(
            "Tier 4 requires SESSION_COOKIE_PATH pointing at cookies captured "
            "on the VPS (never imported from a laptop browser)."
        )
    cookies = load_cookies(path)
    vm = load_vm_profile()
    profile_dir = make_temp_profile("login-")
    driver = build_chrome_driver(profile_dir, vm)

    def pre_get(drv: WebDriver) -> None:
        apply_cookies(drv, cookies, contract.url)

    return capture_page(
        driver,
        contract,
        tier=4,
        browser="chrome-login",
        proxy_type=direct_proxy_type(),
        profile_dir=profile_dir,
        vm_profile=vm,
        rate_limit=rate_limit,
        pre_get=pre_get,
    )
