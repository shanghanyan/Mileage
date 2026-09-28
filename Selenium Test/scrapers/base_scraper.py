"""Shared Selenium capture: cookies, waits, scroll, chart xpath, wipe."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait

from scrapers.artifacts import ArtifactBundle, write_artifacts
from scrapers.bot_detection import detect_bot_block
from scrapers.chart_detection import ChartResult, detect_chart
from scrapers.contracts import SiteContract
from scrapers.fingerprint import VmProfile, load_vm_profile
from scrapers.http_fetch import fetch_egress_ip
from scrapers.inspect import inspect_keyword_hits
from scrapers.rate_limit import wait_for_domain
from scrapers.robots import check_robots
from scrapers.session import wipe_driver, wipe_profile
from scrapers.validation import ValidationResult, validate_scrape


@dataclass
class ScrapeResult:
    validation: ValidationResult
    artifact: dict[str, Any]
    bundle: ArtifactBundle


_COOKIE_BUTTON_IDS = (
    "onetrust-accept-btn-handler",
    "ensAcceptAll",
    "accept-cookies",
)

_EXPAND_HINTS = (
    "terms and conditions",
    "terms & conditions",
    "points conversion",
    "bonus miles",
    "how to earn",
    "conversion campaign",
)


def accept_cookies(driver: WebDriver, labels: list[str], timeout: float = 6.0) -> bool:
    """Click the first visible cookie-consent button matching contract labels."""
    lowered = [label.lower() for label in labels]
    deadline = time.time() + timeout
    for button_id in _COOKIE_BUTTON_IDS:
        try:
            el = driver.find_element(By.ID, button_id)
            if el.is_displayed():
                el.click()
                time.sleep(0.4)
                return True
        except WebDriverException:
            continue
    selectors = [
        "button",
        "a",
        "[role='button']",
        "input[type='button']",
        "input[type='submit']",
    ]
    while time.time() < deadline:
        for selector in selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
            except WebDriverException:
                continue
            for el in elements:
                try:
                    if not el.is_displayed():
                        continue
                    text = (el.text or el.get_attribute("value") or "").strip().lower()
                    aria = (el.get_attribute("aria-label") or "").strip().lower()
                    if any(label == text or label in text or label in aria for label in lowered):
                        el.click()
                        time.sleep(0.4)
                        return True
                except WebDriverException:
                    continue
        time.sleep(0.25)
    return False


def expand_collapsed_panels(driver: WebDriver, timeout: float = 8.0) -> int:
    """Open accordion / disclosure controls that usually hide conversion copy."""
    clicked = 0
    deadline = time.time() + timeout
    selectors = (
        "button[aria-expanded='false']",
        "[role='button'][aria-expanded='false']",
        "details:not([open]) > summary",
    )
    while time.time() < deadline:
        progressed = False
        for selector in selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
            except WebDriverException:
                continue
            for el in elements:
                try:
                    if not el.is_displayed():
                        continue
                    text = (el.text or el.get_attribute("aria-label") or "").strip().lower()
                    if selector.endswith("summary") or any(hint in text for hint in _EXPAND_HINTS):
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                        el.click()
                        clicked += 1
                        progressed = True
                        time.sleep(0.4)
                except WebDriverException:
                    continue
        if not progressed:
            break
    return clicked


def wait_for_contract_selectors(driver: WebDriver, selectors: list[str], timeout: float = 20.0) -> bool:
    def any_present(drv: WebDriver) -> bool:
        for selector in selectors:
            try:
                if drv.find_elements(By.CSS_SELECTOR, selector):
                    return True
            except WebDriverException:
                continue
        return False

    WebDriverWait(driver, timeout).until(any_present)
    return True


def scroll_page(driver: WebDriver, to_bottom: bool = True) -> None:
    if not to_bottom:
        driver.execute_script("window.scrollBy(0, Math.min(800, document.body.scrollHeight / 3));")
        time.sleep(0.4)
        return
    last_height = 0
    for _ in range(6):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(0.45)
        height = driver.execute_script("return document.body.scrollHeight") or 0
        if height == last_height:
            break
        last_height = height
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(0.2)


def visible_text(driver: WebDriver) -> str:
    try:
        return driver.execute_script(
            "return document.body && document.body.innerText ? document.body.innerText : '';"
        ) or ""
    except WebDriverException:
        return driver.find_element(By.TAG_NAME, "body").text


def _chart_follow_urls(driver: WebDriver, contract: SiteContract) -> list[str]:
    urls = list(contract.extra_urls)
    fragments = ("MILESCONVERSIONPARTNERS", "miles-conversion", "conversion-partners")
    try:
        for el in driver.find_elements(By.CSS_SELECTOR, "a[href]"):
            href = el.get_attribute("href") or ""
            if any(frag.lower() in href.lower() for frag in fragments):
                urls.append(href)
    except WebDriverException:
        pass
    seen: set[str] = set()
    ordered: list[str] = []
    for url in urls:
        if url and url not in seen and url.rstrip("/") != contract.url.rstrip("/"):
            seen.add(url)
            ordered.append(url)
    return ordered


def _append_extra_pages(
    driver: WebDriver,
    contract: SiteContract,
    html: str,
    text: str,
    title: str,
    final_url: str,
) -> tuple[str, str, str, str]:
    if detect_chart(html, contract.chart_keywords).found:
        return html, text, title, final_url
    extras = _chart_follow_urls(driver, contract)
    if not extras:
        return html, text, title, final_url
    combined_html = [html]
    combined_text = [text]
    last_title = title
    last_url = final_url
    for url in extras:
        try:
            driver.get(url)
            accept_cookies(driver, contract.cookie_button_texts)
            try:
                wait_for_contract_selectors(driver, contract.wait_selectors + ["h1", "h2", "main"])
            except TimeoutException:
                pass
            time.sleep(min(contract.js_settle_seconds, 3.0))
            expand_collapsed_panels(driver)
            scroll_page(driver, contract.scroll_to_bottom)
            extra_html = driver.page_source or ""
            extra_text = visible_text(driver)
            combined_html.append(extra_html)
            combined_text.append(extra_text)
            last_title = driver.title or last_title
            last_url = driver.current_url or last_url
        except WebDriverException:
            continue
    return "\n".join(combined_html), "\n".join(combined_text), last_title, last_url


def cookie_names(driver: WebDriver) -> list[str]:
    try:
        return [c.get("name", "") for c in driver.get_cookies()]
    except WebDriverException:
        return []


def selenium_locators_for_table(driver: WebDriver, table_index: int | None) -> tuple[str, str]:
    return selenium_locators_for_chart(driver, source="table", index=table_index)


def selenium_locators_for_chart(
    driver: WebDriver, source: str, index: int | None
) -> tuple[str, str]:
    if index is None:
        return "", ""
    tag = {"table": "table", "dl": "dl", "dl_group": "dl", "list": "ul"}.get(source)
    if not tag:
        return "", ""
    xpath = f"(//{tag})[{index + 1}]"
    css = f"{tag}:nth-of-type({index + 1})"
    try:
        el = driver.find_elements(By.XPATH, xpath)
        if el:
            return xpath, css
    except WebDriverException:
        pass
    return xpath, css


def capture_page(
    driver: WebDriver,
    contract: SiteContract,
    *,
    tier: int,
    browser: str,
    proxy_type: str,
    profile_dir,
    vm_profile: VmProfile | None = None,
    rate_limit: bool = True,
    pre_get: Callable[[WebDriver], None] | None = None,
) -> ScrapeResult:
    vm_profile = vm_profile or load_vm_profile()
    robots = check_robots(contract.url)
    timed_out = False
    error: str | None = None
    html = ""
    text = ""
    title = ""
    final_url = ""
    png: bytes | None = None
    selenium_xpath = ""
    selenium_css = ""

    if rate_limit:
        wait_for_domain(contract.url)

    try:
        if pre_get:
            pre_get(driver)
        driver.get(contract.url)
        accept_cookies(driver, contract.cookie_button_texts)
        try:
            wait_for_contract_selectors(driver, contract.wait_selectors)
        except TimeoutException:
            timed_out = True
        time.sleep(contract.js_settle_seconds)
        expand_collapsed_panels(driver)
        scroll_page(driver, contract.scroll_to_bottom)
        html = driver.page_source or ""
        text = visible_text(driver)
        title = driver.title or ""
        final_url = driver.current_url or contract.url
        html, text, title, final_url = _append_extra_pages(
            driver, contract, html, text, title, final_url
        )
        try:
            png = driver.get_screenshot_as_png()
        except WebDriverException:
            png = None
        chart: ChartResult = detect_chart(html, contract.chart_keywords)
        selenium_xpath, selenium_css = selenium_locators_for_chart(driver, chart.source, chart.table_index)
        bot = detect_bot_block(html, title=title, cookies=cookie_names(driver), visible_text=text)
        validation = validate_scrape(
            contract,
            html=html,
            visible_text=text,
            title=title,
            final_url=final_url,
            cookies=cookie_names(driver),
            timed_out=timed_out,
            chart=chart,
            bot=bot,
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        validation = validate_scrape(
            contract,
            html=html,
            visible_text=text,
            title=title,
            final_url=final_url,
            error=error,
        )

    inspect_hits = inspect_keyword_hits(html) if html else []
    bundle = write_artifacts(
        contract,
        tier=tier,
        validation=validation,
        html=html,
        screenshot_bytes=png,
        extra={"robots": robots.to_dict()},
        browser=browser,
        proxy_type=proxy_type,
        egress_ip=fetch_egress_ip(),
        machine_id=vm_profile.machine_id,
        timezone_name=vm_profile.timezone,
        inspect_hits=inspect_hits,
        selenium_xpath=selenium_xpath,
        selenium_css=selenium_css,
    )
    wipe_driver(driver)
    wipe_profile(profile_dir)
    return ScrapeResult(validation=validation, artifact=bundle.payload, bundle=bundle)
