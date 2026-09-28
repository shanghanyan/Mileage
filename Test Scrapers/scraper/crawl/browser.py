"""Playwright browser session with stealth settings."""

from __future__ import annotations

import asyncio
import random
from typing import Any

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright


STEALTH_INIT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
"""


class BrowserSession:
    def __init__(self, headless: bool = True, slow_mo_ms: int = 80):
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        launch_kwargs: dict[str, Any] = {
            "headless": self.headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        }
        if not self.headless:
            launch_kwargs["slow_mo"] = self.slow_mo_ms
        self._browser = await self._playwright.chromium.launch(**launch_kwargs)
        self._context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="America/Los_Angeles",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,*/*;q=0.8"
                ),
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Upgrade-Insecure-Requests": "1",
            },
        )
        await self._context.add_init_script(STEALTH_INIT)

    async def stop(self) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def new_page(self) -> Page:
        if not self._context:
            raise RuntimeError("Browser not started")
        return await self._context.new_page()

    async def load_page(
        self,
        url: str,
        timeout_ms: int = 60000,
        wait_after_ms: int = 3000,
    ) -> tuple[Page, str, int | None]:
        """Returns (page, html, http_status). Caller must close page."""
        page = await self.new_page()
        status: int | None = None
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            if response:
                status = response.status
                retry_after = response.headers.get("retry-after")
                if status == 429 and retry_after:
                    delay = int(retry_after) if retry_after.isdigit() else 60
                    await asyncio.sleep(delay)
                    response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    if response:
                        status = response.status
            await page.wait_for_timeout(wait_after_ms)
            await self._scroll_page(page)
            html = await page.content()
            return page, html, status
        except Exception:
            try:
                html = await page.content()
            except Exception:
                html = ""
            return page, html, status

    async def _scroll_page(self, page: Page) -> None:
        if self.headless:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1500)
            return

        await page.evaluate(
            """async () => {
                const delay = (ms) => new Promise((r) => setTimeout(r, ms));
                const height = Math.max(
                    document.body.scrollHeight,
                    document.documentElement.scrollHeight
                );
                const step = Math.max(window.innerHeight * 0.65, 400);
                for (let y = 0; y < height; y += step) {
                    window.scrollTo({ top: y, behavior: "smooth" });
                    await delay(450);
                }
                window.scrollTo({ top: height, behavior: "smooth" });
                await delay(600);
            }"""
        )

    @staticmethod
    async def random_delay(min_s: float, max_s: float) -> None:
        await asyncio.sleep(random.uniform(min_s, max_s))
