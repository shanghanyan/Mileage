"""Transfer-bonus, alliance labels, and multi-hop ranking tests."""

from __future__ import annotations

import os as _os
from datetime import date
from pathlib import Path

_os.environ.setdefault("MILEAGE_OFFLINE", "1")

from mileage.domain.alliances import (
    Alliance,
    ProgramTransfer,
    load_alliances_yaml,
    program_to_alliance,
)
from mileage.domain.models import Cabin, Provenance, Route, TransferRatio
from mileage.graph.build import SEAT_NODE, build_graph
from mileage.graph.optimize import rank_paths
from mileage.providers.base import Query
from mileage.providers.curated import CuratedProvider, _partner_entries
from mileage.verify.crosscheck import VerifiedAward

_KNOWLEDGE = Path(__file__).resolve().parents[1] / "mileage" / "knowledge"
_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_effective_ratio_property() -> None:
    r = TransferRatio(
        from_currency="capital_one",
        to_program="aeroplan",
        ratio=1.0,
        bonus_multiplier=1.3,
        flags=["transfer_bonus"],
    )
    assert r.effective_ratio == 1.3
    assert r.is_bonus


def test_partner_entries_emits_base_and_active_bonus() -> None:
    rows = _partner_entries(
        "eva",
        {
            "ratio": 0.75,
            "bonus": 1.3,
            "valid_from": "2026-07-01",
            "valid_until": "2026-07-31",
            "label": "+30% EVA transfer bonus",
        },
        today=date(2026, 7, 20),
    )
    assert len(rows) == 2
    assert rows[0][1] == 1.0  # base multiplier
    assert rows[1][1] == 1.3
    assert "transfer_bonus" in rows[1][2]


def test_partner_entries_skips_expired_bonus() -> None:
    rows = _partner_entries(
        "eva",
        {
            "ratio": 0.75,
            "bonus": 1.3,
            "valid_until": "2026-06-01",
        },
        today=date(2026, 7, 20),
    )
    assert len(rows) == 1
    assert rows[0][1] == 1.0


def test_curated_loads_eva_bonus_from_calendar() -> None:
    """EVA +30% comes from the bonus calendar (Table 2), not ratios.yaml.

    Pinned to a fixture calendar: the live file is scraper-owned, so asserting
    a named promo against it fails on expiry rather than on a real regression.
    """
    from mileage.domain.models import Layer

    provider = CuratedProvider(
        as_of=date(2026, 7, 20),
        bonus_calendar_path=_FIXTURES / "bonus_calendar_pinned.yaml",
    )
    ratios = [
        q
        for q in provider.fetch(Query(Route("LAX", "TPE", Cabin.BUSINESS), Layer.CHARTS))
        if isinstance(q, TransferRatio) and q.to_program == "eva"
    ]
    assert any(r.is_bonus for r in ratios)
    assert any(not r.is_bonus for r in ratios)
    bonus = next(r for r in ratios if r.is_bonus)
    assert "live_bonus_calendar" in bonus.flags


def test_curated_no_aeroplan_demo_bonus() -> None:
    from mileage.domain.models import Layer

    provider = CuratedProvider(as_of=date(2026, 7, 20))
    ratios = [
        q
        for q in provider.fetch(Query(Route("LAX", "IST", Cabin.BUSINESS), Layer.CHARTS))
        if isinstance(q, TransferRatio) and q.to_program == "aeroplan"
    ]
    assert len(ratios) == 1
    assert ratios[0].bonus_multiplier == 1.0
    assert not ratios[0].is_bonus


def test_curated_skips_eva_bonus_outside_window() -> None:
    from mileage.domain.models import Layer

    provider = CuratedProvider(as_of=date(2026, 8, 15))
    ratios = [
        q
        for q in provider.fetch(Query(Route("LAX", "TPE", Cabin.BUSINESS), Layer.CHARTS))
        if isinstance(q, TransferRatio) and q.to_program == "eva"
    ]
    assert all(not r.is_bonus for r in ratios)


def test_bonus_path_beats_base_path() -> None:
    """Same award miles: +30% bonus needs fewer source points → higher CPP."""
    currency = "capital_one"
    ratios = [
        TransferRatio(from_currency=currency, to_program="aeroplan", ratio=1.0),
        TransferRatio(
            from_currency=currency,
            to_program="aeroplan",
            ratio=1.0,
            bonus_multiplier=1.3,
            flags=["transfer_bonus"],
            bonus_label="+30% Aeroplan transfer bonus",
        ),
    ]
    awards = [
        VerifiedAward(
            program="aeroplan",
            route=Route("LAX", "IST", Cabin.BUSINESS),
            miles=90000,
            confidence=0.9,
            flags=[],
            provenance=[Provenance(source_name="test")],
        )
    ]
    graph = build_graph(currency, ratios, awards)
    # Parallel edges currency→aeroplan
    assert graph.number_of_edges(currency, "aeroplan") == 2

    options = rank_paths(graph, currency, 450000, portal_cpp=1.25, balance=200000)
    transfer = [o for o in options if o.kind == "transfer"]
    assert len(transfer) == 2

    bonus = next(o for o in transfer if "transfer_bonus" in o.flags or "30%" in o.label)
    base = next(o for o in transfer if o is not bonus)
    assert bonus.cpp > base.cpp
    assert bonus.source_points < base.source_points
    assert bonus.cpp == max(o.cpp for o in transfer)


def test_alliance_label_on_star_alliance_path() -> None:
    currency = "capital_one"
    alliances = {
        "star_alliance": Alliance(
            id="star_alliance",
            name="Star Alliance",
            programs=frozenset({"aeroplan", "united", "turkish"}),
        )
    }
    ratios = [
        TransferRatio(from_currency=currency, to_program="aeroplan", ratio=1.0),
    ]
    awards = [
        VerifiedAward(
            program="aeroplan",
            route=Route("LAX", "IST", Cabin.BUSINESS),
            miles=60000,
            confidence=0.9,
            flags=[],
            provenance=[Provenance(source_name="test")],
        )
    ]
    graph = build_graph(currency, ratios, awards, alliances=alliances)
    options = rank_paths(graph, currency, 450000, portal_cpp=1.25, balance=200000)
    transfer = next(o for o in options if o.kind == "transfer")
    assert "Star Alliance" in transfer.label
    assert any(f.startswith("alliance:") for f in transfer.flags)


def test_program_transfer_multi_hop_beats_when_cheaper_award() -> None:
    """Currency → A → B → SEAT when B's award is cheaper after the hop."""
    currency = "capital_one"
    ratios = [
        TransferRatio(from_currency=currency, to_program="aeroplan", ratio=1.0),
    ]
    transfers = [
        ProgramTransfer(from_program="aeroplan", to_program="turkish", ratio=1.0),
    ]
    awards = [
        VerifiedAward(
            program="aeroplan",
            route=Route("LAX", "IST", Cabin.BUSINESS),
            miles=90000,
            confidence=0.9,
            flags=[],
            provenance=[Provenance(source_name="test")],
        ),
        VerifiedAward(
            program="turkish",
            route=Route("LAX", "IST", Cabin.BUSINESS),
            miles=45000,
            confidence=0.9,
            flags=[],
            provenance=[Provenance(source_name="test")],
        ),
    ]
    graph = build_graph(
        currency, ratios, awards, program_transfers=transfers
    )
    options = rank_paths(graph, currency, 450000, portal_cpp=1.25, balance=200000)
    multi = [o for o in options if o.kind == "transfer" and "multi_hop" in o.flags]
    assert len(multi) == 1
    assert multi[0].program == "turkish"
    assert multi[0].source_points == 45000
    assert "program_transfer" in multi[0].flags


def test_multi_hop_flag_when_two_transfer_hops() -> None:
    """Program→program edge compounds; path gets multi_hop flag."""
    currency = "capital_one"
    ratios = [
        TransferRatio(from_currency=currency, to_program="aeroplan", ratio=1.0),
    ]
    import networkx as nx

    g = nx.MultiDiGraph()
    g.add_node(currency, kind="currency")
    g.add_node("aeroplan", kind="program")
    g.add_node("turkish", kind="program")
    g.add_node(SEAT_NODE, kind="seat")
    g.add_edge(
        currency, "aeroplan", key="base", ratio=1.0, confidence=1.0, flags=[], provenance=None
    )
    g.add_edge(
        "aeroplan",
        "turkish",
        key="partner",
        ratio=1.0,
        confidence=0.8,
        flags=["program_transfer"],
        provenance=None,
    )
    g.add_edge(
        "turkish",
        SEAT_NODE,
        key="award",
        miles=45000,
        confidence=0.9,
        flags=[],
        provenance=None,
        seats_available=None,
    )
    options = rank_paths(g, currency, 90000, portal_cpp=1.25, balance=100000)
    multi = [o for o in options if o.kind == "transfer" and "multi_hop" in o.flags]
    assert len(multi) == 1
    assert "Aeroplan" in multi[0].label and "Turkish" in multi[0].label


def test_load_alliances_yaml() -> None:
    alliances, transfers = load_alliances_yaml(_KNOWLEDGE / "alliances.yaml")
    assert "star_alliance" in alliances
    assert "aeroplan" in alliances["star_alliance"].programs
    assert "united" in alliances["star_alliance"].programs
    assert any(t.from_program == "marriott_bonvoy" for t in transfers)
    mapping = program_to_alliance(alliances)
    assert mapping["aeroplan"].id == "star_alliance"
    assert mapping["delta"].id == "skyteam"
