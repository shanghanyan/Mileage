"""Tier fallback stops at the first validated chart."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from scrapers.contracts import get_contract
from scrapers.tier_fallback import run_tier_fallback
from scrapers.validation import ValidationResult

pytestmark = pytest.mark.unit


@dataclass
class _FakeScrape:
    validation: ValidationResult
    artifact: dict


def _result(outcome: str, deliverable: bool, confidence: float = 0.0) -> _FakeScrape:
    return _FakeScrape(
        validation=ValidationResult(
            outcome=outcome,
            deliverable_met=deliverable,
            chart_confidence=confidence,
            messages=[outcome],
        ),
        artifact={"outcome": outcome, "deliverable_met": deliverable},
    )


def test_fallback_stops_after_tier1_success(monkeypatch: pytest.MonkeyPatch) -> None:
    contract = get_contract("jal_partner_point")

    class _T1:
        validation = ValidationResult(outcome="success", deliverable_met=True, chart_confidence=0.7)
        artifact = {"tier": 1, "deliverable_met": True}

    monkeypatch.setattr("scrapers.tier_fallback.tier1_http_fetch", lambda *a, **k: _T1())
    called = {"chrome": False}

    def boom(*_a, **_k):
        called["chrome"] = True
        raise AssertionError("Chrome should not run after Tier 1 success")

    result = run_tier_fallback(contract.key, chrome_fn=boom, include_tor=False, rate_limit=False)
    assert result.deliverable_met is True
    assert result.winning_tier == 1
    assert called["chrome"] is False


def test_fallback_escalates_to_chrome(monkeypatch: pytest.MonkeyPatch) -> None:
    class _T1:
        validation = ValidationResult(outcome="chart_not_found", deliverable_met=False, chart_confidence=0.1)
        artifact = {"tier": 1}

    monkeypatch.setattr("scrapers.tier_fallback.tier1_http_fetch", lambda *a, **k: _T1())
    chrome = _result("success", True, 0.8)
    result = run_tier_fallback(
        "capitalone_venture_partners",
        chrome_fn=lambda *_a, **_k: chrome,
        include_tor=True,
        include_login=False,
        rate_limit=False,
        tor_fn=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("Tor should not run")),
    )
    assert result.winning_tier == 2
    assert result.deliverable_met is True


def test_cli_rejects_unknown_site() -> None:
    from scrapers.run import main

    assert main(["--tier1-only", "--site", "not_a_real_site"]) == 2
