"""Tier 2 — Google Chrome via Xvfb, VPS public IP (primary)."""

from __future__ import annotations

import pytest

from scrapers.chrome_scraper import scrape_chrome
from scrapers.contracts import SITE_CONTRACTS
from scrapers.test_results import live_scrape_record


@pytest.mark.chrome
@pytest.mark.parametrize("site_key", list(SITE_CONTRACTS))
def test_chrome_finds_point_conversion_chart(site_key: str, require_vm_scrape, record_scrape) -> None:
    result = scrape_chrome(SITE_CONTRACTS[site_key])
    record_scrape(
        f"live_chrome_{site_key}",
        live_scrape_record(
            site=site_key,
            browser="chrome",
            validation=result.validation,
            artifact=result.artifact,
        ),
    )
    validation = result.validation
    assert validation.outcome != "error", validation.messages
    assert validation.deliverable_met, (
        f"{site_key}: expected point conversion chart via Chrome. "
        f"outcome={validation.outcome} confidence={validation.chart_confidence} "
        f"messages={validation.messages}"
    )
    assert validation.chart is not None
    assert validation.chart.confidence >= 0.45
    assert result.artifact.get("html_path")
    assert result.artifact.get("proxy_type") == "vps"
