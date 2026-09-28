"""Tests for data source hardening (Wayback, RSS, URL validation)."""

from __future__ import annotations

import pytest

from scrapers.chart_scraper import load_scrape_targets
from scrapers.rss_awards import _entry_matches, load_rss_feeds
from scrapers.wayback import wayback_source_name


DEAD_URL_FRAGMENTS = [
    "frequentmiler.com/air-canada-aeroplan-sweet-spots",
    "upgradedpoints.com/travel/airlines/ana-mileage-club-award-chart",
    "upgradedpoints.com/travel/airlines/singapore-krisflyer-award-chart",
    "awardwallet.com/airlines/singapore-airlines-krisflyer",
    "10xtravel.com/singapore-krisflyer-award-chart",
    "frequentmiler.com/singapore-krisflyer-sweet-spots",
    "awardtravelfinder.com/award-charts/singapore",
    "upgradedpoints.com/travel/airlines/british-airways-avios-award-chart",
    "awardwallet.com/airlines/british-airways-avios",
    "frequentmiler.com/british-airways-avios-sweet-spots",
    "10xtravel.com/british-airways-avios-award-chart",
    "upgradedpoints.com/travel/airlines/cathay-pacific-asia-miles-award-chart",
    "awardwallet.com/airlines/cathay-pacific-asia-miles",
    "upgradedpoints.com/travel/airlines/jal-mileage-bank-award-chart",
    "awardwallet.com/airlines/jal-mileage-bank",
    "upgradedpoints.com/travel/airlines/qatar-airways-privilege-club-award-chart",
    "awardwallet.com/airlines/qatar-airways-privilege-club",
    "upgradedpoints.com/travel/airlines/qantas-frequent-flyer-award-chart",
    "awardwallet.com/airlines/qantas-frequent-flyer",
    "upgradedpoints.com/travel/airlines/eva-air-infinity-mileagelands-award-chart",
    "awardwallet.com/airlines/eva-air-infinity-mileagelands",
    "capitalone.com/credit-cards/benefits/transfer-partners",
    "awardtravelfinder.com/award-charts/singapore",
]


class TestPrunedTargets:
    def test_no_dead_urls_in_config(self):
        targets = load_scrape_targets()
        all_urls = [
            entry["url"]
            for entries in targets.values()
            if isinstance(entries, list)
            for entry in entries
            if entry.get("url")
        ]
        for url in all_urls:
            for dead in DEAD_URL_FRAGMENTS:
                assert dead not in url, f"Dead URL still in config: {url}"

    def test_krisflyer_has_http_targets(self):
        targets = load_scrape_targets().get("krisflyer", [])
        assert len(targets) >= 1
        assert any("10xtravel.com" in t["url"] for t in targets)

    def test_avios_has_correct_slug(self):
        urls = [t["url"] for t in load_scrape_targets().get("avios", [])]
        assert any("british-airways-club-award-charts" in u for u in urls)

    def test_qantas_has_correct_slug(self):
        urls = [t["url"] for t in load_scrape_targets().get("qantas", [])]
        assert any("qantas-frequent-flyer-award-charts" in u for u in urls)

    def test_eva_air_has_sources(self):
        assert len(load_scrape_targets().get("eva_air", [])) >= 1

    def test_jal_has_official_source(self):
        urls = [t["url"] for t in load_scrape_targets().get("jal", [])]
        assert any("jal.co.jp" in u for u in urls)


class TestRssMatching:
    def test_keyword_match(self):
        entry = {"title": "Best ways to use Singapore KrisFlyer miles", "summary": ""}
        assert _entry_matches(entry, ["krisflyer", "singapore"])

    def test_no_match(self):
        entry = {"title": "Hotel review in Paris", "summary": ""}
        assert not _entry_matches(entry, ["krisflyer"])


class TestWayback:
    def test_source_name(self):
        assert wayback_source_name("https://frequentmiler.com/foo").startswith("wayback:")
