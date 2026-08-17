"""Preference re-ranking + wait-for-bonus signals."""

from __future__ import annotations

from typing import Optional

from .models import PathOption


def apply_preferences(
    options: list[PathOption],
    preferences: Optional[dict[str, str]] = None,
) -> list[PathOption]:
    """Filter / boost paths from user prefs. Never invents award space.

    Recognized keys (stringy truthy: "1", "true", "yes"):
      - nonstop_only: drop paths lacking a nonstop/direct flag when any path
        has one; otherwise no-op (we often lack schedule data).
      - prefer_morning: boost paths flagged morning (soft re-rank).
      - alliance: preferred alliance id (star_alliance / skyteam / oneworld);
        soft-boost matching paths.

    Ordering is NOT this function's job. It arrives already ranked by §6.1
    (domain/rank.py) and every re-rank here is a STABLE partition, so a
    preference promotes matching rows without scrambling the ranking beneath
    them. The previous version re-sorted everything by `-o.cpp`, which silently
    discarded the cabin/cash/points ordering and raised on the None cpp that a
    fare-free route now legitimately carries.
    """
    prefs = {k: str(v).lower() for k, v in (preferences or {}).items()}

    def _truthy(key: str) -> bool:
        return prefs.get(key, "") in {"1", "true", "yes", "on"}

    def _promote(rows: list[PathOption], match) -> list[PathOption]:
        """Stable partition: matches first, everything else in original order."""
        yes = [o for o in rows if match(o)]
        no = [o for o in rows if not match(o)]
        return yes + no

    out = list(options)
    if _truthy("nonstop_only"):
        nonstop = [
            o
            for o in out
            if o.kind == "portal"
            or any(f in {"nonstop", "direct", "only_direct"} for f in o.flags)
        ]
        # Only filter if we actually have nonstop-tagged transfer data.
        if any(o.kind == "transfer" for o in nonstop):
            out = nonstop

    alliance = prefs.get("alliance") or prefs.get("prefer_alliance")
    if alliance:
        flag = f"alliance:{alliance}"
        out = _promote(out, lambda o: flag in o.flags or o.kind == "portal")

    if _truthy("prefer_morning"):
        out = _promote(out, lambda o: "morning" in o.flags)
    return out


def wait_for_bonus_flags(
    best: Optional[PathOption],
    portal_cpp: Optional[float],
    *,
    recent_bonus_programs: list[str],
    threshold: float = 0.20,
) -> list[str]:
    """Suggest waiting when a near-miss path partners with recent promo history."""
    if not recent_bonus_programs or best is None:
        return []
    if portal_cpp and portal_cpp > 0 and best.cpp is not None:
        if best.cpp >= portal_cpp * (1.0 + threshold):
            return []  # already a clear win
    prog = best.program
    if prog and prog in recent_bonus_programs:
        return [f"wait_for_bonus:{prog}"]
    # Also flag if any recent-bonus program is among options near the top.
    return []
