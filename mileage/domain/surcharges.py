"""Estimated award taxes / carrier surcharges from knowledge/surcharges.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

_KNOWLEDGE = Path(__file__).resolve().parent.parent / "knowledge"


def load_surcharges(path: Optional[Path] = None) -> dict:
    p = path or (_KNOWLEDGE / "surcharges.yaml")
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def estimate_taxes_cents(
    program: str,
    *,
    surcharges: Optional[dict] = None,
) -> tuple[int, list[str], float]:
    """Return (taxes_cents, flags, confidence) for a program award.

    Conservative curated estimates — never claimed as live tax quotes.
    """
    data = surcharges if surcharges is not None else load_surcharges()
    defaults = data.get("defaults") or {}
    programs = data.get("programs") or {}
    spec = programs.get(program) or {}
    taxes = int(spec.get("taxes_cents", defaults.get("taxes_cents", 0)))
    conf = float(spec.get("confidence", defaults.get("confidence", 0.4)))
    flags = ["estimated_surcharge"] if taxes else []
    return taxes, flags, conf
