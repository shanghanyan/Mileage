"""Success / failure validation for a scrape attempt."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from scrapers.bot_detection import BotDetection, detect_bot_block
from scrapers.chart_detection import ChartResult, detect_chart
from scrapers.contracts import SiteContract

CHART_CONFIDENCE_THRESHOLD = 0.45

OUTCOME_SUCCESS = "success"
OUTCOME_CHART_NOT_FOUND = "chart_not_found"
OUTCOME_BOT_BLOCKED = "bot_blocked"
OUTCOME_EMPTY_CONTENT = "empty_content"
OUTCOME_WRONG_REGION = "wrong_region"
OUTCOME_LOGIN_REQUIRED = "login_required"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_ERROR = "error"


@dataclass
class ValidationResult:
    outcome: str
    deliverable_met: bool
    chart_confidence: float
    messages: list[str] = field(default_factory=list)
    bot_vendor: str | None = None
    bot_signals: list[str] = field(default_factory=list)
    text_length: int = 0
    final_url: str = ""
    chart: ChartResult | None = None
    title: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "deliverable_met": self.deliverable_met,
            "chart_confidence": round(self.chart_confidence, 4),
            "messages": self.messages,
            "bot_vendor": self.bot_vendor,
            "bot_signals": self.bot_signals,
            "text_length": self.text_length,
            "final_url": self.final_url,
            "title": self.title,
            "chart": self.chart.to_dict() if self.chart else None,
        }


def _title_blocked(title: str, fragments: list[str]) -> bool:
    lowered = title.lower()
    return any(frag.lower() in lowered for frag in fragments)


def _looks_login_gated(text: str, markers: list[str], chart_found: bool) -> bool:
    if chart_found:
        return False
    blob = text.lower()
    hits = sum(1 for m in markers if m.lower() in blob)
    return hits >= 2


def _wrong_region(contract: SiteContract, final_url: str, title: str, text: str) -> bool:
    blob = f"{title}\n{text}".lower()
    if any(frag.lower() in blob for frag in contract.region_fail_fragments if frag.isascii()):
        # Non-ascii fragments (e.g. 日本語) checked separately
        if any(frag.lower() in blob for frag in contract.region_fail_fragments if all(ord(c) < 128 for c in frag)):
            if "jal" in contract.key and "/arl/en/" in final_url and "partner" in blob:
                return False
    if contract.key == "jal_partner_point":
        path = urlparse(final_url).path
        if "/arl/en/" not in path and ("/jp/" in path or path.startswith("/jal/")):
            return True
        if "日本語" in title or "日本語" in text[:400]:
            if "partner point" not in blob and "partner" not in blob:
                return True
    return any(frag in blob for frag in (f.lower() for f in contract.region_fail_fragments if f.isascii()))


def validate_scrape(
    contract: SiteContract,
    html: str,
    visible_text: str,
    title: str = "",
    final_url: str = "",
    cookies: list[str] | None = None,
    timed_out: bool = False,
    error: str | None = None,
    chart: ChartResult | None = None,
    bot: BotDetection | None = None,
) -> ValidationResult:
    messages: list[str] = []
    if error:
        return ValidationResult(
            outcome=OUTCOME_ERROR,
            deliverable_met=False,
            chart_confidence=0.0,
            messages=[error],
            text_length=len(visible_text),
            final_url=final_url,
            title=title,
        )
    if timed_out and len(visible_text.strip()) < contract.min_page_text_length:
        return ValidationResult(
            outcome=OUTCOME_TIMEOUT,
            deliverable_met=False,
            chart_confidence=0.0,
            messages=["Selector or page load timed out"],
            text_length=len(visible_text),
            final_url=final_url,
            title=title,
        )
    if timed_out:
        messages.append("Selector wait timed out; validating captured page anyway")

    if _title_blocked(title, contract.block_title_fragments):
        messages.append(f"Blocked title: {title}")
        return ValidationResult(
            outcome=OUTCOME_BOT_BLOCKED,
            deliverable_met=False,
            chart_confidence=0.0,
            messages=messages,
            text_length=len(visible_text),
            final_url=final_url,
            title=title,
        )

    bot = bot or detect_bot_block(html, title=title, cookies=cookies or [], visible_text=visible_text)
    if bot.blocked:
        vendor_note = f" ({bot.vendor})" if bot.vendor else ""
        messages.append(f"Bot manager block{vendor_note}: {', '.join(bot.signals)}")
        return ValidationResult(
            outcome=OUTCOME_BOT_BLOCKED,
            deliverable_met=False,
            chart_confidence=0.0,
            messages=messages,
            bot_vendor=bot.vendor,
            bot_signals=bot.signals,
            text_length=len(visible_text),
            final_url=final_url,
            title=title,
        )

    text_length = len(visible_text.strip())
    if text_length < contract.min_page_text_length:
        messages.append(
            f"Visible text length {text_length} < min {contract.min_page_text_length}"
        )
        return ValidationResult(
            outcome=OUTCOME_EMPTY_CONTENT,
            deliverable_met=False,
            chart_confidence=0.0,
            messages=messages,
            bot_vendor=bot.vendor,
            bot_signals=bot.signals,
            text_length=text_length,
            final_url=final_url,
            title=title,
        )

    if _wrong_region(contract, final_url, title, visible_text):
        messages.append("Page appears geo-redirected away from expected locale")
        return ValidationResult(
            outcome=OUTCOME_WRONG_REGION,
            deliverable_met=False,
            chart_confidence=0.0,
            messages=messages,
            text_length=text_length,
            final_url=final_url,
            title=title,
        )

    content_hits = [kw for kw in contract.content_keywords if kw.lower() in visible_text.lower()]
    if not content_hits:
        messages.append("Content keyword smoke test failed (page may not have loaded target copy)")

    chart = chart or detect_chart(html, contract.chart_keywords)
    if _looks_login_gated(visible_text, contract.login_markers, chart.found):
        messages.append("Login/gated markers present without a validated chart")
        return ValidationResult(
            outcome=OUTCOME_LOGIN_REQUIRED,
            deliverable_met=False,
            chart_confidence=chart.confidence,
            messages=messages,
            bot_vendor=bot.vendor,
            bot_signals=bot.signals,
            text_length=text_length,
            final_url=final_url,
            title=title,
            chart=chart,
        )

    deliverable = chart.found and chart.confidence >= CHART_CONFIDENCE_THRESHOLD
    if deliverable:
        messages.append(
            f"Point conversion chart found (confidence={chart.confidence:.2f}, table_index={chart.table_index})"
        )
        outcome = OUTCOME_SUCCESS
    else:
        messages.append(
            f"No chart above threshold {CHART_CONFIDENCE_THRESHOLD} (best={chart.confidence:.2f})"
        )
        outcome = OUTCOME_CHART_NOT_FOUND

    return ValidationResult(
        outcome=outcome,
        deliverable_met=deliverable,
        chart_confidence=chart.confidence,
        messages=messages,
        bot_vendor=bot.vendor,
        bot_signals=bot.signals,
        text_length=text_length,
        final_url=final_url,
        title=title,
        chart=chart,
    )
