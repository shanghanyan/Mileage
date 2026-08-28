"""§10 property-based invariants + the §8 compile gate.

These are the rules that must hold for EVERY result, not just the golden ones.
Written against the pure modules (domain/rank.py, domain/fuel.py,
domain/service.py) so they run in milliseconds and need no pipeline.

`hypothesis` isn't a project dependency, so the generators here are a small
deterministic sweep rather than randomized search — same invariants, no new
install, and reproducible failures.
"""

from __future__ import annotations

import itertools
import os as _os

_os.environ.setdefault("MILEAGE_OFFLINE", "1")

from dataclasses import replace

import pytest

from mileage.config import Config
from mileage.domain.cpp import source_points_for_award
from mileage.domain.fuel import HIGH_FUEL_USD, price_paid
from mileage.domain.geo import airports, haul_band, route_distance_miles
from mileage.domain.models import AwardSpace, Cabin, PathOption, Route
from mileage.domain.rank import MAX_RESULTS, collapse_dominated, dominates, rank_options
from mileage.domain.service import partner_rights, serving_carriers, service_map
from mileage.knowledge_snapshot import build_snapshot

_CONFIG = Config.from_env()

AIRPORTS = ["LAX", "JFK", "SFO", "LHR", "CDG", "HND", "ITM", "SIN", "SYD", "DXB", "GRU", "JNB"]
CABINS = [Cabin.ECONOMY, Cabin.BUSINESS]


def _option(**kw) -> PathOption:
    base = dict(
        label=kw.pop("label", "test"),
        kind="transfer",
        source_points=10_000,
        price_paid_cents=5_000,
        currency="capital_one",
        program="turkish",
        operating_carrier="TK",
        cabin=Cabin.ECONOMY.value,
    )
    base.update(kw)
    return PathOption(**base)


# --------------------------------------------------------------------------- #
# §8 — the compile gate
# --------------------------------------------------------------------------- #
def test_knowledge_tables_pass_referential_integrity() -> None:
    """Every transfer destination, carrier, airport and chart currency resolves.

    This is the check that caught a chart renamed from `iberia` to
    `iberia_plus` while ratios.yaml still pointed at the old id — a dangling
    reference that produces no error at runtime, just a transfer partner that
    silently never reaches a chart.
    """
    snapshot = build_snapshot(_CONFIG.knowledge_dir)
    assert not snapshot.blocking, "\n".join(str(i) for i in snapshot.blocking)


def test_knowledge_tables_are_fresh() -> None:
    """§7: `verifiedAt` older than 90 days is a build failure, not a comment."""
    snapshot = build_snapshot(_CONFIG.knowledge_dir)
    stale = [i for i in snapshot.issues if i.kind == "staleness"]
    assert not stale, "\n".join(str(i) for i in stale[:20])


def test_snapshot_hash_is_stable_and_content_addressed() -> None:
    a = build_snapshot(_CONFIG.knowledge_dir)
    b = build_snapshot(_CONFIG.knowledge_dir)
    assert a.hash == b.hash, "same bytes must hash the same"
    assert len(a.hash) == 64


# --------------------------------------------------------------------------- #
# §10 — ranking properties
# --------------------------------------------------------------------------- #
def test_list_never_exceeds_ten() -> None:
    opts = [_option(label=f"r{i}", source_points=1000 * i) for i in range(1, 40)]
    ranked, _ = rank_options(opts, balance_by_currency={"capital_one": 10**9})
    assert len(ranked) <= MAX_RESULTS


def test_first_class_never_ranks_below_business() -> None:
    first = _option(label="first", cabin=Cabin.FIRST.value, source_points=90_000)
    business = _option(label="biz", cabin=Cabin.BUSINESS.value, source_points=1_000)
    ranked, _ = rank_options([business, first], balance_by_currency={"capital_one": 10**9})
    assert ranked[0].cabin == Cabin.FIRST.value, (
        "cabin class dominates every other criterion (§6.1 step 1)"
    )


def test_routes_beyond_the_reach_multiple_are_dropped_but_counted() -> None:
    """Out of reach is a number, not a silence.

    JFK-JNB printed "no bookable option" while 8 chart rows priced it at
    105k-115k against a 100k balance. Dropping is fine; dropping without
    counting is what made a too-expensive route look like a nonexistent one.
    """
    opts = [_option(label=f"r{i}", source_points=p) for i, p in enumerate([5_000, 50_000])]
    ranked, out_of_reach = rank_options(opts, balance_by_currency={"capital_one": 10_000})
    assert [o.source_points for o in ranked] == [5_000]
    assert out_of_reach == 1, "the 50k row must be counted, not silently discarded"


def test_reach_band_is_shown_and_names_its_shortfall() -> None:
    """Within 1.5x balance the row survives and says how short it falls."""
    opts = [_option(label="reach", source_points=14_000)]
    ranked, out_of_reach = rank_options(opts, balance_by_currency={"capital_one": 10_000})
    assert out_of_reach == 0
    assert len(ranked) == 1
    assert ranked[0].affordable is False
    assert ranked[0].shortfall_points == 4_000
    assert "4,000 pts short" in ranked[0].reason


def test_affordable_rows_always_outrank_reach_rows() -> None:
    """A reach row must never displace something the user can actually book."""
    # Distinct programs/metal — otherwise these are two prices for ONE booking
    # and collapse_dominated correctly folds the worse one away before ranking.
    reach = _option(
        label="reach", source_points=11_000, program="avios", operating_carrier="BA"
    )
    afford = _option(
        label="afford", source_points=9_000, program="turkish", operating_carrier="TK"
    )
    ranked, _ = rank_options([reach, afford], balance_by_currency={"capital_one": 10_000})
    assert [o.label for o in ranked] == ["afford", "reach"]


def test_heavy_cash_is_demoted_below_cheap_cash() -> None:
    cheap = _option(label="cheap", source_points=70_000, price_paid_cents=500)
    heavy = _option(label="heavy", source_points=60_000,
                    price_paid_cents=int(HIGH_FUEL_USD * 100) + 30_000)
    ranked, _ = rank_options([heavy, cheap], balance_by_currency={"capital_one": 10**9})
    assert ranked[0].label == "cheap", (
        "a 60k-point route carrying heavy surcharges must not outrank a "
        "70k-point route carrying almost none (§6.1 step 2)"
    )


def test_dominated_variants_collapse_but_survivor_is_annotated() -> None:
    bonused = _option(label="lifemiles", source_points=10_870)
    plain = _option(label="lifemiles", source_points=12_500)
    kept = collapse_dominated([bonused, plain])
    assert len(kept) == 1
    assert kept[0].source_points == 10_870
    assert any(f.startswith("collapsed_variants:") for f in kept[0].flags)


def test_dominance_is_irreflexive_and_asymmetric() -> None:
    a = _option(label="a", source_points=1_000)
    b = _option(label="b", source_points=2_000)
    assert not dominates(a, a)
    assert dominates(a, b) and not dominates(b, a)


def test_different_metal_is_never_collapsed() -> None:
    """Same points, same program, different aircraft — a real choice, not noise."""
    on_aa = _option(label="x", operating_carrier="AA", price_paid_cents=2_000)
    on_ba = _option(label="x", operating_carrier="BA", price_paid_cents=56_000)
    assert len(collapse_dominated([on_aa, on_ba])) == 2


def test_ranking_is_deterministic() -> None:
    opts = [_option(label=f"r{i}", source_points=10_000) for i in range(8)]
    a = [o.label for o in rank_options(opts, balance_by_currency={"capital_one": 10**9})[0]]
    b = [o.label for o in rank_options(list(reversed(opts)),
                                       balance_by_currency={"capital_one": 10**9})[0]]
    assert a == b, "ranking must not depend on input order"


# --------------------------------------------------------------------------- #
# §10 — transfer math properties
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ratio", [0.5, 0.75, 1.0, 1.5, 2.0])
def test_more_source_points_never_yields_fewer_destination_points(ratio: float) -> None:
    prev = -1
    for miles in range(1_000, 100_000, 7_000):
        pts = source_points_for_award(miles, ratio)
        assert pts >= prev
        prev = pts


@pytest.mark.parametrize("ratio", [0.5, 0.75, 1.0, 1.5])
def test_source_points_always_cover_the_award(ratio: float) -> None:
    """Round UP, always. Under-transferring strands points with no way back."""
    for miles in (6_500, 12_750, 45_000, 63_000, 85_001):
        pts = source_points_for_award(miles, ratio)
        assert pts * ratio >= miles - 1e-6, (
            f"{pts} source points at {ratio} yields less than {miles}"
        )


# --------------------------------------------------------------------------- #
# §10 — pricing properties
# --------------------------------------------------------------------------- #
def test_price_paid_is_never_negative_and_always_explained() -> None:
    for o, d in itertools.combinations(AIRPORTS, 2):
        for cabin in CABINS:
            p = price_paid("avios", "BA", Route(o, d, cabin))
            assert p.total_cents >= 0
            assert p.total_cents == p.taxes_cents + p.fuel_cents
            assert p.policy, "every price must name the policy that produced it"
            assert "estimated_surcharge" in p.flags


def test_price_rises_with_cabin() -> None:
    route_e = Route("LHR", "JFK", Cabin.ECONOMY)
    route_b = Route("LHR", "JFK", Cabin.BUSINESS)
    assert price_paid("avios", "BA", route_b).total_cents > price_paid(
        "avios", "BA", route_e
    ).total_cents


def test_unknown_pair_is_not_priced_as_free() -> None:
    """An unknown (currency, carrier) must not look like a bargain."""
    p = price_paid("no_such_program", "ZZ", Route("LAX", "JFK", Cabin.BUSINESS))
    assert "fuel_policy_unknown" in p.flags
    assert p.fuel_cents > 0, "guessing zero understates the bill"


def test_haul_bands_partition_all_distances() -> None:
    for miles in (0, 1, 1499, 1500, 3499, 3500, 6999, 7000, 25_000):
        assert haul_band(miles) in {"short", "medium", "long", "ultra_long"}


# --------------------------------------------------------------------------- #
# §10 — service / partner-rights properties
# --------------------------------------------------------------------------- #
def test_every_serving_carrier_is_a_known_carrier() -> None:
    smap = service_map(_CONFIG.knowledge_dir)
    for o, d in itertools.combinations(AIRPORTS, 2):
        for hit in serving_carriers(Route(o, d, Cabin.ECONOMY), smap=smap):
            assert hit.carrier.id in smap.carriers


def test_partner_rights_only_name_known_carriers() -> None:
    smap = service_map(_CONFIG.knowledge_dir)
    rights = partner_rights(_CONFIG.knowledge_dir)
    known = set(smap.carriers)
    for currency, allowed in rights.by_currency.items():
        unknown = allowed - known
        assert not unknown, f"{currency} may book unknown carrier(s) {sorted(unknown)}"


def test_a_program_can_always_book_its_own_metal() -> None:
    smap = service_map(_CONFIG.knowledge_dir)
    rights = partner_rights(_CONFIG.knowledge_dir)
    for carrier in smap.carriers.values():
        if carrier.program in rights.by_currency:
            assert rights.can_book(carrier.program, carrier.id), (
                f"{carrier.program} cannot book its own {carrier.id} metal"
            )


def test_service_never_claims_first_class_on_a_domestic_hop() -> None:
    """Intra-region flying uses the short-haul product."""
    hits = serving_carriers(Route("HND", "ITM", Cabin.FIRST))
    assert not hits, "no carrier offers a true first cabin Tokyo-Osaka"


def test_every_airport_has_both_a_region_and_a_position() -> None:
    """The failure this table exists to prevent: a region with no coordinates
    silently disables every distance-banded chart for that airport."""
    table = airports(_CONFIG.knowledge_dir)
    for code, ap in table.by_iata.items():
        assert ap.region, f"{code} has no region"
        assert (ap.lat, ap.lon) != (0.0, 0.0), f"{code} has no coordinates"


def test_route_distance_resolves_for_every_known_pair() -> None:
    for o, d in itertools.combinations(AIRPORTS, 2):
        assert route_distance_miles(Route(o, d, Cabin.ECONOMY)) is not None


# --------------------------------------------------------------------------- #
# Availability honesty
# --------------------------------------------------------------------------- #
def test_award_space_states_are_distinct() -> None:
    assert AwardSpace.UNKNOWN is not AwardSpace.NONE
    assert AwardSpace.UNKNOWN.value != AwardSpace.NONE.value
    assert all(s.label for s in AwardSpace)


def test_unknown_space_is_the_default() -> None:
    """Defaulting to a negative would fabricate a fact. Default to ignorance."""
    assert _option().space is AwardSpace.UNKNOWN
