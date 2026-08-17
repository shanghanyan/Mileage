"""Bonus calendar parse + portal floors + surcharge honesty."""

from __future__ import annotations

import os as _os
from datetime import date
from pathlib import Path

_os.environ.setdefault("MILEAGE_OFFLINE", "1")

from mileage.domain.fuel import price_paid
from mileage.domain.models import Cabin, Route, portal_cpp_for
from mileage.providers.bonus_calendar import (
    load_bonus_calendar,
    parse_json_ld_offers,
    parse_offer_name,
)

_KNOWLEDGE = Path(__file__).resolve().parents[1] / "mileage" / "knowledge"
_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_parse_offer_name() -> None:
    assert parse_offer_name(
        "30% transfer bonus from Capital One to EVA Infinity MileageLands"
    ) == ("capital_one", "eva", 1.3)


def test_load_scraped_bonus_calendar() -> None:
    """The live calendar must LOAD and be well-formed — not contain any one promo.

    knowledge/bonus_calendar.yaml is Table 2: a scraper rewrites it weekly.
    Asserting a specific offer here made the suite fail whenever a bonus
    expired. Shape is the real invariant; pinned content lives in
    tests/fixtures/bonus_calendar_pinned.yaml.
    """
    offers = load_bonus_calendar(_KNOWLEDGE / "bonus_calendar.yaml")
    assert offers, "live bonus calendar parsed to zero offers"
    for o in offers:
        assert o.from_currency and o.to_program
        # A magnitude typo (30 meaning +30%) would sail through without this.
        assert 1.0 < o.bonus_multiplier <= 2.0, f"implausible bonus {o!r}"


def test_pinned_bonus_calendar_fixture() -> None:
    """Content assertions run against the pinned fixture, which never refreshes."""
    offers = load_bonus_calendar(_FIXTURES / "bonus_calendar_pinned.yaml")
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


def test_avios_surcharge_depends_on_operating_carrier() -> None:
    """The whole point of §4.4: one currency, two carriers, two prices.

    The predecessor asserted a single per-program number for Avios, which is
    the exact modeling error that made Avios-on-JAL price like Avios-on-BA.
    """
    route = Route("LHR", "JFK", Cabin.BUSINESS)
    on_ba = price_paid("avios", "BA", route)
    on_aa = price_paid("avios", "AA", route)

    assert on_ba.fuel_cents > 20000, "BA passes full carrier surcharges"
    assert on_aa.fuel_cents == 0, "AA passes none"
    assert on_ba.total_cents > on_aa.total_cents * 2
    assert "estimated_surcharge" in on_ba.flags, "never claimed as a live quote"
