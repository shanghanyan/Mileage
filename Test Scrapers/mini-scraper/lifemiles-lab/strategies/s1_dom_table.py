"""S1 — Official-site DOM/table selector scrape (stealth Playwright).

This mirrors what the production scraper *tries* to do: load the LifeMiles page
with a real (stealth) browser and read an award-chart ``<table>`` out of the DOM.

It exists in the lab as the control/baseline: it documents *why* the current
approach fails. LifeMiles is Akamai-fronted and the redeem URL is a JS booking
widget, so we expect either a block or a page with no chart table. We capture
the page title, HTTP status, table count and a screenshot so the failure mode is
evidenced rather than assumed.
"""

from __future__ import annotations

from playwright.async_api import async_playwright

from common import (
    AwardRow,
    StrategyResult,
    LIFEMILES_URL,
    launch_stealth_browser,
    new_stealth_page,
    goto_resilient,
    detect_block,
    parse_miles,
    looks_like_miles,
    save_artifact,
)
from . import Strategy

# Award-chart-ish selectors, broadest-first.
CANDIDATE_SELECTORS = [
    "table",
    "[class*='award'] table",
    "[class*='chart']",
    "[role='table']",
]


class DomTableStrategy(Strategy):
    name = "dom_table"
    label = "Official-site DOM <table> scrape (stealth Playwright)"
    cost = "medium"

    async def run(self) -> StrategyResult:
        result = StrategyResult(name=self.name, label=self.label, cost=self.cost)
        headless = self.ctx.get("headless", True)
        timeout_ms = self.ctx.get("nav_timeout_ms", 45000)

        async with async_playwright() as p:
            browser = await launch_stealth_browser(p, headless=headless)
            context, page = await new_stealth_page(browser)
            try:
                ok = await goto_resilient(
                    page, LIFEMILES_URL, timeout_ms=timeout_ms, logger=self.logger
                )
                if not ok:
                    result.error = "navigation failed (likely Akamai block)"
                    return result

                title = await page.title()
                self.logger.info(f"page title={title!r}")
                result.notes.append(f"page title: {title!r}")

                blocked, block_note = await detect_block(page)
                if blocked:
                    result.notes.append(f"Akamai block detected ({block_note})")

                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1500)

                shot = await page.screenshot(full_page=True, type="png")
                result.artifacts.append(save_artifact(f"{self.name}.png", shot))

                # Count and parse any tables present in the DOM.
                tables = await page.query_selector_all("table")
                self.logger.info(f"tables_in_dom={len(tables)}")
                result.notes.append(f"{len(tables)} <table> element(s) in DOM")

                rows = await self._parse_tables(page)
                if rows:
                    result.rows = rows
                    result.ok = True
                    result.confidence = 0.6
                    result.notes.append(f"parsed {len(rows)} award rows from DOM table")
                else:
                    result.error = (
                        "no parseable award-chart table in DOM "
                        "(page is a JS booking widget, not a static chart)"
                    )
                return result
            finally:
                await context.close()
                await browser.close()

    async def _parse_tables(self, page) -> list[AwardRow]:
        """Extract rows from any DOM <table> that looks like an award chart."""
        tables_data = await page.evaluate(
            """() => Array.from(document.querySelectorAll('table')).map(t => {
                const rows = Array.from(t.querySelectorAll('tr'));
                return rows.map(r => Array.from(r.querySelectorAll('th,td'))
                    .map(c => c.innerText.trim()));
            })"""
        )
        out: list[AwardRow] = []
        for grid in tables_data:
            if not grid or len(grid) < 2:
                continue
            header = [h.lower() for h in grid[0]]
            # Heuristic: needs an origin-ish + cabin-ish header.
            has_cabin = any(
                k in " ".join(header) for k in ("economy", "business", "first")
            )
            if not has_cabin:
                continue
            for cells in grid[1:]:
                if len(cells) < 3:
                    continue
                eco_lo, _ = parse_miles(cells[-2]) if len(cells) >= 2 else (None, None)
                out.append(
                    AwardRow(
                        origin=cells[0],
                        destination=cells[1] if len(cells) > 1 else "",
                        economy_miles=eco_lo if looks_like_miles(eco_lo) else None,
                        one_way=True,
                        source="lifemiles.com DOM",
                        raw={"cells": cells},
                    )
                )
        return [r for r in out if r.has_any_price()]
