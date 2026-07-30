"""Build the redemption graph from verified edges (§3).

Nodes:
  - the source currency (e.g. "capital_one")
  - one node per partner program
  - a single SEAT sink representing the requested route/cabin

Edges (MultiDiGraph — parallel currency→program edges for base vs bonus):
  - currency -> program : a verified TransferRatio
  - program  -> program : optional ProgramTransfer (second hop / hotel→airline)
  - program  -> SEAT    : a verified award cost

Alliance membership is stored on program nodes so the optimizer can label
paths (e.g. Aeroplan books Star Alliance metal, including United) without
inventing fake currency→United transfers.

A program only connects to SEAT if there is a verified award cost for it, and
only contributes a path if the currency can reach it — so the absence of a
Capital One -> United ratio structurally prevents any United path unless a
second-hop ProgramTransfer bridges it.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Optional

import networkx as nx

from ..domain.alliances import Alliance, ProgramTransfer, program_to_alliance
from ..domain.models import TransferRatio
from ..verify.crosscheck import VerifiedAward

SEAT_NODE = "__SEAT__"

# Max transfer hops (currency → … → program) before the SEAT edge. Caps
# combinatorial growth once program→program edges appear.
MAX_TRANSFER_HOPS = 2


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
    g.add_node(SEAT_NODE, kind="seat")

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
            ratio=r.effective_ratio,
            base_ratio=r.ratio,
            bonus_multiplier=r.bonus_multiplier,
            confidence=r.confidence,
            provenance=r.provenance,
            flags=list(r.flags),
            bonus_label=r.bonus_label,
        )

    # Second-hop loyalty→loyalty edges. Only attach when the source program is
    # already reachable from the currency (or *is* the currency for hotel banks
    # modeled as currencies — those use TransferRatio instead).
    for t in program_transfers or []:
        if t.from_program not in g:
            continue
        _tag_program(t.to_program)
        key = "program_transfer"
        if t.bonus_label:
            key = f"program_transfer:{t.bonus_label}"
        g.add_edge(
            t.from_program,
            t.to_program,
            key=key,
            ratio=t.effective_ratio,
            base_ratio=t.ratio,
            bonus_multiplier=t.bonus_multiplier,
            confidence=t.confidence,
            provenance=t.provenance,
            flags=list(t.flags),
            bonus_label=t.bonus_label,
        )

    for a in awards:
        # Only wire programs the currency can actually reach.
        if a.program not in g:
            continue
        _tag_program(a.program)
        alliance = alliance_by_program.get(a.program)
        award_flags = list(a.flags)
        if alliance is not None and alliance.id != "independent":
            award_flags.append(f"alliance:{alliance.id}")
        g.add_edge(
            a.program,
            SEAT_NODE,
            key="award",
            miles=a.miles,
            confidence=a.confidence,
            provenance=a.provenance,
            flags=award_flags,
            seats_available=a.seats_available,
            taxes_cents=a.taxes_cents,
            alliance_id=alliance.id if alliance else None,
            alliance_name=alliance.name if alliance else None,
        )
    return g
