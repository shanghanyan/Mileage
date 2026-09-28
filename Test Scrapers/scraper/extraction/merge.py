"""Merge DOM + vision extractions into typed CSV records."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from extraction.prompts import page_category, program_for_domain

NA = "N/A"


def _na(value: Any) -> str:
    if value is None or value == "":
        return NA
    return str(value)


def merge_extractions(vision_data: dict, dom_data: dict) -> dict[str, Any]:
    """Vision fills gaps; DOM takes precedence."""
    merged = {**vision_data, **dom_data}
    # Flatten simple nested keys if both modes returned overlapping names
    for key in ("origin", "destination", "cabin", "miles_cost", "program"):
        if key in vision_data and key not in dom_data and vision_data[key]:
            merged.setdefault(key, vision_data[key])
    return merged


def records_from_merged(
    merged: dict[str, Any],
    url: str,
    dom_success: bool,
    vision_success: bool,
) -> list[dict[str, Any]]:
    """Produce zero or more typed records for SQLite/CSV."""
    now = datetime.now(timezone.utc).isoformat()
    program = program_for_domain(url)
    category = page_category(url)
    records: list[dict[str, Any]] = []

    origin = merged.get("origin") or merged.get("origin_iata")
    dest = merged.get("destination") or merged.get("destination_iata")
    miles = merged.get("miles_cost") or merged.get("miles")
    cabin = merged.get("cabin")

    if miles or (origin and dest and category == "award"):
        records.append(
            {
                "record_type": "award_flight",
                "origin_iata": _na(origin),
                "destination_iata": _na(dest),
                "cabin": _na(cabin),
                "miles_cost": _na(miles),
                "taxes_fees_usd": _na(merged.get("taxes_fees_usd")),
                "fuel_surcharge_usd": _na(merged.get("fuel_surcharge_usd")),
                "program": _na(merged.get("program") or program),
                "partner_operated": _na(merged.get("is_partner_flight")),
                "partner_airline": _na(merged.get("partner_airline")),
                "is_saver_award": NA,
                "one_way_roundtrip": NA,
                "stopover_allowed": NA,
                "source_url": url,
                "extracted_at": now,
                "dom_success": str(dom_success).lower(),
                "vision_success": str(vision_success).lower(),
            }
        )

    price = merged.get("price_usd") or merged.get("cash_cost_usd")
    if price or category == "cash_price":
        source = "google_flights" if "google.com" in url else "kayak" if "kayak.com" in url else program
        records.append(
            {
                "record_type": "cash_flight",
                "origin_iata": _na(origin),
                "destination_iata": _na(dest),
                "cabin": _na(cabin),
                "price_usd": _na(price),
                "fare_class": NA,
                "refundable": NA,
                "flight_number": NA,
                "airline": _na(merged.get("airline")),
                "departure_datetime": _na(merged.get("departure_date")),
                "arrival_datetime": _na(merged.get("arrival_date")),
                "duration_minutes": NA,
                "stops": _na(merged.get("stops")),
                "source": source,
                "source_url": url,
                "extracted_at": now,
                "dom_success": str(dom_success).lower(),
                "vision_success": str(vision_success).lower(),
            }
        )

    if merged.get("transfer_ratio") or merged.get("destination_program") or category == "transfer":
        records.append(
            {
                "record_type": "transfer_partner",
                "source_currency": _na(merged.get("source_currency")),
                "destination_program": _na(merged.get("destination_program")),
                "transfer_ratio": _na(merged.get("transfer_ratio")),
                "transfer_bonus_active": _na(merged.get("transfer_bonus_active")),
                "transfer_bonus_pct": _na(merged.get("transfer_bonus_pct")),
                "transfer_bonus_expiry": _na(merged.get("transfer_bonus_expiry")),
                "min_transfer_units": _na(merged.get("min_transfer_units")),
                "transfer_time_hours": _na(merged.get("transfer_time_hours")),
                "source_url": url,
                "extracted_at": now,
                "dom_success": str(dom_success).lower(),
                "vision_success": str(vision_success).lower(),
            }
        )

    if merged.get("card_name") or merged.get("annual_fee_usd") or "credit-card" in url.lower():
        records.append(
            {
                "record_type": "credit_card",
                "card_name": _na(merged.get("card_name") or merged.get("page_title")),
                "issuer": _na(merged.get("issuer") or program),
                "points_currency": _na(merged.get("points_currency")),
                "annual_fee_usd": _na(merged.get("annual_fee_usd")),
                "signup_bonus_points": _na(merged.get("signup_bonus_points")),
                "signup_spend_usd": NA,
                "signup_window_days": NA,
                "earn_rate_base": NA,
                "earn_rate_travel": NA,
                "earn_rate_dining": NA,
                "lounge_access": NA,
                "lounge_network": NA,
                "free_checked_bag": NA,
                "priority_boarding": NA,
                "source_url": url,
                "extracted_at": now,
                "dom_success": str(dom_success).lower(),
                "vision_success": str(vision_success).lower(),
            }
        )

    if merged.get("lounge_name") or merged.get("airport_iata") or category == "lounge":
        records.append(
            {
                "record_type": "lounge",
                "airport_iata": _na(merged.get("airport_iata")),
                "terminal": _na(merged.get("terminal")),
                "lounge_name": _na(merged.get("lounge_name")),
                "operated_by": _na(merged.get("operated_by")),
                "network": _na(merged.get("network")),
                "access_via_cards": _na(merged.get("access_via_cards")),
                "access_via_ticket": _na(merged.get("access_via_ticket")),
                "has_showers": _na(merged.get("has_showers")),
                "has_hot_food": _na(merged.get("has_hot_food")),
                "has_bar": _na(merged.get("has_bar")),
                "has_wifi": _na(merged.get("has_wifi")),
                "has_quiet_zone": NA,
                "hours": _na(merged.get("hours")),
                "source_url": url,
                "extracted_at": now,
                "dom_success": str(dom_success).lower(),
                "vision_success": str(vision_success).lower(),
            }
        )

    if merged.get("cents_per_point") or category == "valuation":
        records.append(
            {
                "record_type": "points_valuation",
                "program": _na(merged.get("program") or program),
                "cabin": _na(merged.get("cabin")),
                "cents_per_point": _na(merged.get("cents_per_point")),
                "source": _na(merged.get("source_name") or program),
                "valuation_date": _na(merged.get("valuation_date")),
                "source_url": url,
                "extracted_at": now,
                "dom_success": str(dom_success).lower(),
                "vision_success": str(vision_success).lower(),
            }
        )

    if merged.get("rule_summary") or merged.get("rule_topic"):
        summary = str(merged.get("rule_summary", ""))[:300]
        records.append(
            {
                "record_type": "program_rule",
                "program": _na(merged.get("program") or program),
                "rule_topic": _na(merged.get("rule_topic")),
                "rule_summary": _na(summary) if summary else NA,
                "source_url": url,
                "extracted_at": now,
                "dom_success": str(dom_success).lower(),
                "vision_success": str(vision_success).lower(),
            }
        )

    # Always store a route_metadata / page snapshot when we have page title but no other records
    if not records and merged.get("page_title"):
        records.append(
            {
                "record_type": "program_rule",
                "program": _na(program),
                "rule_topic": "page_content",
                "rule_summary": _na(str(merged.get("page_title"))[:300]),
                "source_url": url,
                "extracted_at": now,
                "dom_success": str(dom_success).lower(),
                "vision_success": str(vision_success).lower(),
            }
        )

    return records
