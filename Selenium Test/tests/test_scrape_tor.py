"""Tier 3 — Firefox + Tor SOCKS (experimental). Skip on expected bot blocks."""

from __future__ import annotations

import pytest

from scrapers.contracts import SITE_CONTRACTS
from scrapers.test_results import live_scrape_record
from scrapers.tor_scraper import scrape_tor
from scrapers.validation import OUTCOME_BOT_BLOCKED, OUTCOME_TIMEOUT


@pytest.mark.tor
@pytest.mark.parametrize("site_key", list(SITE_CONTRACTS))
def test_tor_probe_point_conversion_chart(site_key: str, require_vm_scrape, record_scrape) -> None:
    result = scrape_tor(SITE_CONTRACTS[site_key])
    record_scrape(
        f"live_tor_{site_key}",
        live_scrape_record(
            site=site_key,
            browser="firefox-tor",
            validation=result.validation,
            artifact=result.artifact,
        ),
    )
    validation = result.validation
    if validation.outcome in {OUTCOME_BOT_BLOCKED, OUTCOME_TIMEOUT}:
        pytest.skip(
            f"{site_key}: Tor path blocked or timed out "
            f"(vendor={validation.bot_vendor} signals={validation.bot_signals} "
            f"messages={validation.messages})"
        )
    assert validation.outcome != "error", validation.messages
    assert validation.deliverable_met, (
        f"{site_key}: Tor loaded the page but no chart. "
        f"outcome={validation.outcome} confidence={validation.chart_confidence} "
        f"messages={validation.messages}"
    )
    assert result.artifact.get("proxy_type") == "tor"
