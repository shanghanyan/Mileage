"""conclude_winner — the honesty engine (§7).

Rules, when a portal floor and a market fare both exist:
  - No verified, non-stale transfer path        -> portal_only
  - Best transfer within 20% of portal          -> comparable
  - Best transfer beats portal by >= 20%        -> best
  - ...but if the winner carries a data-quality warning flag -> tentative_best

When there is no portal floor OR no market fare (the common case now that §1
puts live fares out of scope), cents-per-point is undefined and there is
nothing to compare against. The verdict then reports the best *bookable* route
on points and price paid, and says so.

Every verdict carries a `reason` naming the rule that produced it. The sweep
review could not reconstruct `best` vs `tentative_best` from twelve days of
output — which means no user could either. A rule a reader can't infer from the
data has to be stated outright.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import AwardSpace, PathOption, Route, Verdict, VerdictLabel
from .prefs import apply_preferences, wait_for_bonus_flags

DEFAULT_THRESHOLD = 0.20

WARNING_FLAGS: frozenset[str] = frozenset(
    {"sources_disagree", "stale", "bounds_violation"}
)


def _has_warning(option: PathOption) -> tuple[bool, list[str]]:
    """Data-quality warnings that downgrade `best` to `tentative_best`.

    `space_unknown` and `single_source` are EXPECTED caveats and do not
    downgrade on their own — but they ARE named in the reason, so a reader can
    tell which caveats applied and which one moved the label.
    """
    hits = [
        f
        for f in option.flags
        if f in WARNING_FLAGS or f.startswith("sources_disagree")
    ]
    return bool(hits), sorted(hits)


def _label_for(option: PathOption) -> tuple[VerdictLabel, str]:
    """Pick best vs tentative_best AND say which flag decided it."""
    warned, hits = _has_warning(option)
    if warned:
        return (
            VerdictLabel.TENTATIVE_BEST,
            f"tentative because the winning route carries {', '.join(hits)}",
        )
    return VerdictLabel.BEST, "no data-quality warnings on the winning route"


def _describe(option: PathOption) -> str:
    """Points + price paid, the two numbers that are always known."""
    bits = [f"{option.source_points:,} pts", f"${option.price_paid_usd:,.0f}"]
    if option.cpp is not None:
        bits.append(f"{option.cpp:.2f}c/pt")
    return " · ".join(bits)


def conclude_winner(
    route: Route,
    portal: Optional[PathOption],
    transfers: list[PathOption],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    preferences: Optional[dict[str, str]] = None,
    recent_bonus_programs: Optional[list[str]] = None,
    degraded: bool = False,
    ranked_options: Optional[list[PathOption]] = None,
) -> Verdict:
    """Compare the portal floor (if any) against the best affordable transfer.

    `ranked_options` is the already-§6.1-ordered list including the portal row.
    Pass it whenever you have it: rebuilding the display list as
    `[portal] + transfers` pins the portal to the top regardless of how it
    actually ranks, which is how a 12,640-point portal row sat above a
    5,000-point transfer.
    """
    ranked = apply_preferences(
        list(ranked_options)
        if ranked_options is not None
        else (([portal] if portal else []) + list(transfers)),
        preferences,
    )
    affordable = [t for t in ranked if t.kind == "transfer" and t.affordable]
    all_options = ranked

    # With a fare, "best" means best value per point. Without one, cpp is
    # undefined for every row, so the winner is the cheapest bookable route —
    # fewest points, then least cash. That is exactly rank_options' ordering,
    # so the first affordable transfer already IS the winner.
    def _best(rows: list[PathOption]) -> Optional[PathOption]:
        if not rows:
            return None
        priced = [r for r in rows if r.cpp is not None and r.cpp > 0]
        if priced:
            return max(priced, key=lambda t: t.cpp or 0.0)
        return rows[0]

    best_transfer = _best(affordable)

    portal_cpp = portal.cpp if portal else None
    extra = wait_for_bonus_flags(
        best_transfer,
        portal_cpp,
        recent_bonus_programs=recent_bonus_programs or [],
        threshold=threshold,
    )

    if best_transfer is None:
        if portal is None:
            rationale = (
                "No bookable transfer route found for this route and cabin, and "
                "this currency has no travel-portal floor. Nothing to recommend "
                "without inventing one."
            )
            return Verdict(
                label=VerdictLabel.PORTAL_ONLY,
                route=route,
                portal=None,
                best_transfer=None,
                options=all_options,
                rationale=rationale,
                flags=["no_portal_floor"],
                reason="no affordable transfer path and no portal floor",
            )
        rationale = (
            "No verified transfer path beats your portal floor "
            f"({portal.cpp:.2f}c/pt). Just use your portal."
        )
        return Verdict(
            label=VerdictLabel.PORTAL_ONLY,
            route=route,
            portal=portal,
            best_transfer=None,
            options=all_options,
            rationale=rationale,
            flags=sorted(set(portal.flags) | set(extra)),
            reason="no affordable transfer path cleared the portal floor",
        )

    # No basis for comparison: no portal floor, or no market fare to price
    # cents-per-point against. Report the cheapest bookable route and say why
    # there is no comparison rather than manufacturing one.
    if portal is None or portal.cpp is None or portal.cpp <= 0 or best_transfer.cpp is None:
        label, why = _label_for(best_transfer)
        basis = (
            "no market fare was available, so cents-per-point is undefined"
            if best_transfer.cpp is None or degraded
            else "this currency has no travel-portal floor"
        )
        rationale = (
            f"{best_transfer.label} is the cheapest bookable route at "
            f"{_describe(best_transfer)} — {basis}."
        )
        flags = set(best_transfer.flags) | set(extra)
        if portal is None:
            flags.add("no_portal_floor")
        if degraded:
            flags.add("degraded_no_fare")
        return Verdict(
            label=label,
            route=route,
            portal=None,
            best_transfer=best_transfer,
            options=all_options,
            rationale=rationale,
            flags=sorted(flags),
            reason=f"ranked on points then cash ({basis}); {why}",
        )

    ratio = best_transfer.cpp / portal.cpp
    flags = sorted(set(best_transfer.flags) | set(extra))

    if ratio >= 1.0 + threshold:
        label, why = _label_for(best_transfer)
        rationale = (
            f"{best_transfer.label} returns {best_transfer.cpp:.2f}c/pt vs the "
            f"{portal.cpp:.2f}c/pt portal floor "
            f"({(ratio - 1) * 100:.0f}% better). Transfer wins."
        )
        reason = (
            f"transfer beats portal by {(ratio - 1) * 100:.0f}% "
            f"(threshold {threshold * 100:.0f}%); {why}"
        )
    elif ratio >= 1.0 - threshold:
        label = VerdictLabel.COMPARABLE
        rationale = (
            f"{best_transfer.label} ({best_transfer.cpp:.2f}c/pt) is within "
            f"{threshold * 100:.0f}% of the portal floor "
            f"({portal.cpp:.2f}c/pt). Weigh availability and fees."
        )
        reason = (
            f"transfer is within ±{threshold * 100:.0f}% of the portal floor "
            f"({ratio:.2f}×)"
        )
    else:
        label = VerdictLabel.PORTAL_ONLY
        rationale = (
            f"Best transfer ({best_transfer.cpp:.2f}c/pt) is well below the "
            f"portal floor ({portal.cpp:.2f}c/pt). Portal is your floor."
        )
        reason = (
            f"best transfer is {(1 - ratio) * 100:.0f}% below the portal floor"
        )

    if extra and label in (VerdictLabel.COMPARABLE, VerdictLabel.PORTAL_ONLY):
        prog = extra[0].split(":", 1)[-1]
        rationale += (
            f" Tip: {prog.replace('_', ' ')} recently had a transfer bonus — "
            "waiting for the next promo may beat today's verdict."
        )

    return Verdict(
        label=label,
        route=route,
        portal=portal,
        best_transfer=best_transfer,
        options=all_options,
        rationale=rationale,
        flags=flags,
        reason=reason,
    )
