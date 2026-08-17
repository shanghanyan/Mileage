"""Build the redemption graph from verified edges (§3).

Two edge types, and neither cares whether its endpoints are banks or airlines:

  TRANSFER — points MOVE from one program's account into another's. Bank →
             airline is the common case, but airline → airline is real and
             expanding (the Avios family moves directly between programs).
  REDEEM   — points STAY PUT; you book a seat on a partner's aircraft using
             your program's award chart. Avios → JAL is this. You never obtain
             JAL miles.

Every route is `TRANSFER* → REDEEM` — zero or more transfers terminating in
exactly one redeem edge. Conflating the two produces routes that don't exist.

Nodes:
  - the source currency (e.g. "capital_one")
  - one node per program
  - one SEAT sink per OPERATING CARRIER, not a single shared sink

That last point is load-bearing and is new. A single SEAT node makes "Avios on
JAL" and "Avios on BA" the same edge, so they collapse to one price — and the
entire premise of the fuel-charge matrix (§4.4) is that they must not. Seat
nodes are keyed per carrier so the redeem edge can carry the metal you actually
fly, and the surcharge that comes with it.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Optional

import networkx as nx

from ..domain.alliances import Alliance, ProgramTransfer, program_to_alliance
from ..domain.models import TransferRatio
from ..verify.crosscheck import VerifiedAward

# Legacy single-sink id, kept so old callers/tests that reference it still
# resolve. Carrier-specific sinks are `__SEAT__:<carrier>`.
SEAT_NODE = "__SEAT__"
SEAT_PREFIX = "__SEAT__:"

# §3: "Hop cap is 3 transfers — double and triple transfers stay in, which is
# where the non-obvious wins live." Was 2, which structurally excluded the
# Amex → BA Avios → Iberia → Qatar shape the architecture calls out by name.
MAX_TRANSFER_HOPS = 3


def seat_node(carrier: Optional[str]) -> str:
    """Sink id for one operating carrier (or the shared sink when unknown)."""
    return f"{SEAT_PREFIX}{carrier.upper()}" if carrier else SEAT_NODE


def is_seat(node: str) -> bool:
    return node == SEAT_NODE or node.startswith(SEAT_PREFIX)


def carrier_of_seat(node: str) -> Optional[str]:
    return node[len(SEAT_PREFIX):] if node.startswith(SEAT_PREFIX) else None


def build_graph(
    currency: str,
    ratios: Iterable[TransferRatio],
    awards: Iterable[VerifiedAward],
    *,
    program_transfers: Optional[Iterable[ProgramTransfer]] = None,
    alliances: Optional[Mapping[str, Alliance]] = None,
) -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()
    g.add_node(currency, kind="currency")

    alliance_by_program = program_to_alliance(dict(alliances or {}))

    def _tag_program(program: str) -> None:
        if program not in g:
            g.add_node(program, kind="program")
        alliance = alliance_by_program.get(program)
        if alliance is not None:
            g.nodes[program]["alliance_id"] = alliance.id
            g.nodes[program]["alliance_name"] = alliance.name

    for r in ratios:
        if r.from_currency != currency:
            continue
        _tag_program(r.to_program)
        key = "bonus" if r.is_bonus else "base"
        # Distinct keys so base + bonus both exist as parallel edges.
        if r.is_bonus and r.bonus_label:
            key = f"bonus:{r.bonus_label}"
        g.add_edge(
            r.from_currency,
            r.to_program,
            key=key,
            kind="TRANSFER",
            ratio=r.effective_ratio,
            base_ratio=r.ratio,
            bonus_multiplier=r.bonus_multiplier,
            confidence=r.confidence,
            provenance=r.provenance,
            flags=list(r.flags),
            bonus_label=r.bonus_label,
            gates=list(getattr(r, "gates", []) or []),
            settlement_minutes=int(getattr(r, "settlement_minutes", 0) or 0),
        )

    # Program→program transfer edges, added transitively so a 3-hop chain
    # (Amex → Avios → Iberia → Qatar) actually materializes. Repeating until no
    # new node appears is what lets the hop cap mean what §3 says it means;
    # a single pass could only ever attach one extra hop.
    transfers = list(program_transfers or [])
    for _ in range(MAX_TRANSFER_HOPS):
        added = False
        for t in transfers:
            if t.from_program not in g or t.from_program == currency:
                continue
            if t.to_program not in g:
                added = True
            _tag_program(t.to_program)
            key = "program_transfer"
            if t.bonus_label:
                key = f"program_transfer:{t.bonus_label}"
            if g.has_edge(t.from_program, t.to_program, key=key):
                continue
            g.add_edge(
                t.from_program,
                t.to_program,
                key=key,
                kind="TRANSFER",
                ratio=t.effective_ratio,
                base_ratio=t.ratio,
                bonus_multiplier=t.bonus_multiplier,
                confidence=t.confidence,
                provenance=t.provenance,
                flags=list(t.flags),
                bonus_label=t.bonus_label,
                gates=list(getattr(t, "gates", []) or []),
                settlement_minutes=int(getattr(t, "settlement_minutes", 0) or 0),
            )
            added = True
        if not added:
            break

    for a in awards:
        # Only wire programs the currency can actually reach. The absence of a
        # Capital One → United ratio is load-bearing: it structurally prevents
        # any United path unless a second hop bridges it.
        if a.program not in g:
            continue
        _tag_program(a.program)
        alliance = alliance_by_program.get(a.program)
        carrier = getattr(a, "operating_carrier", None)
        sink = seat_node(carrier)
        if sink not in g:
            g.add_node(sink, kind="seat", carrier=carrier)
        award_flags = list(a.flags)
        if alliance is not None and alliance.id != "independent":
            award_flags.append(f"alliance:{alliance.id}")
        g.add_edge(
            a.program,
            sink,
            key=f"award:{carrier or '*'}",
            kind="REDEEM",
            miles=a.miles,
            confidence=a.confidence,
            provenance=a.provenance,
            flags=award_flags,
            seats_available=a.seats_available,
            space=getattr(a, "space", None),
            taxes_cents=a.taxes_cents,
            operating_carrier=carrier,
            carrier_name=getattr(a, "carrier_name", None),
            alliance_id=alliance.id if alliance else None,
            alliance_name=alliance.name if alliance else None,
        )
    return g


def seat_nodes(g: nx.MultiDiGraph) -> list[str]:
    return [n for n in g.nodes if is_seat(n)]
