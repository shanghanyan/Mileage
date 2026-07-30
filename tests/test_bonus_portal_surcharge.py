"""Bonus calendar parse + portal floors + surcharge honesty."""

from __future__ import annotations

import os as _os
from datetime import date
from pathlib import Path

_os.environ.setdefault("MILEAGE_OFFLINE", "1")

from mileage.domain.models import portal_cpp_for
from mileage.domain.surcharges import estimate_taxes_cents
from mileage.providers.bonus_calendar import (
    load_bonus_calendar,
    parse_json_ld_offers,
    parse_offer_name,
)

_KNOWLEDGE = Path(__file__).resolve().parents[1] / "mileage" / "knowledge"


def test_parse_offer_name() -> None:
    assert parse_offer_name(
        "30% transfer bonus from Capital One to EVA Infinity MileageLands"
    ) == ("capital_one", "eva", 1.3)


def test_load_scraped_bonus_calendar() -> None:
    offers = load_bonus_calendar(_KNOWLEDGE / "bonus_calendar.yaml")
    assert any(
        o.from_currency == "capital_one" and o.to_program == "eva" for o in offers
    )


def test_json_ld_parse_sample() -> None:
    html = """
    <script type="application/ld+json">
    {"@type":"Offer","name":"30% transfer bonus from Capital One to EVA Infinity MileageLands",
     "validFrom":"2026-07-01","validThrough":"2026-07-31","category":"Points transfer bonus"}
    </script>
    """
    offers = parse_json_ld_offers(html, today=date(2026, 7, 20))
    assert len(offers) == 1
    assert offers[0].bonus_multiplier == 1.3
    assert offers[0].status == "active"


def test_portal_floors_by_currency() -> None:
    assert portal_cpp_for("capital_one", "venture_x") == 1.25
    assert portal_cpp_for("chase_ur") == 1.0
    assert portal_cpp_for("amex_mr") == 1.0
    assert portal_cpp_for("bilt") == 1.25
    assert portal_cpp_for("marriott_bonvoy") is None


def test_avios_surcharge_estimate() -> None:
    taxes, flags, _ = estimate_taxes_cents("avios")
    assert taxes >= 20000
    assert "estimated_surcharge" in flags
