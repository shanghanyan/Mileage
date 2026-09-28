"""Load and filter Capital One transfer partners from config/partners.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PARTNERS_PATH = ROOT / "config" / "partners.yaml"

TRANSATLANTIC = {"JFK", "LAX", "ORD", "SFO", "IAD", "MIA", "BOS", "SEA", "EWR", "ATL"}
NORTH_ASIA = {"NRT", "HND", "ICN", "PEK", "PVG", "HKG", "TPE", "SIN"}


def load_partners_config(path: Path | None = None) -> dict:
    p = path or PARTNERS_PATH
    with open(p) as f:
        return yaml.safe_load(f)


def get_airline_partners(config: dict | None = None) -> list[dict]:
    cfg = config or load_partners_config()
    return cfg.get("airlines", [])


def partner_by_id(partners: list[dict]) -> dict[str, dict]:
    return {p["id"]: p for p in partners}


def is_transpacific_route(origin: str, dest: str) -> bool:
    o, d = origin.upper(), dest.upper()
    return (o in TRANSATLANTIC and d in NORTH_ASIA) or (o in NORTH_ASIA and d in TRANSATLANTIC)


def filter_relevant_partners(
    partners: list[dict],
    origin: str,
    dest: str,
    *,
    show_all: bool = False,
    nonstop_only: bool = False,
) -> list[dict]:
    if show_all:
        return partners

    result: list[dict] = []
    transpacific = is_transpacific_route(origin, dest)

    for p in partners:
        if transpacific and not p.get("relevant_for_transpacific", True):
            continue
        if nonstop_only and p.get("routing_type") == "connecting":
            continue
        result.append(p)

    return result


def c1_miles_required(partner_miles: int, effective_ratio: float) -> int:
    """C1 miles needed = ceil(partner_miles / effective_ratio)."""
    import math
    if effective_ratio <= 0:
        return partner_miles
    return math.ceil(partner_miles / effective_ratio)
