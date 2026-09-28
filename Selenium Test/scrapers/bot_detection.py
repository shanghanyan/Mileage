"""Akamai / Imperva / DataDome and generic bot-block signals."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

AKAMAI_SIGNALS = ("_abck", "bm_sz", "ak_bmsc", "akamai")
IMPERVA_SIGNALS = (
    "incapsula",
    "_incapsula_resource",
    "visid_incap",
    "pardon our interruption",
)
DATADOME_SIGNALS = (
    "datadome",
    "geo.captcha-delivery.com",
    "dd-cid",
    "ddchallenge",
)
GENERIC_BLOCK_SIGNALS = (
    "unusual traffic",
    "are you a robot",
    "verify you are human",
    "captcha",
    "access denied",
    "request blocked",
    "please enable cookies",
    "attention required! | cloudflare",
    "cf-challenge",
    "just a moment...",
)


@dataclass
class BotDetection:
    blocked: bool
    vendor: str | None = None
    signals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"blocked": self.blocked, "vendor": self.vendor, "signals": self.signals}


def _haystack(html: str, title: str, cookies: Iterable[str], text: str) -> str:
    cookie_blob = " ".join(cookies)
    return f"{title}\n{cookie_blob}\n{text}\n{html}".lower()


def detect_bot_block(
    html: str,
    title: str = "",
    cookies: Iterable[str] | None = None,
    visible_text: str = "",
) -> BotDetection:
    """Return vendor + matching signals if a bot manager appears to have blocked the page."""
    blob = _haystack(html, title, cookies or [], visible_text)
    hits: list[tuple[str, str]] = []

    for signal in AKAMAI_SIGNALS:
        if signal in blob:
            hits.append(("akamai", signal))
    for signal in IMPERVA_SIGNALS:
        if signal in blob:
            hits.append(("imperva", signal))
    for signal in DATADOME_SIGNALS:
        if signal in blob:
            hits.append(("datadome", signal))

    generic = [s for s in GENERIC_BLOCK_SIGNALS if s in blob]

    # Cookie names alone (akamai/datadome) are common on allowed pages.
    # Treat as blocked only with a challenge/interruption marker or generic block copy.
    challenge_markers = {
        "pardon our interruption",
        "access denied",
        "unusual traffic",
        "are you a robot",
        "verify you are human",
        "attention required! | cloudflare",
        "just a moment...",
        "request blocked",
        "ddchallenge",
        "geo.captcha-delivery.com",
        "_incapsula_resource",
    }
    vendor_challenge = [vendor for vendor, signal in hits if signal in challenge_markers]
    generic_challenge = [s for s in generic if s in challenge_markers or s == "captcha"]

    if vendor_challenge or generic_challenge:
        vendor = vendor_challenge[0] if vendor_challenge else None
        if vendor is None and hits:
            vendor = hits[0][0]
        signals = sorted({s for _, s in hits} | set(generic_challenge))
        return BotDetection(blocked=True, vendor=vendor, signals=signals)

    return BotDetection(blocked=False, vendor=hits[0][0] if hits else None, signals=[s for _, s in hits])
