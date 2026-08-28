"""Date stability — SFO→Tokyo across September and October.

WHAT THIS TEST CURRENTLY PROVES, HONESTLY
-----------------------------------------
Almost nothing, and that is the reason to write it down.

`domain.models.Route` is (origin, dest, cabin). There is no date on it. Nothing
in `domain/charts.py` reads a date: no peak/off-peak bands, no seasonal tables,
no blackout logic. `Query.start_date` / `Query.end_date` exist and are threaded
through `run_quote`, but only two providers ever read them — seats.aero (live
award search) and Amadeus (cash fares). Both are OFF in the hermetic suite.

So today this test is close to tautological: identical inputs produce identical
outputs because the date never reaches the pricing path at all.

WHY IT EXISTS ANYWAY
--------------------
It is a tripwire, positioned before the wire is live. The moment anyone adds

  - peak / off-peak award bands (most real charts have them: BA, Aeroplan and
    Flying Blue all price by date),
  - a seasonal surcharge in fuel_charges.yaml,
  - or a date-sensitive provider that feeds the ranking,

this test starts doing real work, and it fails LOUDLY the first time a Tuesday
in September prices differently from a Tuesday in October without anyone having
decided that it should. Writing it after such a change lands is writing it too
late: by then the new behaviour is the baseline and there is nothing to compare
against.

It also pins a claim the sweep implies but never checks — that a quote is a
function of (snapshot, route, wallet) and nothing else. If that stops being
true, the beta logs stop being comparable day to day, which is the entire
premise of running them daily.

The sampled dates are deliberately spread across two months and both a weekday
and a weekend, since date-sensitivity, when it arrives, usually arrives as
day-of-week or season.
"""

from __future__ import annotations

import os as _os

_os.environ.setdefault("MILEAGE_OFFLINE", "1")

import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from mileage.cli import run_quote
from mileage.config import Config, build_registry
from mileage.domain.models import Cabin, Route, User

_CONFIG = Config.from_env()

# Sept–Oct 2026. Spread across both months, mixing weekdays and a weekend, so a
# day-of-week or seasonal rule cannot hide between two similar samples.
SAMPLE_DATES: tuple[str, ...] = (
    "2026-09-08",  # Tuesday, early September
    "2026-09-19",  # Saturday, mid September
    "2026-09-30",  # Wednesday, end of September
    "2026-10-13",  # Tuesday, mid October
    "2026-10-24",  # Saturday, late October
)

# Both Tokyo airports: NRT is the long-haul default, HND is the one the JAL
# service map lists explicitly, so a regression could plausibly hit one only.
TOKYO_ROUTES: tuple[tuple[str, str, str], ...] = (
    ("SFO", "NRT", "business"),
    ("SFO", "HND", "business"),
)

BALANCE = 300_000  # deliberately generous: this test is about dates, not reach


def _fingerprint(payload: dict[str, Any]) -> dict[str, Any]:
    """The parts of a quote that must not move when only the date moves.

    Deliberately excludes elapsed time, cache counters and timestamps — those
    are expected to vary and comparing them would make the test flaky for
    reasons that have nothing to do with dates.
    """
    return {
        "verdict": payload.get("verdict"),
        "options_considered": payload.get("options_considered"),
        "options_out_of_reach": payload.get("options_out_of_reach"),
        "options": [
            {
                "label": o.get("label"),
                "source_points": o.get("source_points"),
                "price_paid_cents": o.get("price_paid_cents"),
                "operating_carrier": o.get("operating_carrier"),
                "effective_ratio": o.get("effective_ratio"),
                "hop_ratios": o.get("hop_ratios"),
            }
            for o in (payload.get("options") or [])
        ],
    }


def _quote_on(route: Route, start_date: str) -> dict[str, Any]:
    from mileage.serialize import quote_result_to_dict

    registry = build_registry(_CONFIG)
    user = User(
        user_id="date_stability",
        card="sapphire_reserve",
        balances={"chase_ur": BALANCE},
    )
    window_start = date.fromisoformat(start_date)
    result = run_quote(
        route,
        user,
        "chase_ur",
        registry=registry,
        config=_CONFIG,
        start_date=start_date,
        # A 30-day search window anchored on each sample date.
        end_date=(window_start + timedelta(days=30)).isoformat(),
    )
    return quote_result_to_dict(result)


@pytest.mark.parametrize("origin,dest,cabin", TOKYO_ROUTES)
def test_sfo_tokyo_quote_is_identical_across_september_and_october(
    origin: str, dest: str, cabin: str
) -> None:
    """Same route, five dates across two months — one answer.

    Currently trivially true (Route carries no date). Kept as a tripwire: see
    this module's docstring. If it ever fails, either seasonal pricing was
    added on purpose — in which case this test should be rewritten to assert
    the intended per-date behaviour, not deleted — or a date leaked into the
    pricing path by accident, which is exactly what it is here to catch.
    """
    route = Route(origin, dest, Cabin(cabin))

    baseline_date = SAMPLE_DATES[0]
    baseline = _fingerprint(_quote_on(route, baseline_date))
    assert baseline["options"], (
        f"{route.key()} produced no options at all — this test cannot say "
        "anything about date stability if the route is empty on every date"
    )

    for sample in SAMPLE_DATES[1:]:
        current = _fingerprint(_quote_on(route, sample))
        assert current == baseline, (
            f"{route.key()} priced differently on {sample} than on "
            f"{baseline_date}.\n"
            "Nothing in the pricing path reads a date today, so this is either "
            "a deliberate new seasonal rule (rewrite this test to assert it) "
            "or an accidental date dependency (fix it)."
        )


def test_sfo_tokyo_is_stable_when_the_same_date_is_asked_twice() -> None:
    """Determinism control.

    Without this, a failure above is ambiguous between "dates matter" and "the
    engine is nondeterministic". Ask the same date twice; if THIS fails, the
    problem is not dates.
    """
    route = Route("SFO", "NRT", Cabin.BUSINESS)
    first = _fingerprint(_quote_on(route, SAMPLE_DATES[0]))
    second = _fingerprint(_quote_on(route, SAMPLE_DATES[0]))
    assert first == second, (
        "same route, same date, two different answers — the engine is "
        "nondeterministic, so no date comparison above can be trusted"
    )


def test_fingerprint_actually_discriminates() -> None:
    """Guard against a vacuous tripwire.

    An equality assertion is only worth as much as the sensitivity of the thing
    being compared. If `_fingerprint` ever collapsed to a constant — say someone
    trims it down to `{"verdict": ...}` — every date test above would pass
    forever while checking nothing.

    Worth knowing: SFO-NRT and SFO-HND fingerprint IDENTICALLY today. That is
    correct, not a bug — charts are region-banded and both airports sit in
    `north_asia` with the same carriers serving them — but it means changing
    the destination is NOT a valid sensitivity probe. Cabin is, because cabin
    is the one axis §6.1 ranks above everything else.
    """
    business = _fingerprint(_quote_on(Route("SFO", "NRT", Cabin.BUSINESS), SAMPLE_DATES[0]))
    economy = _fingerprint(_quote_on(Route("SFO", "NRT", Cabin.ECONOMY), SAMPLE_DATES[0]))
    assert business != economy, (
        "_fingerprint cannot tell a business quote from an economy one, so the "
        "date-stability assertions above are comparing nothing"
    )


def test_route_still_carries_no_date_field() -> None:
    """Pins the assumption the tests above rest on.

    If someone adds a date to `Route`, the stability tests silently become
    weaker — they would still compare two quotes, but both built from a Route
    that now has a date field they never set. This fails first and says so.
    """
    route = Route("SFO", "NRT", Cabin.BUSINESS)
    assert not hasattr(route, "date"), (
        "Route gained a date field. The date-stability tests in this module "
        "assume dates reach pricing only through Query.start_date/end_date — "
        "revisit them before this lands."
    )
    # `replace` proves the dataclass shape is still the three-field one.
    assert replace(route, cabin=Cabin.ECONOMY).key() == "SFO-NRT-economy"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
