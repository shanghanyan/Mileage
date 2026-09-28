"""S3 — Vision scrape (screenshot + Ollama), hardened.

Implements the two pieces of advice about vision directly:

  1. "Improve the prompt so JSON output is constrained and easy to parse" —
     we pass Ollama ``format="json"`` (structured-output mode) so the model is
     *forced* to emit valid JSON. This eliminates the production scraper's
     parse-failure retry death spiral (the logs show repeated
     "JSON parse failed ... retrying with stricter prompt").

  2. "Swap the vision model for something faster/more instruction-following" —
     we benchmark each configured model (default: the heavy
     ``llama3.2-vision:11b`` vs the lighter/faster ``llava``) on the *same*
     screenshot and report per-model latency + row yield so the trade-off is
     visible rather than guessed.

Target: by default we screenshot the official LifeMiles page (the real task).
Because that page has no chart, vision is starved of input — a genuine finding.
Pass ``--vision-chart-url`` to instead screenshot a page that *does* render a
chart, which validates that the hardened pipeline extracts correctly when given
real chart pixels.
"""

from __future__ import annotations

import io
import json
import time

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

VISION_PROMPT = (
    "You are extracting an airline award chart from the screenshot. "
    "Each row is an origin region, a destination region, and the miles needed "
    "per one-way flight in each cabin. "
    "Respond with ONLY a JSON object of this exact shape:\n"
    '{"rows": [{"origin": "string", "destination": "string", '
    '"economy_miles": number_or_null, "business_miles": number_or_null, '
    '"first_miles": number_or_null}]}\n'
    "Use integers with no commas (e.g. 35000, not 35,000). "
    "If the image shows no award chart, return {\"rows\": []}."
)

# JSON schema handed to Ollama format= for strict structured output.
JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string"},
                    "destination": {"type": "string"},
                    "economy_miles": {"type": ["integer", "null"]},
                    "business_miles": {"type": ["integer", "null"]},
                    "first_miles": {"type": ["integer", "null"]},
                },
                "required": ["origin", "destination"],
            },
        }
    },
    "required": ["rows"],
}


def _response_text(response) -> str:
    """Handle both dict and typed-object Ollama responses (version-dependent)."""
    message = response["message"] if isinstance(response, dict) else response.message
    content = message["content"] if isinstance(message, dict) else message.content
    return content or ""


class VisionStrategy(Strategy):
    name = "vision"
    label = "Vision screenshot + Ollama (format=json, model A/B)"
    cost = "high"

    async def run(self) -> StrategyResult:
        import ollama

        result = StrategyResult(name=self.name, label=self.label, cost=self.cost)
        headless = self.ctx.get("headless", True)
        timeout_ms = self.ctx.get("nav_timeout_ms", 45000)
        host = self.ctx.get("ollama_host", "http://localhost:11434")
        models = self.ctx.get("vision_models", ["llama3.2-vision:11b", "llava:latest"])
        target_url = self.ctx.get("vision_chart_url") or LIFEMILES_URL
        use_schema = self.ctx.get("vision_schema", True)
        self._timeout_s = self.ctx.get("vision_timeout_s", 180)

        # Per-call timeout: local vision generation latency is wildly variable
        # (we have seen a single image take 5+ minutes), and one slow model must
        # never hang the whole lab.
        client = ollama.Client(host=host, timeout=self._timeout_s)
        try:
            available = {m["model"] for m in client.list().get("models", [])}
        except Exception as exc:  # noqa: BLE001
            result.error = f"Ollama unavailable at {host}: {exc}"
            return result
        models = [m for m in models if m in available] or list(available)
        self.logger.info(f"vision models to try: {models}")

        # 1. Capture the screenshot once, reuse across models.
        png, blocked, block_note = await self._screenshot(target_url, headless, timeout_ms)
        if png is None:
            result.error = "could not capture screenshot (navigation failed)"
            return result
        shot_path = save_artifact(f"{self.name}.png", png)
        result.artifacts.append(shot_path)
        if blocked:
            # The page is an Akamai block screen — no chart pixels exist. Running
            # the vision model here would only produce hallucinated rows (proven
            # in testing), so we refuse and skip the expensive model calls.
            self.logger.warning(f"target blocked ({block_note}); skipping vision to avoid hallucination")
            result.error = (
                f"target page blocked ({block_note}); vision skipped to avoid "
                "hallucinating data from a no-content page"
            )
            result.notes.append(
                "blocked page detected — vision intentionally not run "
                "(a vision model will fabricate a chart from a blank/denied page)"
            )
            return result
        b64 = self._encode(png)
        result.notes.append(f"screenshot target: {target_url} ({len(png)//1024} KB)")

        # 2. Benchmark each model on that screenshot.
        best_rows: list[AwardRow] = []
        best_model = None
        per_model: list[str] = []
        for model in models:
            rows, ms, raw = self._extract(client, model, b64, use_schema)
            note = f"{model}: {len(rows)} rows in {ms}ms"
            per_model.append(note)
            self.logger.info(note)
            art = save_artifact(f"{self.name}_{model.replace(':','_').replace('/','_')}.json", raw[:50000])
            result.artifacts.append(art)
            if len(rows) > len(best_rows):
                best_rows, best_model = rows, model

        result.notes.extend(per_model)
        if best_rows:
            result.rows = best_rows
            result.ok = True
            # Vision is inherently noisier than structured HTML; cap confidence.
            result.confidence = 0.55
            result.notes.append(f"best model: {best_model} ({len(best_rows)} rows)")
        else:
            result.error = (
                "vision returned no chart rows — the target page renders no award "
                "chart, so even valid-JSON output is empty (format=json prevented "
                "the parse-failure death spiral, which is the real fix)"
            )
        return result

    async def _screenshot(self, url, headless, timeout_ms) -> tuple[bytes | None, bool, str]:
        """Return (png, blocked, note). png is None only on navigation failure.

        We prefer an *element-scoped* screenshot of the award-chart table over a
        full-page capture: a full page downscaled to fit the vision model's input
        renders the table text unreadable, so the model extracts nothing. Cropping
        to the table keeps the digits legible.
        """
        async with async_playwright() as p:
            browser = await launch_stealth_browser(p, headless=headless)
            context, page = await new_stealth_page(browser)
            try:
                ok = await goto_resilient(page, url, timeout_ms=timeout_ms, logger=self.logger)
                if not ok:
                    return None, False, "navigation failed"
                await page.wait_for_timeout(1000)
                blocked, note = await detect_block(page)
                if blocked:
                    png = await page.screenshot(type="png")
                    return png, blocked, note

                # Find the chart table (one whose text mentions a cabin class) and
                # screenshot just that element if we can.
                handle = await page.evaluate_handle(
                    """() => {
                        const tables = Array.from(document.querySelectorAll('table'));
                        return tables.find(t => /economy|business|first/i.test(t.innerText)) || null;
                    }"""
                )
                element = handle.as_element()
                if element:
                    try:
                        await element.scroll_into_view_if_needed()
                        await page.wait_for_timeout(400)
                        png = await element.screenshot(type="png")
                        self.logger.info("captured element-scoped screenshot of chart table")
                        return png, False, note
                    except Exception as exc:  # noqa: BLE001
                        self.logger.warning(f"element screenshot failed ({exc}); full page")
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(800)
                png = await page.screenshot(full_page=True, type="png")
                return png, False, note
            finally:
                await context.close()
                await browser.close()

    def _encode(self, png: bytes) -> str:
        import base64
        from PIL import Image

        # Downscale very tall full-page shots to keep the vision model snappy.
        img = Image.open(io.BytesIO(png))
        if img.height > 2200:
            ratio = 2200 / img.height
            img = img.resize((int(img.width * ratio), 2200))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    def _chat(self, client, model, b64, fmt) -> str:
        response = client.chat(
            model=model,
            format=fmt,
            options={"temperature": 0},
            messages=[{"role": "user", "content": VISION_PROMPT, "images": [b64]}],
        )
        return _response_text(response).strip()

    def _extract(self, client, model, b64, use_schema) -> tuple[list[AwardRow], int, str]:
        import concurrent.futures

        t0 = time.monotonic()
        fmt = JSON_SCHEMA if use_schema else "json"
        timeout = getattr(self, "_timeout_s", 180)
        # Hard wall-clock cap: Ollama non-streaming calls don't honour httpx read
        # timeouts (no early bytes), and local vision latency can blow past any
        # budget, so we enforce the timeout in a worker thread. An orphaned slow
        # generation is left to die in the background; the lab moves on.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(self._chat, client, model, b64, fmt)
            try:
                raw = future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                ms = int((time.monotonic() - t0) * 1000)
                self.logger.warning(f"{model} timed out after {timeout}s")
                return [], ms, f"ERROR: timed out after {timeout}s"
            except Exception as exc:  # noqa: BLE001
                ms = int((time.monotonic() - t0) * 1000)
                return [], ms, f"ERROR: {exc}"
        ms = int((time.monotonic() - t0) * 1000)
        rows = self._parse(raw)
        return rows, ms, raw

    def _parse(self, raw: str) -> list[AwardRow]:
        try:
            data = json.loads(raw)
        except Exception:  # noqa: BLE001
            return []
        out: list[AwardRow] = []
        for r in data.get("rows", []) if isinstance(data, dict) else []:
            if not isinstance(r, dict):
                continue
            eco = r.get("economy_miles")
            biz = r.get("business_miles")
            first = r.get("first_miles")
            row = AwardRow(
                origin=str(r.get("origin", "")).strip(),
                destination=str(r.get("destination", "")).strip(),
                economy_miles=eco if looks_like_miles(eco if isinstance(eco, int) else None) else None,
                business_miles=biz if looks_like_miles(biz if isinstance(biz, int) else None) else None,
                first_miles=first if looks_like_miles(first if isinstance(first, int) else None) else None,
                one_way=True,
                source="vision",
                raw=r,
            )
            if row.origin and row.has_any_price():
                out.append(row)
        return out
