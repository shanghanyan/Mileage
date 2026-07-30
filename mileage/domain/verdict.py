"""conclude_winner — the honesty engine (Cursor-Mileage-Plan.md §7).

Rules (when a portal floor exists):
  - No verified, non-stale transfer path        -> portal_only
  - Best transfer within 20% of portal          -> comparable
  - Best transfer beats portal by >= 20%        -> best
  - ...but if the winner carries a data-quality warning flag -> tentative_best

When no portal floor exists (hotel banks, etc.):
  - Best affordable transfer -> best / tentative_best
  - No transfer -> portal_only with an honest "no portal floor" rationale

`no_live_space` and `single_source` are EXPECTED Phase-0 caveats, so they are
surfaced but do not by themselves downgrade a `best` to `tentative_best`.
"""

from __future__ import annotations

from typing import Optional

from .models import PathOption, Route, Verdict, VerdictLabel
from .prefs import apply_preferences, wait_for_bonus_flags

DEFAULT_THRESHOLD = 0.20

WARNING_FLAGS: frozenset[str] = frozenset(
    {"sources_disagree", "stale", "bounds_violation"}
)


def _has_warning(option: PathOption) -> bool:
    return any(
        f in WARNING_FLAGS or f.startswith("sources_disagree")
        for f in option.flags
    )


def conclude_winner(
    route: Route,
    portal: Optional[PathOption],
    transfers: list[PathOption],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    preferences: Optional[dict[str, str]] = None,
    recent_bonus_programs: Optional[list[str]] = None,
) -> Verdict:
    """Compare the portal floor (if any) against the best affordable transfer."""
    ranked = apply_preferences(
        ([portal] if portal else []) + list(transfers),
        preferences,
    )
    affordable = [
        t for t in ranked if t.kind == "transfer" and t.affordable and t.cpp > 0
    ]
    all_options = ranked

    best_transfer: Optional[PathOption] = (
        max(affordable, key=lambda t: t.cpp) if affordable else None
    )

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
                "No verified transfer path and no portal floor for this currency. "
                "Add a transferable balance or try another card."
            )
            return Verdict(
                label=VerdictLabel.PORTAL_ONLY,
                route=route,
                portal=None,
                best_transfer=None,
                options=all_options,
                rationale=rationale,
                flags=["no_portal_floor"],
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
        )

    if portal is None or portal.cpp <= 0:
        label = (
            VerdictLabel.TENTATIVE_BEST
            if _has_warning(best_transfer)
            else VerdictLabel.BEST
        )
        rationale = (
            f"{best_transfer.label} is the best verified route at "
            f"{best_transfer.cpp:.2f}c/pt (no portal floor for this currency)."
        )
        return Verdict(
            label=label,
            route=route,
            portal=None,
            best_transfer=best_transfer,
            options=all_options,
            rationale=rationale,
            flags=sorted(set(best_transfer.flags) | set(extra) | {"no_portal_floor"}),
        )

    ratio = best_transfer.cpp / portal.cpp
    flags = sorted(set(best_transfer.flags) | set(extra))

    if ratio >= 1.0 + threshold:
        label = (
            VerdictLabel.TENTATIVE_BEST
            if _has_warning(best_transfer)
            else VerdictLabel.BEST
        )
        rationale = (
            f"{best_transfer.label} returns {best_transfer.cpp:.2f}c/pt vs the "
            f"{portal.cpp:.2f}c/pt portal floor "
            f"({(ratio - 1) * 100:.0f}% better). Transfer wins."
        )
    elif ratio >= 1.0 - threshold:
        label = VerdictLabel.COMPARABLE
        rationale = (
            f"{best_transfer.label} ({best_transfer.cpp:.2f}c/pt) is within "
            f"{threshold * 100:.0f}% of the portal floor "
            f"({portal.cpp:.2f}c/pt). Weigh availability and fees."
        )
    else:
        label = VerdictLabel.PORTAL_ONLY
        rationale = (
            f"Best transfer ({best_transfer.cpp:.2f}c/pt) is well below the "
            f"portal floor ({portal.cpp:.2f}c/pt). Portal is your floor."
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
    )
