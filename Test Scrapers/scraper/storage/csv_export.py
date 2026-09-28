"""Export SQLite records to algorithm-ready CSV."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

NA = "N/A"

# Union of all columns across record types (spec schema)
CSV_COLUMNS = [
    "record_type",
    # award_flight
    "origin_iata",
    "destination_iata",
    "cabin",
    "miles_cost",
    "taxes_fees_usd",
    "fuel_surcharge_usd",
    "program",
    "partner_operated",
    "partner_airline",
    "is_saver_award",
    "one_way_roundtrip",
    "stopover_allowed",
    # cash_flight
    "price_usd",
    "fare_class",
    "refundable",
    "flight_number",
    "airline",
    "departure_datetime",
    "arrival_datetime",
    "duration_minutes",
    "stops",
    "source",
    # transfer_partner
    "source_currency",
    "destination_program",
    "transfer_ratio",
    "transfer_bonus_active",
    "transfer_bonus_pct",
    "transfer_bonus_expiry",
    "min_transfer_units",
    "transfer_time_hours",
    # credit_card
    "card_name",
    "issuer",
    "points_currency",
    "annual_fee_usd",
    "signup_bonus_points",
    "signup_spend_usd",
    "signup_window_days",
    "earn_rate_base",
    "earn_rate_travel",
    "earn_rate_dining",
    "lounge_access",
    "lounge_network",
    "free_checked_bag",
    "priority_boarding",
    # lounge
    "airport_iata",
    "terminal",
    "lounge_name",
    "operated_by",
    "network",
    "access_via_cards",
    "access_via_ticket",
    "has_showers",
    "has_hot_food",
    "has_bar",
    "has_wifi",
    "has_quiet_zone",
    "hours",
    # route_metadata
    "aircraft_type",
    "has_lie_flat_business",
    "has_premium_economy",
    "has_seatback_ife",
    "meal_service",
    "avg_duration_minutes",
    "frequency",
    # layover
    "airport_name",
    "has_united_club",
    "has_delta_sky_club",
    "has_admirals_club",
    "has_priority_pass_lounge",
    "min_connection_minutes",
    "recommended_connection_minutes",
    "requires_terminal_transit",
    "requires_immigration",
    # program_rule
    "rule_topic",
    "rule_summary",
    # points_valuation
    "cents_per_point",
    "valuation_date",
    # common
    "source_url",
    "extracted_at",
    "dom_success",
    "vision_success",
]


def export_csv(records: list[dict[str, Any]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for rec in records:
        row = {col: NA for col in CSV_COLUMNS}
        for key, value in rec.items():
            if key in row:
                row[key] = NA if value is None or value == "" else value
        rows.append(row)

    df = pd.DataFrame(rows, columns=CSV_COLUMNS)
    df = df.fillna(NA)
    df.to_csv(output_path, index=False)
