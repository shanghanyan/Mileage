"""S2 — Network interception (Playwright response sniffing).

The advice "maybe the data is in an API call you could intercept" — tested here.
We drive a stealth browser to the LifeMiles page and record every XHR/fetch
response. JSON payloads are saved to disk and scanned for award-pricing shapes
(keys/values that look like mile costs). If LifeMiles fetches its chart or search
results over an API, this captures the endpoint + payload so a future scraper can
hit it directly (or keep intercepting).

This is the highest-ceiling strategy for *live* prices, but also the most
fragile: it depends on getting past Akamai and, for real prices, on driving the
origin/destination/date search form. We capture whatever surfaces and report the
endpoint inventory either way.
"""

from __future__ import annotations

import json

from playwright.async_api import async_playwright

from common import (
    AwardRow,
    StrategyResult,
    LIFEMILES_URL,
    launch_stealth_browser,
    new_stealth_page,
    goto_resilient,
    detect_block,
    save_artifact,
    looks_like_miles,
)
from . import Strategy

# Substrings that hint a JSON payload carries award/pricing data.
PRICE_HINT_KEYS = ("miles", "award", "redeem", "price", "amount", "fare", "cabin")


def _scan_json_for_miles(obj, found: list[int], depth: int = 0) -> None:
    """Recursively collect integers that plausibly represent mile costs."""
    if depth > 8:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (int, float)) and looks_like_miles(int(v)):
                if any(h in str(k).lower() for h in ("mile", "award", "amount", "price")):
                    found.append(int(v))
            _scan_json_for_miles(v, found, depth + 1)
    elif isinstance(obj, list):
        for item in obj[:200]:
            _scan_json_for_miles(item, found, depth + 1)


class NetworkInterceptStrategy(Strategy):
    name = "network_intercept"
    label = "Playwright network/XHR interception (live API capture)"
    cost = "high"

    async def run(self) -> StrategyResult:
        result = StrategyResult(name=self.name, label=self.label, cost=self.cost)
        headless = self.ctx.get("headless", True)
        timeout_ms = self.ctx.get("nav_timeout_ms", 45000)

        captured: list[dict] = []

        async with async_playwright() as p:
            browser = await launch_stealth_browser(p, headless=headless)
            context, page = await new_stealth_page(browser)

            async def on_response(response):
                try:
                    ct = response.headers.get("content-type", "")
                    if "json" not in ct and "javascript" not in ct:
                        return
                    url = response.url
                    if not any(h in url.lower() for h in PRICE_HINT_KEYS) and "api" not in url.lower():
                        # Still record the endpoint, but skip body capture for noise.
                        captured.append({"url": url, "status": response.status, "body": None})
                        return
                    body = await response.body()
                    captured.append(
                        {"url": url, "status": response.status, "body": body[:200000]}
                    )
                    self.logger.info(f"captured json endpoint status={response.status} {url[:110]}")
                except Exception:  # noqa: BLE001
                    pass

            page.on("response", on_response)

            try:
                ok = await goto_resilient(
                    page, LIFEMILES_URL, timeout_ms=timeout_ms, logger=self.logger
                )
                if not ok:
                    result.error = "navigation failed (likely Akamai block)"
                else:
                    # Give XHRs time to fire; nudge lazy loaders by scrolling.
                    await page.wait_for_timeout(2000)
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    try:
                        await page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:  # noqa: BLE001
                        pass
                    blocked, block_note = await detect_block(page)
                    if blocked:
                        result.notes.append(f"Akamai block detected ({block_note})")
            finally:
                await context.close()
                await browser.close()

        json_endpoints = [c for c in captured if c["body"]]
        all_endpoints = sorted({c["url"] for c in captured})
        self.logger.info(
            f"total_responses={len(captured)} json_with_body={len(json_endpoints)} "
            f"unique_endpoints={len(all_endpoints)}"
        )
        result.notes.append(
            f"captured {len(captured)} JSON/JS responses, "
            f"{len(json_endpoints)} candidate payloads"
        )

        # Persist the endpoint inventory for offline analysis.
        inventory = save_artifact(
            f"{self.name}_endpoints.json",
            json.dumps(all_endpoints, indent=2),
        )
        result.artifacts.append(inventory)

        rows: list[AwardRow] = []
        for cand in json_endpoints:
            try:
                payload = json.loads(cand["body"])
            except Exception:  # noqa: BLE001
                continue
            art = save_artifact(
                f"{self.name}_payload_{abs(hash(cand['url'])) % 10000}.json",
                json.dumps(payload, indent=2)[:200000],
            )
            result.artifacts.append(art)
            found: list[int] = []
            _scan_json_for_miles(payload, found)
            if found:
                self.logger.info(f"mile-like values in {cand['url'][:90]}: {found[:8]}")
                for v in dict.fromkeys(found):  # de-dupe, keep order
                    rows.append(
                        AwardRow(
                            origin="(from API)",
                            destination="(from API)",
                            economy_miles=v,
                            one_way=True,
                            source=cand["url"],
                            raw={"endpoint": cand["url"]},
                        )
                    )

        if rows:
            result.rows = rows
            result.ok = True
            # Lower confidence: values are inferred from raw payloads, unlabeled by route.
            result.confidence = 0.45
            result.notes.append("award-like mile values extracted from intercepted JSON")
        else:
            if not result.error:
                result.error = (
                    "no award-pricing JSON intercepted on page load "
                    "(prices require driving the search form; endpoints saved for analysis)"
                )
        return result
