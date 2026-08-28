"""JSON serialization for API + CLI output.

The shape follows §6.2: every row leads with what the user actually pays —
points and price paid — and cents-per-point is an optional extra that appears
only when a market fare existed to compute it from. `cpp: null` is a real,
meaningful value here; it is not the same as `cpp: 0`.
"""

from __future__ import annotations

from typing import Optional

from .domain.models import AwardSpace, PathOption, Route, Verdict
from .verify.crosscheck import VerifiedAward, VerifiedFare


def _option_to_dict(o: PathOption) -> dict:
    return {
        "label": o.label,
        "kind": o.kind,
        # Always known.
        "source_points": o.source_points,
        "price_paid_cents": o.price_paid_cents,
        "price_paid_usd": round(o.price_paid_usd, 2),
        "taxes_cents": o.taxes_cents,
        "fuel_cents": o.fuel_cents,
        "fuel_policy": o.fuel_policy,
        # Only when a market fare existed. null != 0.
        "cpp": round(o.cpp, 2) if o.cpp is not None else None,
        "cash_cents": o.cash_cents or None,
        "program": o.program,
        "operating_carrier": o.operating_carrier,
        "carrier_name": o.carrier_name,
        "cabin": o.cabin,
        "space": o.space.value,
        "space_label": o.space.label,
        "transfer_hops": o.transfer_hops,
        "settlement_minutes": o.settlement_minutes,
        # source_points = award_miles / effective_ratio. Emitted so a reader can
        # re-derive the headline number instead of trusting it.
        "hop_ratios": [round(r, 6) for r in o.hop_ratios],
        "effective_ratio": o.effective_ratio,
        "affordable": o.affordable,
        "shortfall_points": o.shortfall_points or None,
        "confidence": o.confidence,
        "flags": o.flags,
        "reason": o.reason,
        "gates": [
            {
                "kind": g.kind.value,
                "description": g.describe(),
                "card_ids": list(g.card_ids),
                "annual_fee_usd": g.annual_fee_usd,
                "approval_days": g.approval_days,
            }
            for g in o.gates
        ],
        "currency": o.currency,
    }


def quote_result_to_dict(result: dict) -> dict:
    route: Route = result["route"]
    out: dict = {"route": route.key()}
    verdict: Optional[Verdict] = result.get("verdict")
    if verdict is None:
        out["error"] = result.get("error")
        out["message"] = result.get("message")
        return out

    fare: Optional[VerifiedFare] = result.get("fare")
    awards: list[VerifiedAward] = result.get("awards") or []

    out["verdict"] = verdict.label.value
    out["rationale"] = verdict.rationale
    out["reason"] = verdict.reason
    out["flags"] = verdict.flags

    # A missing fare is a degraded column, not a failed run (§1).
    out["degraded"] = bool(result.get("degraded"))
    out["fare_cents"] = fare.cash_cents if fare else None
    out["fare_flags"] = fare.flags if fare else ["no_market_fare"]
    out["fare_confidence"] = fare.confidence if fare else None

    # Three states, reported separately. `space_checked=false` means nothing
    # looked — which is NOT the same as looking and finding nothing.
    out["space_checked"] = bool(result.get("space_checked"))
    out["award_space"] = {
        "confirmed": [
            {
                "program": a.program,
                "miles": a.miles,
                "seats_available": a.seats_available,
                "carrier": a.operating_carrier,
            }
            for a in awards
            if a.space is AwardSpace.CONFIRMED
        ],
        "checked_none": sorted(
            {a.program for a in awards if a.space is AwardSpace.NONE}
        ),
        "unknown": sorted({a.program for a in awards if a.space is AwardSpace.UNKNOWN}),
    }
    # Back-compat key for existing UI/log readers.
    out["live_award_space"] = [
        {
            "program": a.program,
            "miles": a.miles,
            "seats_available": a.seats_available,
            "flags": a.flags,
        }
        for a in awards
        if a.seats_available is not None
    ]

    out["carriers_serving"] = result.get("carriers") or []
    out["coverage"] = result.get("coverage") or {}
    out["snapshot"] = result.get("snapshot")
    # §6.1 caps the list at ten. Report the cap rather than letting a truncated
    # list read as an exhaustive one.
    out["options_considered"] = result.get("options_considered")
    out["options_shown"] = result.get("options_shown")
    # Priced, then dropped for exceeding the reach multiple. A count, because a
    # dropped row nobody counts is indistinguishable from one that never
    # existed — which is how a route with 8 valid quotes reported "no bookable
    # option" for twelve days.
    out["options_out_of_reach"] = result.get("options_out_of_reach") or 0
    # §4.1 requirements anyone can satisfy: open a card, open an airline
    # account. Never a filter, always reported.
    out["easy_unlocks"] = result.get("easy_unlocks") or []

    out["options"] = [_option_to_dict(o) for o in verdict.options]
    # §6.3 — never hidden, just separated so the UI can offer them as
    # "3 routes require a Sapphire card ›".
    out["gated_options"] = [_option_to_dict(o) for o in verdict.gated]
    out["gated_summary"] = _gated_summary(verdict.gated)

    if verdict.best_transfer:
        out["best_transfer"] = _option_to_dict(verdict.best_transfer)
    out["portal_cpp"] = (
        round(verdict.portal.cpp, 2)
        if verdict.portal is not None and verdict.portal.cpp is not None
        else None
    )
    return out


def _gated_summary(gated: list[PathOption]) -> list[dict]:
    """One line per distinct requirement: "3 routes require a Sapphire card"."""
    by_desc: dict[str, dict] = {}
    for o in gated:
        for g in o.gates:
            entry = by_desc.setdefault(
                g.describe(),
                {
                    "requirement": g.describe(),
                    "kind": g.kind.value,
                    "routes": 0,
                    "annual_fee_usd": g.annual_fee_usd,
                    "approval_days": g.approval_days,
                },
            )
            entry["routes"] += 1
    return sorted(by_desc.values(), key=lambda e: -e["routes"])
