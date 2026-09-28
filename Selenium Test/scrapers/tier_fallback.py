"""Escalate Tier 1 → 2 → 3 → 4 and stop at the first validated chart."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from scrapers.chrome_scraper import scrape_chrome
from scrapers.contracts import SiteContract, get_contract
from scrapers.http_fetch import tier1_http_fetch
from scrapers.login import LoginMimicryDisabled, scrape_with_login
from scrapers.tor_scraper import scrape_tor
from scrapers.validation import OUTCOME_LOGIN_REQUIRED, ValidationResult


@dataclass
class TierAttempt:
    tier: int
    name: str
    validation: ValidationResult
    artifact: dict[str, Any]


def attempt_to_dict(attempt: TierAttempt) -> dict[str, Any]:
    art = attempt.artifact or {}
    return {
        "kind": "live_scrape",
        "tier": attempt.tier,
        "name": attempt.name,
        "browser": attempt.name,
        "outcome": attempt.validation.outcome,
        "deliverable_met": attempt.validation.deliverable_met,
        "chart_confidence": attempt.validation.chart_confidence,
        "messages": attempt.validation.messages,
        "title": attempt.validation.title,
        "text_length": attempt.validation.text_length,
        "final_url": attempt.validation.final_url or art.get("final_url", ""),
        "html_path": art.get("html_path", ""),
        "screenshot_path": art.get("screenshot_path", ""),
        "proxy_type": art.get("proxy_type", ""),
        "validation": attempt.validation.to_dict(),
        "artifact": art,
    }


@dataclass
class FallbackResult:
    contract_key: str
    winning_tier: int | None
    deliverable_met: bool
    attempts: list[TierAttempt] = field(default_factory=list)
    artifact: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "live_scrape",
            "contract_key": self.contract_key,
            "winning_tier": self.winning_tier,
            "deliverable_met": self.deliverable_met,
            "attempts": [attempt_to_dict(a) for a in self.attempts],
            "artifact": self.artifact,
        }


def run_tier_fallback(
    key: str,
    *,
    include_browser: bool = True,
    include_tor: bool = True,
    include_login: bool = True,
    rate_limit: bool = True,
    chrome_fn: Callable[..., Any] | None = None,
    tor_fn: Callable[..., Any] | None = None,
    on_attempt: Callable[[TierAttempt], None] | None = None,
) -> FallbackResult:
    contract: SiteContract = get_contract(key) if not isinstance(key, SiteContract) else key
    result = FallbackResult(contract_key=contract.key, winning_tier=None, deliverable_met=False)

    def _note(attempt: TierAttempt) -> None:
        result.attempts.append(attempt)
        if on_attempt:
            on_attempt(attempt)

    t1 = tier1_http_fetch(contract, rate_limit=rate_limit)
    _note(TierAttempt(1, "httpx", t1.validation, t1.artifact))
    if t1.validation.deliverable_met:
        result.winning_tier = 1
        result.deliverable_met = True
        result.artifact = t1.artifact
        return result

    if not include_browser:
        return result

    chrome = (chrome_fn or scrape_chrome)(contract, rate_limit=rate_limit)
    _note(TierAttempt(2, "chrome", chrome.validation, chrome.artifact))
    if chrome.validation.deliverable_met:
        result.winning_tier = 2
        result.deliverable_met = True
        result.artifact = chrome.artifact
        return result

    if include_tor:
        tor = (tor_fn or scrape_tor)(contract, rate_limit=rate_limit)
        _note(TierAttempt(3, "tor", tor.validation, tor.artifact))
        if tor.validation.deliverable_met:
            result.winning_tier = 3
            result.deliverable_met = True
            result.artifact = tor.artifact
            return result
        login_needed = chrome.validation.outcome == OUTCOME_LOGIN_REQUIRED or tor.validation.outcome == OUTCOME_LOGIN_REQUIRED
    else:
        login_needed = chrome.validation.outcome == OUTCOME_LOGIN_REQUIRED

    if include_login and login_needed:
        try:
            login = scrape_with_login(contract, rate_limit=rate_limit)
            _note(TierAttempt(4, "login", login.validation, login.artifact))
            if login.validation.deliverable_met:
                result.winning_tier = 4
                result.deliverable_met = True
                result.artifact = login.artifact
        except LoginMimicryDisabled as exc:
            _note(
                TierAttempt(
                    4,
                    "login",
                    ValidationResult(
                        outcome="error",
                        deliverable_met=False,
                        chart_confidence=0.0,
                        messages=[str(exc)],
                    ),
                    {},
                )
            )
    return result
