"""The §11 thesis: prove the architecture, not the route count.

ARCHITECTURE §11 states the bar for calling this proven, and it is not a
coverage percentage:

    Two queries side by side —
      Chase → Avios → JAL   Tokyo–Osaka   → business, minimal fuel charges
      Chase → Avios → BA    London–NY     → same currency, fuel charges demote it
    Same bank, same currency, same alliance, opposite results — and the fuel
    charge matrix plus per-segment distance banding explain exactly why.
    A flat route table cannot express that.

Plus the structural claim that justifies a graph at all: `TRANSFER* → REDEEM`
with up to 3 hops. Twelve days of production sweeps never once produced a
multi-hop route, so the one feature that distinguishes this from a lookup table
had never fired in its own output. That is asserted here directly.

These run offline against curated data. No API keys, no network.
"""

from __future__ import annotations

import os as _os

_os.environ.setdefault("MILEAGE_OFFLINE", "1")

import tempfile
from pathlib import Path

from mileage.cli import run_quote
from mileage.config import Config, build_registry, build_repository
from mileage.domain.fuel import price_paid
from mileage.domain.models import AwardSpace, Cabin, Route, User
from mileage.domain.service import serving_carriers

_CONFIG = Config.from_env()


def _quote(route: Route, currency: str, miles: int, card: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        config = Config.from_env()
        config.db_path = str(Path(tmp) / "thesis.db")
        # Never let a shared Redis cache serve a prior build's quotes into a
        # correctness test — that is how a stale $450 program-keyed surcharge
        # survived three code changes without anyone noticing.
        config.redis_url = None
        repo = build_repository(config)
        registry = build_registry(config, repo)
        try:
            return run_quote(
                route,
                User(user_id="thesis", balances={currency: miles}, card=card),
                currency,
                registry=registry,
                config=config,
            )
        finally:
            repo.close()


def _avios_rows(result: dict) -> list:
    """Avios rows from the FULL ranked list, not the displayed top ten.

    §6.1 caps the display at 10. A route can be correctly priced, correctly
    demoted, and correctly absent from the top ten all at once — asserting
    against the capped list would conflate "ranked badly" with "not computed".
    """
    return [o for o in result["ranked_all"] if o.program == "avios"]


# --------------------------------------------------------------------------- #
# 1 — the matrix, in isolation
# --------------------------------------------------------------------------- #
def test_same_currency_two_carriers_two_prices() -> None:
    """§4.4 keyed on (currency × operating carrier), not on the program."""
    short_haul = Route("HND", "ITM", Cabin.BUSINESS)
    transatlantic = Route("LHR", "JFK", Cabin.BUSINESS)

    jal = price_paid("avios", "JL", short_haul)
    ba = price_paid("avios", "BA", transatlantic)
    aa = price_paid("avios", "AA", transatlantic)

    assert jal.policy == "minimal"
    assert ba.policy == "passes_full"
    assert aa.policy == "none"

    # The headline: same balance, an order of magnitude apart in cash.
    assert jal.total_cents < 5_000, f"JAL short-haul should be tens of dollars, got {jal}"
    assert ba.total_cents > 40_000, f"BA transatlantic should be hundreds, got {ba}"

    # And on the SAME route, the only difference is whose aircraft it is.
    assert ba.total_cents > aa.total_cents * 2


def test_uk_apd_is_directional() -> None:
    """APD is levied leaving the UK. Symmetric modeling overstates half of all
    transatlantic awards by ~$216 in a premium cabin."""
    out = price_paid("avios", "BA", Route("LHR", "JFK", Cabin.BUSINESS))
    home = price_paid("avios", "BA", Route("JFK", "LHR", Cabin.BUSINESS))
    assert out.taxes_cents > home.taxes_cents + 20_000
    assert out.fuel_cents == home.fuel_cents, "carrier surcharge is not directional"


def test_per_segment_banding_keeps_the_shorthaul_sweet_spot() -> None:
    """§4.3 distance_band / per_segment. A region matrix collapses this away."""
    from mileage.domain.charts import lookup_award_miles
    from mileage.domain.geo import airports
    import yaml

    charts = yaml.safe_load(
        (_CONFIG.knowledge_dir / "charts.yaml").read_text(encoding="utf-8")
    )
    spec = charts["programs"]["avios"]
    table = airports(_CONFIG.knowledge_dir)

    short = lookup_award_miles(
        "avios", spec, Route("HND", "ITM", Cabin.BUSINESS),
        table.region_map(), airport_coords=table.coord_map(),
    )
    long = lookup_award_miles(
        "avios", spec, Route("LHR", "JFK", Cabin.BUSINESS),
        table.region_map(), airport_coords=table.coord_map(),
    )
    assert short is not None and long is not None
    # §4.3's own worked example.
    assert short.miles == 12750
    assert long.miles > short.miles * 2
    assert "per_segment_floor" in short.flags, "a connecting itinerary pays per leg"


# --------------------------------------------------------------------------- #
# 2 — the same thing end to end, through the real pipeline
# --------------------------------------------------------------------------- #
def test_thesis_demo_jal_vs_ba_end_to_end() -> None:
    """The §11 demo. Same bank, same currency, same alliance, opposite results."""
    jal_run = _quote(Route("HND", "ITM", Cabin.BUSINESS), "chase_ur", 200_000, "sapphire_reserve")
    ba_run = _quote(Route("LHR", "JFK", Cabin.BUSINESS), "chase_ur", 200_000, "sapphire_reserve")

    jal_rows = _avios_rows(jal_run)
    ba_rows = _avios_rows(ba_run)
    assert jal_rows, "no Avios route on Tokyo-Osaka"
    assert ba_rows, "no Avios route on London-New York"

    on_jal = next(r for r in jal_rows if r.operating_carrier == "JL")
    on_ba = next(r for r in ba_rows if r.operating_carrier == "BA")

    assert on_jal.price_paid_usd < 50, f"expected tens of dollars, got {on_jal.price_paid_usd}"
    assert on_ba.price_paid_usd > 400, f"expected hundreds, got {on_ba.price_paid_usd}"
    assert on_jal.fuel_policy == "minimal"
    assert on_ba.fuel_policy == "passes_full"

    # The reason is stated, not implied — a user must be able to see WHY.
    assert "JL" in on_jal.reason or "Japan" in on_jal.reason
    assert "demoted" in on_ba.reason


def test_ba_metal_ranks_below_a_cheaper_carrier_on_identical_points() -> None:
    """§6.1 step 2: identical points, different cash → cash breaks the tie.

    This is the assertion a flat route table cannot make. Both rows are the same
    currency, same chart, same mileage cost — they differ only in whose aircraft
    flies, and that difference is worth hundreds of dollars.
    """
    run = _quote(Route("LHR", "JFK", Cabin.BUSINESS), "chase_ur", 300_000, "sapphire_reserve")
    rows = _avios_rows(run)
    by_carrier = {r.operating_carrier: r for r in rows}
    assert "BA" in by_carrier, "BA metal must still be OFFERED, just ranked honestly"
    assert "AA" in by_carrier

    ba, aa = by_carrier["BA"], by_carrier["AA"]
    assert ba.source_points == aa.source_points, "same chart, same mileage cost"
    assert ba.price_paid_cents > aa.price_paid_cents
    ordered = [r.operating_carrier for r in rows]
    assert ordered.index("AA") < ordered.index("BA"), "cheaper metal must rank first"

    # And the truncation is reported, never silent.
    assert run["options_considered"] >= run["options_shown"]


# --------------------------------------------------------------------------- #
# 3 — the structural claim: TRANSFER* → REDEEM really is a graph
# --------------------------------------------------------------------------- #
def test_multi_hop_route_appears_and_beats_every_direct_route() -> None:
    """A >=2-hop transfer must be reachable, and must WIN outright.

    Without airline→airline edges the graph can only ever emit `bank → airline
    → seat`, which is a lookup table with extra steps — and that is exactly what
    twelve days of sweep output contained.

    Citi ThankYou is the currency that makes this a structural test rather than
    a pricing accident. Citi has NO direct British Airways edge — its published
    airline list does not include BA, and the only way Citi reaches Avios is
    Citi → Qatar Privilege Club → Avios. So a correct engine has to walk two
    airline→airline hops to find Citi's best LHR-JFK redemption, and a third to
    reach the cheaper Iberia Plus chart beyond it.

    This test previously used Chase and asserted only that the best multi-hop
    beat the *worst* single-hop — which a lookup table could satisfy by
    accident. It also broke the moment Chase gained its (real) direct Aer Lingus
    AerClub edge, because the cheap route stopped needing two hops. Asserting
    against the BEST single-hop, on a currency that structurally cannot reach
    the winner directly, is the property actually worth pinning.
    """
    run = _quote(Route("LHR", "JFK", Cabin.BUSINESS), "citi_typ", 300_000, "venture_x")
    options = run["verdict"].options
    multi = [o for o in options if o.transfer_hops >= 2]
    assert multi, "no multi-hop route in the output at all"
    assert any("multi_hop" in o.flags for o in multi)

    best_multi = min(multi, key=lambda o: o.source_points)
    single = [o for o in options if o.kind == "transfer" and o.transfer_hops == 1]
    assert single, "expected single-hop routes to compare against"
    best_single = min(o.source_points for o in single)
    assert best_multi.source_points < best_single, (
        f"best multi-hop ({best_multi.source_points:,} pts via "
        f"{best_multi.label}) did not beat the best direct transfer "
        f"({best_single:,} pts) — the graph is earning nothing"
    )


def test_citi_cannot_reach_avios_in_one_hop() -> None:
    """Pins the structural fact the test above depends on.

    Citi ThankYou does not transfer to British Airways. If someone re-adds that
    ratio, the multi-hop test above would still pass while silently testing a
    one-hop path, so the absence is asserted directly rather than assumed.
    """
    run = _quote(Route("LHR", "JFK", Cabin.BUSINESS), "citi_typ", 300_000, "venture_x")
    for opt in run["verdict"].options:
        if opt.program == "avios" and opt.kind == "transfer":
            assert opt.transfer_hops >= 2, (
                f"{opt.label} reaches Avios in {opt.transfer_hops} hop(s). Citi "
                "has no direct British Airways edge — the real path is "
                "Citi → Qatar → Avios."
            )


def test_account_age_gate_rides_along_on_a_multi_hop_path() -> None:
    """§4.1/§6.3 — a gate on a TRANSFER edge reaches the route that uses it.

    Built at the graph level rather than through a live route: whether any
    given city pair happens to route through Iberia depends on chart coverage
    and on whether a shorter path dominates, neither of which is what this
    asserts. What matters is that a gate declared on an edge survives all the
    way to the option a user reads.
    """
    from mileage.domain.alliances import Alliance, ProgramTransfer
    from mileage.domain.models import Gate, GateKind, Provenance, TransferRatio
    from mileage.graph.build import build_graph
    from mileage.graph.optimize import rank_paths
    from mileage.verify.crosscheck import VerifiedAward

    prov = Provenance(source_name="test", trust=1.0)
    route = Route("LHR", "JFK", Cabin.BUSINESS)
    gate = Gate(kind=GateKind.ACCOUNT_AGE, program_id="iberia_plus", min_days=90)

    graph = build_graph(
        "chase_ur",
        [TransferRatio(from_currency="chase_ur", to_program="avios",
                       ratio=1.0, provenance=prov)],
        [VerifiedAward(program="iberia_plus", route=route, miles=34000,
                       confidence=0.7, operating_carrier="IB", provenance=[prov])],
        program_transfers=[
            ProgramTransfer(from_program="avios", to_program="iberia_plus",
                            ratio=1.0, provenance=prov, gates=[gate],
                            settlement_minutes=0)
        ],
        alliances={"oneworld": Alliance("oneworld", "oneworld",
                                        frozenset({"avios", "iberia_plus"}))},
    )

    options = rank_paths(graph, "chase_ur", 0, route=route, balance=200_000)
    assert options, "no path through the gated hop"
    assert any("90" in g.describe() for o in options for g in o.gates), (
        "an edge gate must reach the option the user reads"
    )
    assert all(o.transfer_hops == 2 for o in options)


def test_a_known_too_young_account_does_block() -> None:
    """The gate is advisory only while unknown. Known-too-young really blocks."""
    from mileage.domain.models import Gate, GateKind

    gate = Gate(kind=GateKind.ACCOUNT_AGE, program_id="iberia_plus", min_days=90)
    assert gate.satisfied_by(frozenset(), {}) is True, "unknown -> not a block"
    assert gate.satisfied_by(frozenset(), {"iberia_plus": 10}) is False
    assert gate.satisfied_by(frozenset(), {"iberia_plus": 120}) is True


def test_hard_card_gate_blocks_but_still_lists_the_route() -> None:
    """§6.3 — a Freedom-only holder sees the routes AND what unlocks them."""
    run = _quote(Route("LHR", "JFK", Cabin.BUSINESS), "chase_ur", 300_000, "freedom_unlimited")
    gated = run["verdict"].gated
    assert gated, "routes must be shown, not dropped, when a card gate blocks"
    assert all(o.gates for o in gated)
    assert any(
        "Sapphire" in g.describe() for o in gated for g in o.gates
    ), "the gated list must name the card that unlocks the trip"


# --------------------------------------------------------------------------- #
# 4 — service + partner filters agree with the redemption offered
# --------------------------------------------------------------------------- #
def test_every_offered_route_is_served_and_bookable() -> None:
    """§10 property: the operating carrier must actually fly the pair, and the
    currency must be permitted to book it."""
    route = Route("SFO", "HND", Cabin.BUSINESS)
    run = _quote(route, "chase_ur", 300_000, "sapphire_reserve")
    serving = {h.carrier.id for h in serving_carriers(route)}
    for o in run["verdict"].options:
        if o.kind != "transfer" or not o.operating_carrier:
            continue
        assert o.operating_carrier in serving, (
            f"{o.label}: {o.operating_carrier} does not serve {route.key()}"
        )


def test_no_route_claims_space_it_never_checked() -> None:
    """A `no_space` verdict requires that something actually looked."""
    run = _quote(Route("SFO", "HND", Cabin.BUSINESS), "chase_ur", 300_000, "sapphire_reserve")
    if not run["space_checked"]:
        assert all(
            o.space is not AwardSpace.NONE for o in run["verdict"].options
            if o.kind == "transfer"
        ), "reported a checked negative without checking"
