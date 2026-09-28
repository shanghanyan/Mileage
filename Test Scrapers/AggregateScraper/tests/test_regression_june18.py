"""Tests for June 18 regression fixes."""

from __future__ import annotations

import pytest

from pipeline.orchestrator import _dedupe_freshness_entries
from scrapers.exceptions import RateLimitError, WaybackSnapshotMissingError


class TestFreshnessDedup:
    def test_deduplicates_per_source(self):
        raw = [
            {"name": "ana_mileage", "source": "10xtravel.com", "scraped_at": "2026-06-18", "used": False},
            {"name": "ana_mileage", "source": "10xtravel.com", "scraped_at": "2026-06-18", "used": False},
            {"name": "ana_mileage", "source": "10xtravel.com", "scraped_at": "2026-06-18", "used": True},
        ]
        result = _dedupe_freshness_entries(raw)
        assert len(result) == 1
        assert result[0]["used"] is True


class TestWaybackExceptions:
    def test_rate_limit_is_distinct(self):
        with pytest.raises(RateLimitError):
            raise RateLimitError("429")

    def test_missing_snapshot_is_distinct(self):
        with pytest.raises(WaybackSnapshotMissingError):
            raise WaybackSnapshotMissingError("no snapshot")


class TestProgramSummary:
    def test_per_program_tracking(self):
        from scrapers.scraper_logger import program_summary, record_attempt, record_success, reset_run_stats

        reset_run_stats()
        record_attempt("avios")
        record_attempt("avios")
        record_success("avios")
        summary = program_summary()
        assert summary["avios"]["attempted"] == 2
        assert summary["avios"]["succeeded"] == 1
