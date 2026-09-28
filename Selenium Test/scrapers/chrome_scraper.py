"""Tier 2 — Google Chrome via Xvfb, VPS public IP."""

from __future__ import annotations

import os
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions

from scrapers.base_scraper import ScrapeResult, capture_page
from scrapers.contracts import SiteContract
from scrapers.fingerprint import VmProfile, direct_proxy_type, load_vm_profile
from scrapers.session import make_temp_profile

# /dev/shm is often 64 MB on VPS images; sandbox fails as root or without user namespaces.
VPS_CHROME_ARGS = (
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
    "--disable-popup-blocking",
    "--disable-gpu",
    "--no-sandbox",
)

_CHROME_BINARIES = (
    "/usr/bin/google-chrome-stable",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
)


def chrome_binary() -> str | None:
    for key in ("CHROME_BIN", "GOOGLE_CHROME_BIN"):
        raw = os.environ.get(key)
        if raw and Path(raw).exists():
            return raw
    for candidate in _CHROME_BINARIES:
        if Path(candidate).exists():
            return candidate
    return None


def _chrome_options(profile_dir, vm: VmProfile) -> ChromeOptions:
    options = ChromeOptions()
    options.page_load_strategy = "eager"
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument(f"--window-size={vm.screen_width},{vm.screen_height}")
    options.add_argument(f"--lang={vm.locale}")
    for arg in VPS_CHROME_ARGS:
        if arg == "--no-sandbox" and os.environ.get("CHROME_NO_SANDBOX", "1") == "0":
            continue
        options.add_argument(arg)
    binary = chrome_binary()
    if binary:
        options.binary_location = binary
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    return options


def build_chrome_driver(profile_dir, vm: VmProfile | None = None):
    vm = vm or load_vm_profile()
    options = _chrome_options(profile_dir, vm)
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(45)
    return driver


def scrape_chrome(contract: SiteContract, rate_limit: bool = True) -> ScrapeResult:
    vm = load_vm_profile()
    profile_dir = make_temp_profile("chrome-")
    driver = build_chrome_driver(profile_dir, vm)
    return capture_page(
        driver,
        contract,
        tier=2,
        browser="chrome",
        proxy_type=direct_proxy_type(),
        profile_dir=profile_dir,
        vm_profile=vm,
        rate_limit=rate_limit,
    )
