"""Tests for remediation fixes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scrapers.base import ScrapedRow, parse_miles_text
from scrapers.ana_pricing import detect_pricing_basis, normalize_to_one_way, get_ow_miles
from scrapers.http_util import is_bot_blocked
from verify.trust import compute_trust, consensus_rate
from graph.partners import c1_miles_required
from graph.carriers import get_carrier_label, airport_region
from graph.optimizer import compute_cpp


def _row(miles: int, trust: float, field: str = "business_miles") -> ScrapedRow:
    kwargs = {"economy_miles": None, "business_miles": None, "first_miles": None}
    kwargs[field] = miles
    return ScrapedRow(
        source_name="test",
        source_url="http://test",
        scraped_at=datetime.now(timezone.utc),
        from_program="lifemiles",
        to_program="award",
        source_trust=trust,
        selector_matched=True,
        **kwargs,
    )


class TestConsensusRate:
    def test_weighted_median_prefers_high_trust(self):
        stale = [_row(70000, 0.10), _row(70000, 0.10), _row(70000, 0.10)]
        fresh = [_row(90000, 1.00)]
        val, winner = consensus_rate(stale + fresh)
        assert val == 90000
        assert winner.source_trust == 1.00

    def test_single_record(self):
        val, _ = consensus_rate([_row(90000, 0.85)])
        assert val == 90000


class TestParseMiles:
    @pytest.mark.parametrize("text,expected", [
        ("90K", 90000),
        ("90k", 90000),
        ("90,000", 90000),
        ("90000", 90000),
        ("90.0K", 90000),
    ])
    def test_formats(self, text, expected):
        assert parse_miles_text(text) == expected

    def test_range_midpoint_via_prose(self):
        lo = parse_miles_text("85000")
        hi = parse_miles_text("90000")
        assert (lo + hi) // 2 == 87500


class TestComputeTrust:
    def test_none(self):
        assert compute_trust(None) == 0.40

    def test_fresh(self):
        dt = datetime.now(timezone.utc) - timedelta(days=3)
        assert compute_trust(dt) == 1.00

    def test_30_days(self):
        dt = datetime.now(timezone.utc) - timedelta(days=30)
        assert compute_trust(dt) == 1.00

    def test_60_days(self):
        dt = datetime.now(timezone.utc) - timedelta(days=45)
        assert compute_trust(dt) == 0.85

    def test_120_days(self):
        dt = datetime.now(timezone.utc) - timedelta(days=90)
        assert compute_trust(dt) == 0.65

    def test_180_days(self):
        dt = datetime.now(timezone.utc) - timedelta(days=150)
        assert compute_trust(dt) == 0.45

    def test_365_days(self):
        dt = datetime.now(timezone.utc) - timedelta(days=300)
        assert compute_trust(dt) == 0.25

    def test_400_days(self):
        dt = datetime.now(timezone.utc) - timedelta(days=400)
        assert compute_trust(dt) == 0.10


class TestAnaPricing:
    def test_rt_default(self):
        assert detect_pricing_basis("business class award") == "RT"

    def test_ow_signals(self):
        assert detect_pricing_basis("one-way business class") == "OW"

    def test_normalize_rt(self):
        assert normalize_to_one_way(88000, "RT") == 44000

    def test_normalize_ow(self):
        assert normalize_to_one_way(43000, "OW") == 43000

    def test_rt_ow_context_override(self):
        miles, basis = get_ow_miles(88000, "business class", rt_ow_context="round_trip")
        assert miles == 44000
        assert basis == "RT"


class TestIsBotBlocked:
    @pytest.mark.parametrize("sig", [
        "challenge-platform",
        "cf-browser-verification",
        "_cf_chl_",
        "Please Wait... | Cloudflare",
        "Just a moment...",
        "Enable JavaScript and cookies",
        "Access denied",
        "403 Forbidden",
    ])
    def test_signatures(self, sig):
        assert is_bot_blocked(f"<html>{sig}</html>", 200) is True

    def test_clean_html(self):
        assert is_bot_blocked("<html><body>Award chart</body></html>", 200) is False

    def test_403_status(self):
        assert is_bot_blocked("", 403) is True


class TestC1MilesRequired:
    def test_jal(self):
        assert c1_miles_required(60000, 0.750) == 80000

    def test_jetblue(self):
        assert c1_miles_required(600, 0.600) == 1000

    def test_one_to_one(self):
        assert c1_miles_required(75000, 1.0) == 75000


class TestCarrierLabel:
    def test_ana_own_metal(self):
        label = get_carrier_label("ana_mileage", "North America", "North Asia")
        assert label == "ANA"

    def test_lifemiles(self):
        label = get_carrier_label("lifemiles", "North America", "North Asia")
        assert "United" in label

    def test_jal(self):
        label = get_carrier_label("jal", "North America", "North Asia")
        assert label == "JAL"


class TestCppWithSurcharge:
    def test_avios_surcharge_reduces_cpp(self):
        without = compute_cpp(1.0, 58500, 4200, 75, 0)
        with_surch = compute_cpp(1.0, 58500, 4200, 75, 600)
        assert with_surch < without


class TestRouteFilter:
    def test_transpacific_regions(self):
        assert airport_region("JFK") == "North America"
        assert airport_region("NRT") == "North Asia"
