"""Load hardcoded fallback rates when scrapers fail."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from scrapers.base import ScrapedRow

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
FALLBACK_PATH = ROOT / "data" / "fallback_rates.json"

# Route-specific fallback key mapping for JFK-NRT business
ROUTE_FALLBACK_KEYS: dict[str, dict[tuple[str, str, str], str]] = {
    "lifemiles": {("JFK", "NRT", "business"): "NA_to_North_Asia_J"},
    "aeroplan": {("JFK", "NRT", "business"): "JFK_to_NRT_J_United"},
    "turkish_miles": {("JFK", "NRT", "business"): "NA_to_Far_East_J_partner"},
    "ana_mileage": {("JFK", "NRT", "business"): "JFK_to_NRT_J_own_metal_one_way"},
    "krisflyer": {("JFK", "NRT", "business"): "JFK_to_NRT_J_via_SIN"},
    "avios": {("JFK", "NRT", "business"): "JFK_to_NRT_J_JAL"},
    "asia_miles": {("JFK", "NRT", "business"): "JFK_to_NRT_J_JAL"},
    "jal": {("JFK", "NRT", "business"): "JFK_to_NRT_J_own_metal"},
    "qatar": {("JFK", "NRT", "business"): "JFK_to_NRT_J_JAL"},
    "qantas": {("JFK", "NRT", "business"): "JFK_to_NRT_J_JAL"},
    "eva_air": {("JFK", "NRT", "business"): "JFK_to_NRT_J_via_TPE"},
}


def load_fallback_rates(path: Path | None = None) -> dict:
    p = path or FALLBACK_PATH
    try:
        with open(p) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def fallback_rows_for_route(
    origin: str,
    dest: str,
    cabin: str,
    programs: list[str] | None = None,
) -> list[ScrapedRow]:
    """Create low-trust ScrapedRows from fallback_rates.json for a route."""
    data = load_fallback_rates()
    now = datetime.now(timezone.utc)
    meta_date = data.get("_meta", {}).get("last_updated")
    try:
        verified = datetime.fromisoformat(meta_date).replace(tzinfo=timezone.utc) if meta_date else None
    except ValueError:
        verified = None

    rows: list[ScrapedRow] = []
    route_key = (origin.upper(), dest.upper(), cabin)

    for program, key_map in ROUTE_FALLBACK_KEYS.items():
        if programs and program not in programs:
            continue
        fb_key = key_map.get(route_key)
        if not fb_key:
            continue
        entry = data.get(program, {}).get(fb_key)
        if not entry:
            continue

        miles = entry.get("miles") or entry.get("partner_miles")
        if miles is None:
            continue

        cabin_field = f"{cabin}_miles"
        kwargs = {
            "economy_miles": None,
            "business_miles": None,
            "first_miles": None,
        }
        kwargs[cabin_field] = miles

        # Map to program-specific zones when available
        from graph.zones import load_zone_mapping
        zm = load_zone_mapping()
        origin_zone = zm.get(program, {}).get(origin.upper(), "North America")
        dest_zone = zm.get(program, {}).get(dest.upper(), "North Asia")

        trust = entry.get("trust", 0.30)
        rows.append(
            ScrapedRow(
                source_name="fallback_rates.json",
                source_url=entry.get("source", str(FALLBACK_PATH)),
                scraped_at=now,
                source_updated_at=verified,
                source_trust=trust,
                from_program=program,
                to_program="award",
                origin_zone=origin_zone,
                destination_zone=dest_zone,
                miles_range_low=entry.get("range_low"),
                miles_range_high=entry.get("range_high"),
                **kwargs,
                raw_cell_text=entry.get("note", ""),
                selector_matched=True,
                confidence="low",
                flags=["hardcoded_fallback", f"trust={trust}"],
            )
        )
        log.info("Using fallback rate for %s: %d miles (trust=%.2f)", program, miles, trust)

    return rows
