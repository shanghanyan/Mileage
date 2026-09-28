"""Tier 3 — Firefox via Tor SOCKS, Xvfb (experimental)."""

from __future__ import annotations

import os

from selenium import webdriver
from selenium.webdriver.firefox.options import Options as FirefoxOptions

from scrapers.base_scraper import ScrapeResult, capture_page
from scrapers.contracts import SiteContract
from scrapers.fingerprint import VmProfile, load_vm_profile
from scrapers.session import make_temp_profile

# System tor on Ubuntu listens on 9050. Override with TOR_SOCKS if using Tor Browser (9150).
DEFAULT_TOR_SOCKS = "127.0.0.1:9050"


def tor_socks() -> tuple[str, int]:
    raw = os.environ.get("TOR_SOCKS", DEFAULT_TOR_SOCKS)
    host, _, port = raw.partition(":")
    return host or "127.0.0.1", int(port or "9050")


def _firefox_options(profile_dir, vm: VmProfile) -> FirefoxOptions:
    host, port = tor_socks()
    options = FirefoxOptions()
    options.page_load_strategy = "eager"
    options.add_argument("-profile")
    options.add_argument(str(profile_dir))
    options.set_preference("intl.accept_languages", vm.locale)
    options.set_preference("browser.cache.disk.enable", False)
    options.set_preference("network.proxy.type", 1)
    options.set_preference("network.proxy.socks", host)
    options.set_preference("network.proxy.socks_port", port)
    options.set_preference("network.proxy.socks_remote_dns", True)
    options.set_preference("network.proxy.socks_version", 5)
    options.set_preference("network.proxy.http", "")
    options.set_preference("network.proxy.ssl", "")
    return options


def build_tor_driver(profile_dir, vm: VmProfile | None = None):
    vm = vm or load_vm_profile()
    options = _firefox_options(profile_dir, vm)
    driver = webdriver.Firefox(options=options)
    driver.set_window_size(vm.screen_width, vm.screen_height)
    return driver


def scrape_tor(contract: SiteContract, rate_limit: bool = True) -> ScrapeResult:
    vm = load_vm_profile()
    profile_dir = make_temp_profile("tor-ff-")
    driver = build_tor_driver(profile_dir, vm)
    return capture_page(
        driver,
        contract,
        tier=3,
        browser="firefox-tor",
        proxy_type="tor",
        profile_dir=profile_dir,
        vm_profile=vm,
        rate_limit=rate_limit,
    )
