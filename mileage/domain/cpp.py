"""Cents-per-point math, with per-hop compounding and award-tax netting.

CPP (cents per point) = net cash value unlocked (in cents) / source points spent.
Higher is better. Award taxes/surcharges reduce net value so Avios/BA-style
awards don't look better than they feel at booking time.
"""

from __future__ import annotations

import math
from typing import Iterable, Optional


def cpp(cash_cents: int, source_points: int, *, taxes_cents: int = 0) -> float:
    """Cents of *net* value per source point. Returns 0.0 if no points spent."""
    if source_points <= 0:
        return 0.0
    net = max(0, cash_cents - max(0, taxes_cents))
    return net / source_points


def portal_points_needed(cash_cents: int, portal_cpp: float) -> int:
    """Points to cover a cash fare at the fixed portal rate."""
    if portal_cpp <= 0:
        return math.inf  # type: ignore[return-value]
    return math.ceil(cash_cents / portal_cpp)


def source_points_for_award(program_miles: int, ratio: float) -> int:
    """Source points needed to acquire `program_miles` at `ratio`.

    ratio = program points per 1 source point. C1 -> Turkish is 1:1, so 45,000
    Turkish miles costs 45,000 C1 miles. A 2:1.5 transfer bonus would lower it.
    """
    if ratio <= 0:
        return math.inf  # type: ignore[return-value]
    return math.ceil(program_miles / ratio)


def compound_ratio(ratios: Iterable[float]) -> float:
    """Multiply transfer ratios across hops (e.g. C1 -> A -> B)."""
    total = 1.0
    for r in ratios:
        total *= r
    return total


def transfer_cpp(
    cash_cents: int,
    program_miles: int,
    ratio: float,
    *,
    taxes_cents: int = 0,
) -> float:
    """End-to-end CPP for a single-currency transfer redemption."""
    pts = source_points_for_award(program_miles, ratio)
    return cpp(cash_cents, pts, taxes_cents=taxes_cents)


def net_value_cents(cash_cents: int, taxes_cents: Optional[int] = None) -> int:
    return max(0, cash_cents - max(0, taxes_cents or 0))
