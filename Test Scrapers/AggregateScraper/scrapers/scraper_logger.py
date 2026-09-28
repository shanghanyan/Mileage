"""Scraper failure logging and per-program success tracking."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILURES_PATH = ROOT / "logs" / "scraper_failures.jsonl"

_run_stats = {"succeeded": 0, "failed": 0}
_program_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"attempted": 0, "succeeded": 0})


def reset_run_stats() -> None:
    _run_stats["succeeded"] = 0
    _run_stats["failed"] = 0
    _program_stats.clear()


def record_attempt(program_id: str) -> None:
    _program_stats[program_id]["attempted"] += 1


def record_success(program_id: str | None = None) -> None:
    _run_stats["succeeded"] += 1
    if program_id:
        _program_stats[program_id]["succeeded"] += 1


def record_failure(
    program_id: str,
    url: str,
    error_type: str,
    *,
    http_status: int | None = None,
    response_snippet: str = "",
) -> None:
    _run_stats["failed"] += 1
    FAILURES_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "program_id": program_id,
        "url": url,
        "error_type": error_type,
        "http_status": http_status,
        "response_snippet": response_snippet[:500],
    }
    with open(FAILURES_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def program_summary() -> dict[str, dict[str, int]]:
    return {k: dict(v) for k, v in sorted(_program_stats.items())}


def run_summary() -> str:
    s, f = _run_stats["succeeded"], _run_stats["failed"]
    total = s + f
    if total == 0:
        return ""
    lines = [f"[scraper] {s}/{total} sources succeeded · {f} failed"]
    if f:
        lines[0] += f" (see {FAILURES_PATH.relative_to(ROOT)})"

    by_prog = program_summary()
    if by_prog:
        lines.append(f"{'Program':<16} {'Attempted':>10} {'Succeeded':>10}")
        for prog, stats in by_prog.items():
            lines.append(
                f"{prog:<16} {stats['attempted']:>10} {stats['succeeded']:>10}"
            )
    return "\n".join(lines)
