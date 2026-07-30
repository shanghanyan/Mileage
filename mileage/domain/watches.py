"""Saved trip watches — re-check routes when space or bonuses change."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Watch:
    watch_id: str
    user_id: str
    origin: str
    dest: str
    cabin: str = "economy"
    currencies: list[str] = field(default_factory=lambda: ["capital_one"])
    # Snapshot from last check for change detection.
    last_verdict: Optional[str] = None
    last_best_label: Optional[str] = None
    last_best_cpp: Optional[float] = None
    last_live_programs: list[str] = field(default_factory=list)
    last_checked_at: Optional[str] = None
    created_at: str = field(default_factory=_now)
    active: bool = True
    note: str = ""


def watch_changed(before: Watch, result: dict) -> list[str]:
    """Return human reasons the watch fired (empty = no material change)."""
    reasons: list[str] = []
    verdict = result.get("verdict")
    if verdict and verdict != before.last_verdict:
        reasons.append(f"verdict:{before.last_verdict}->{verdict}")
    best = result.get("best_transfer") or {}
    label = best.get("label")
    cpp = best.get("cpp")
    if label and label != before.last_best_label:
        reasons.append(f"best_path:{before.last_best_label}->{label}")
    if (
        cpp is not None
        and before.last_best_cpp is not None
        and abs(float(cpp) - float(before.last_best_cpp)) >= 0.25
    ):
        reasons.append(f"cpp:{before.last_best_cpp}->{cpp}")
    live = [
        row["program"]
        for row in (result.get("live_award_space") or [])
        if row.get("program")
    ]
    new_live = sorted(set(live) - set(before.last_live_programs))
    if new_live:
        reasons.append(f"new_live_space:{','.join(new_live)}")
    flags = result.get("flags") or []
    if any(str(f).startswith("wait_for_bonus") for f in flags):
        reasons.append("wait_for_bonus")
    if any("transfer_bonus" in str(f) for f in flags):
        if "transfer_bonus" not in (before.last_best_label or ""):
            reasons.append("active_transfer_bonus")
    return reasons


def apply_check_result(watch: Watch, result: dict) -> Watch:
    best = result.get("best_transfer") or {}
    live = [
        row["program"]
        for row in (result.get("live_award_space") or [])
        if row.get("program")
    ]
    watch.last_verdict = result.get("verdict")
    watch.last_best_label = best.get("label")
    watch.last_best_cpp = best.get("cpp")
    watch.last_live_programs = live
    watch.last_checked_at = _now()
    return watch


def watch_to_dict(w: Watch) -> dict[str, Any]:
    return {
        "watch_id": w.watch_id,
        "user_id": w.user_id,
        "origin": w.origin,
        "dest": w.dest,
        "cabin": w.cabin,
        "currencies": list(w.currencies),
        "last_verdict": w.last_verdict,
        "last_best_label": w.last_best_label,
        "last_best_cpp": w.last_best_cpp,
        "last_live_programs": list(w.last_live_programs),
        "last_checked_at": w.last_checked_at,
        "created_at": w.created_at,
        "active": w.active,
        "note": w.note,
    }


def watch_from_row(row: dict[str, Any]) -> Watch:
    import json

    currencies = row.get("currencies")
    if isinstance(currencies, str):
        currencies = json.loads(currencies)
    live = row.get("last_live_programs")
    if isinstance(live, str):
        live = json.loads(live)
    return Watch(
        watch_id=str(row["watch_id"]),
        user_id=str(row["user_id"]),
        origin=str(row["origin"]),
        dest=str(row["dest"]),
        cabin=str(row.get("cabin") or "economy"),
        currencies=list(currencies or ["capital_one"]),
        last_verdict=row.get("last_verdict"),
        last_best_label=row.get("last_best_label"),
        last_best_cpp=row.get("last_best_cpp"),
        last_live_programs=list(live or []),
        last_checked_at=row.get("last_checked_at"),
        created_at=str(row.get("created_at") or _now()),
        active=bool(row.get("active", True)),
        note=str(row.get("note") or ""),
    )
