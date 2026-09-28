"""ANA round-trip vs one-way pricing detection."""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

RT_SIGNALS = [
    r"\bround[- ]trip\b",
    r"\bRT\b",
    r"\breturn\b",
    r"\bround ?trip\b",
]
OW_SIGNALS = [
    r"\bone[- ]way\b",
    r"\bOW\b",
    r"\beach way\b",
    r"\bone direction\b",
]


def detect_pricing_basis(context_text: str) -> str:
    """Returns 'RT', 'OW', or 'UNKNOWN'. Default for ANA is RT unless OW signals present."""
    text = context_text.lower()
    ow_hits = sum(1 for p in OW_SIGNALS if re.search(p, text, re.I))
    rt_hits = sum(1 for p in RT_SIGNALS if re.search(p, text, re.I))

    if ow_hits > rt_hits:
        return "OW"
    if rt_hits > ow_hits:
        return "RT"
    return "RT"


def normalize_to_one_way(miles: int, basis: str) -> int:
    if basis == "RT":
        return miles // 2
    return miles


def get_ow_miles(
    miles: int,
    context_text: str,
    *,
    rt_ow_context: str | None = None,
) -> tuple[int, str]:
    """
    Normalize miles to one-way. Returns (one_way_miles, basis_used).
    rt_ow_context from scrape_targets.yaml takes priority over regex detection.
    """
    detected = detect_pricing_basis(context_text)
    if rt_ow_context == "round_trip":
        basis = "RT"
    elif rt_ow_context == "one_way":
        basis = "OW"
    else:
        basis = detected

    result = normalize_to_one_way(miles, basis)
    log.debug("ANA pricing: %d miles, basis=%s (detected=%s) → %d OW", miles, basis, detected, result)
    return result, basis
