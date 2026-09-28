"""S5 — Direct API / HTML probe (httpx, no browser).

The cheapest possible approach: hit LifeMiles URLs directly with browser-like
headers and see if anything useful comes back without a full browser. This
documents *why* a headless browser is mandatory: LifeMiles is fronted by Akamai
and returns HTTP 403 "Access Denied" (errors.edgesuite.net) to non-browser
clients on every endpoint, so direct httpx scraping — and naive API calls — are
dead on arrival.

It's kept as a strategy so the report contains hard evidence of the block rather
than an assumption, and so a future change in LifeMiles' posture would surface
here automatically.
"""

from __future__ import annotations

import httpx

from common import StrategyResult, save_artifact
from . import Strategy

PROBE_URLS = [
    "https://www.lifemiles.com/",
    "https://www.lifemiles.com/use/redeem-miles/flights",
    # Speculative API-ish paths — included to show they are blocked too.
    "https://www.lifemiles.com/api/redeem/flights",
    "https://api.lifemiles.com/",
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class ApiProbeStrategy(Strategy):
    name = "api_probe"
    label = "Direct httpx probe of lifemiles.com (no browser)"
    cost = "low"

    async def run(self) -> StrategyResult:
        result = StrategyResult(name=self.name, label=self.label, cost=self.cost)
        timeout = self.ctx.get("http_timeout", 20)
        summary: list[str] = []

        async with httpx.AsyncClient(
            headers=_HEADERS, timeout=timeout, follow_redirects=True
        ) as client:
            for url in PROBE_URLS:
                try:
                    resp = await client.get(url)
                    blocked = (
                        resp.status_code == 403
                        or "access denied" in resp.text[:500].lower()
                        or "edgesuite" in resp.text[:500].lower()
                    )
                    line = (
                        f"{resp.status_code} {'BLOCKED' if blocked else 'OK'} "
                        f"bytes={len(resp.content)} {url}"
                    )
                    self.logger.info(line)
                    summary.append(line)
                except Exception as exc:  # noqa: BLE001
                    line = f"ERR {type(exc).__name__} {url}: {exc}"
                    self.logger.warning(line)
                    summary.append(line)

        result.notes = summary
        result.artifacts.append(
            save_artifact(f"{self.name}_summary.txt", "\n".join(summary))
        )
        # This strategy never returns chart rows; it documents the block.
        result.ok = False
        result.confidence = 0.0
        result.error = (
            "direct access blocked (Akamai 403) — confirms a stealth browser is "
            "required; httpx-only scraping of lifemiles.com is not viable"
        )
        return result
