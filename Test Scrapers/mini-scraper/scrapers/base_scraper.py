import os
import time
import random
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from rich.logging import RichHandler
from rich.console import Console

from optimizer.models import ScraperResult, ScraperMethod, ScraperStatus

console = Console()
Path("logs").mkdir(exist_ok=True)


# ── Anti-detection / resilience helpers ────────────────────────────────────────
#
# Sites like Capital One, TPG and Turkish Airlines actively reject the default
# Playwright fingerprint (headless + navigator.webdriver=true + automation flags).
# These helpers give every Playwright scraper a realistic browser fingerprint and
# make navigation tolerant of slow loads, odd wait selectors and HTTP/2 rejections
# instead of hard-failing into the stale-cache fallback path.

# Pool of current, realistic desktop user agents to rotate through.
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

# Chromium launch flags that hide the most obvious automation tells.
# Kept intentionally minimal: aggressive flags like --no-sandbox / --disable-gpu
# abort the browser (SIGABRT) in some sandboxed environments. The bulk of the
# fingerprint hardening comes from the context options + init script below.
STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
]

# Runs before any page script; patches the JS-visible properties bot-detectors probe.
STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || { runtime: {} };
const _q = window.navigator.permissions && window.navigator.permissions.query;
if (_q) {
  window.navigator.permissions.query = (p) => (
    p && p.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : _q(p)
  );
}
"""

ACCEPT_LANGUAGE = "en-US,en;q=0.9"


def pick_user_agent() -> str:
    """Return a user agent, rotating through the pool unless one is pinned.

    If USER_AGENT is set in the env and USER_AGENT_ROTATE is not 'true', the
    pinned value is used verbatim; otherwise a random UA is chosen each call.
    """
    env_ua = os.getenv("USER_AGENT", "").strip()
    rotate = os.getenv("USER_AGENT_ROTATE", "true").lower() == "true"
    if env_ua and not rotate:
        return env_ua
    pool = list(USER_AGENTS)
    if env_ua and env_ua not in pool:
        pool.append(env_ua)
    return random.choice(pool)


async def launch_stealth_browser(p, *, headless: bool = True, extra_args=None):
    """Launch Chromium with anti-detection flags. `extra_args` is appended."""
    args = list(STEALTH_ARGS)
    if extra_args:
        args.extend(extra_args)
    return await p.chromium.launch(headless=headless, args=args)


async def new_stealth_page(browser, *, user_agent: str | None = None):
    """Create a page in a fresh context with a realistic fingerprint."""
    ua = user_agent or pick_user_agent()
    context = await browser.new_context(
        user_agent=ua,
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
        timezone_id="America/New_York",
        extra_http_headers={
            "Accept-Language": ACCEPT_LANGUAGE,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Upgrade-Insecure-Requests": "1",
        },
    )
    await context.add_init_script(STEALTH_INIT_SCRIPT)
    return await context.new_page()


async def goto_resilient(page, url: str, *, timeout_ms: int, logger=None) -> None:
    """Navigate with progressively looser wait conditions.

    Tries domcontentloaded → load → commit. This tolerates sites that never
    reach the strict 'load' state (lazy third-party widgets) and recovers from
    transient protocol errors (e.g. ERR_HTTP2_PROTOCOL_ERROR) on a retry.
    """
    last_exc: Exception | None = None
    for wait_until in ("domcontentloaded", "load", "commit"):
        try:
            await page.goto(url, timeout=timeout_ms, wait_until=wait_until)
            return
        except Exception as exc:  # noqa: BLE001 - want broad nav resilience
            last_exc = exc
            if logger:
                logger.warning(
                    f"goto wait_until={wait_until} failed ({type(exc).__name__}): {exc}"
                )
    if last_exc:
        raise last_exc


async def wait_for_selector_safe(
    page, selector: str, *, timeout_ms: int, logger=None
) -> bool:
    """Best-effort wait for a selector. Never raises.

    Returns True if found. On timeout we log and return False so the caller can
    fall back to parsing whatever DOM is present (or its own fallback data)
    instead of failing the whole scrape.
    """
    try:
        await page.wait_for_selector(selector, timeout=timeout_ms)
        return True
    except Exception as exc:  # noqa: BLE001
        if logger:
            logger.warning(
                f"selector '{selector}' not visible within {timeout_ms}ms "
                f"({type(exc).__name__}); proceeding with current DOM"
            )
        return False


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s | %(name)-26s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ"
    )
    rich_handler = RichHandler(console=console, show_path=False, markup=True)
    rich_handler.setFormatter(fmt)
    logger.addHandler(rich_handler)

    file_handler = logging.FileHandler(f"logs/scraper_{name}.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))
    return logger


class BaseScraper(ABC):
    name:   str
    url:    str
    method: ScraperMethod

    def __init__(self) -> None:
        self.logger = get_logger(self.name)

    @abstractmethod
    async def _fetch(self) -> dict:
        ...

    @abstractmethod
    def to_edges(self, data: dict) -> list:
        ...

    def get_fallback_data(self) -> dict:
        import json
        cache_path = os.getenv("RATES_CACHE_PATH", "./config/rates_cache.json")
        try:
            with open(cache_path) as f:
                cache = json.load(f)
            return cache.get(self.name, {})
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    async def scrape(self) -> ScraperResult:
        t0 = time.monotonic()
        self.logger.info(f"START url={self.url} method={self.method.value}")

        try:
            data = await self._fetch_with_retry()
            duration_ms = int((time.monotonic() - t0) * 1000)
            self.logger.info(
                f"SUCCESS url={self.url} duration_ms={duration_ms} "
                f"keys={list(data.keys())}"
            )
            return ScraperResult(
                scraper_name=self.name, source_url=self.url,
                method=self.method, status=ScraperStatus.SUCCESS,
                data=data, duration_ms=duration_ms
            )

        except Exception as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            self.logger.error(
                f"FAILED url={self.url} duration_ms={duration_ms} error={exc}"
            )
            fallback = self.get_fallback_data()
            if fallback:
                self.logger.warning(
                    f"CACHED url={self.url} — using stale rates_cache.json data"
                )
                return ScraperResult(
                    scraper_name=self.name, source_url=self.url,
                    method=self.method, status=ScraperStatus.CACHED,
                    data=fallback, duration_ms=duration_ms,
                    error=str(exc)
                )
            return ScraperResult(
                scraper_name=self.name, source_url=self.url,
                method=self.method, status=ScraperStatus.FAILED,
                data={}, duration_ms=duration_ms, error=str(exc)
            )

    async def _fetch_with_retry(self) -> dict:
        from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential

        attempt = 0
        async for attempt_obj in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=2, min=5, max=30),
            reraise=True
        ):
            with attempt_obj:
                attempt += 1
                if attempt > 1:
                    self.logger.warning(
                        f"RETRYING attempt={attempt}/3 url={self.url}"
                    )
                return await self._fetch()
