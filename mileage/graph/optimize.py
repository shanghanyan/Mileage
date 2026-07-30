"""Rank redemption paths by cents-per-point (§7).

Enumerates every currency -> ... -> SEAT path on a MultiDiGraph (so base and
transfer-bonus edges are both considered), compounds the effective transfer
ratios along the hops, converts the seat's program-miles cost into source
points, and computes CPP = net_cash / source_points (cash minus award taxes).

Portal floor is only included when the currency has an honest portal rate —
never invent a Cap One Venture rate for Chase/Amex/etc.
"""

from __future__ import annotations

from typing import Optional

import networkx as nx

from ..domain.alliances import currency_display_name
from ..domain.cpp import (
    cpp as cpp_fn,
    compound_ratio,
    portal_points_needed,
    source_points_for_award,
)
from ..domain.models import PathOption, Provenance
from ..domain.surcharges import estimate_taxes_cents
from .build import MAX_TRANSFER_HOPS, SEAT_NODE


def _portal_option(
    cash_cents: int,
    portal_cpp: float,
    balance: int,
    fare_confidence: float,
    fare_flags: list[str],
    *,
    currency: str,
) -> PathOption:
    pts = portal_points_needed(cash_cents, portal_cpp)
    return PathOption(
        label=f"{currency_display_name(currency)} portal",
        kind="portal",
        cpp=portal_cpp,
        source_points=int(pts),
        cash_cents=cash_cents,
        program=None,
        affordable=balance >= pts,
        confidence=round(fare_confidence, 3),
        flags=list(fare_flags),
        currency=currency,
    )


def _hop_label(node: str, edge: dict, graph: nx.MultiDiGraph | nx.DiGraph) -> str:
    """Human label for one transfer hop, including bonus + alliance annotation."""
    name = node.replace("_", " ").title()
    label = edge.get("bonus_label")
    if label:
        base = f"{name} ({label})"
    elif edge.get("bonus_multiplier", 1.0) != 1.0:
        pct = int(round((edge["bonus_multiplier"] - 1.0) * 100))
        base = f"{name} (+{pct}% bonus)"
    else:
        base = name

    alliance_name = None
    if node in graph.nodes:
        alliance_name = graph.nodes[node].get("alliance_name")
    if alliance_name and alliance_name != "Independent":
        base = f"{base} [{alliance_name}]"
    return base


def rank_paths(
    graph: nx.MultiDiGraph | nx.DiGraph,
    currency: str,
    cash_cents: int,
    *,
    portal_cpp: Optional[float],
    balance: int,
    fare_confidence: float = 1.0,
    fare_flags: Optional[list[str]] = None,
    max_transfer_hops: int = MAX_TRANSFER_HOPS,
    surcharges: Optional[dict] = None,
) -> list[PathOption]:
    fare_flags = fare_flags or []
    options: list[PathOption] = []
    if portal_cpp is not None and portal_cpp > 0:
        options.append(
            _portal_option(
                cash_cents,
                portal_cpp,
                balance,
                fare_confidence,
                fare_flags,
                currency=currency,
            )
        )

    if currency not in graph or SEAT_NODE not in graph:
        return sorted(options, key=lambda o: o.cpp, reverse=True)

    prefix = currency_display_name(currency)
    cutoff = max_transfer_hops + 2

    if isinstance(graph, nx.MultiDiGraph):
        edge_paths = nx.all_simple_edge_paths(graph, currency, SEAT_NODE, cutoff=cutoff)
        for edge_path in edge_paths:
            ratios: list[float] = []
            confidences: list[float] = [fare_confidence]
            provenance: list[Provenance] = []
            flags: set[str] = set(fare_flags)
            hop_labels: list[str] = []
            program = edge_path[-1][0]
            seats_available: Optional[int] = None
            transfer_hops = 0
            taxes_cents = 0
            miles = 0

            for u, v, key in edge_path:
                edge = graph.edges[u, v, key]
                confidences.append(edge.get("confidence", 0.5))
                if edge.get("provenance"):
                    provenance.append(edge["provenance"])
                flags.update(edge.get("flags", []))
                if v == SEAT_NODE:
                    miles = edge["miles"]
                    seats_available = edge.get("seats_available")
                    taxes_cents = int(edge.get("taxes_cents") or 0)
                    if edge.get("alliance_id") and edge["alliance_id"] != "independent":
                        flags.add(f"alliance:{edge['alliance_id']}")
                else:
                    ratios.append(edge.get("ratio", edge.get("effective_ratio", 1.0)))
                    hop_labels.append(_hop_label(v, edge, graph))
                    transfer_hops += 1
                    if "program_transfer" in edge.get("flags", []):
                        flags.add("program_transfer")

            if taxes_cents <= 0 and program:
                est, tax_flags, tax_conf = estimate_taxes_cents(
                    program, surcharges=surcharges
                )
                taxes_cents = est
                flags.update(tax_flags)
                confidences.append(tax_conf)

            if seats_available is not None:
                flags.add(f"{seats_available} seats")
            if transfer_hops > 1:
                flags.add("multi_hop")

            eff_ratio = compound_ratio(ratios)
            source_points = source_points_for_award(miles, eff_ratio)
            path_cpp = cpp_fn(cash_cents, source_points, taxes_cents=taxes_cents)
            path_conf = 1.0
            for c in confidences:
                path_conf *= c

            label = f"{prefix} -> " + " -> ".join(hop_labels)
            options.append(
                PathOption(
                    label=label,
                    kind="transfer",
                    cpp=round(path_cpp, 4),
                    source_points=int(source_points),
                    cash_cents=cash_cents,
                    program=program,
                    affordable=balance >= source_points,
                    confidence=round(path_conf, 3),
                    flags=sorted(flags),
                    provenance=provenance,
                    taxes_cents=taxes_cents,
                    currency=currency,
                )
            )
    else:
        for path in nx.all_simple_paths(graph, currency, SEAT_NODE, cutoff=cutoff):
            ratios = []
            confidences = [fare_confidence]
            provenance = []
            flags_set: set[str] = set(fare_flags)
            program = path[-2]
            seats_available = None
            taxes_cents = 0
            miles = 0
            for u, v in zip(path, path[1:]):
                edge = graph.edges[u, v]
                confidences.append(edge.get("confidence", 0.5))
                if edge.get("provenance"):
                    provenance.append(edge["provenance"])
                flags_set.update(edge.get("flags", []))
                if v == SEAT_NODE:
                    miles = edge["miles"]
                    seats_available = edge.get("seats_available")
                    taxes_cents = int(edge.get("taxes_cents") or 0)
                else:
                    ratios.append(edge["ratio"])
            if taxes_cents <= 0 and program:
                est, tax_flags, tax_conf = estimate_taxes_cents(
                    program, surcharges=surcharges
                )
                taxes_cents = est
                flags_set.update(tax_flags)
                confidences.append(tax_conf)
            if seats_available is not None:
                flags_set.add(f"{seats_available} seats")
            if len(path) - 2 > 1:
                flags_set.add("multi_hop")
            eff_ratio = compound_ratio(ratios)
            source_points = source_points_for_award(miles, eff_ratio)
            path_cpp = cpp_fn(cash_cents, source_points, taxes_cents=taxes_cents)
            path_conf = 1.0
            for c in confidences:
                path_conf *= c
            label = f"{prefix} -> " + " -> ".join(
                n.replace("_", " ").title() for n in path[1:-1]
            )
            options.append(
                PathOption(
                    label=label,
                    kind="transfer",
                    cpp=round(path_cpp, 4),
                    source_points=int(source_points),
                    cash_cents=cash_cents,
                    program=program,
                    affordable=balance >= source_points,
                    confidence=round(path_conf, 3),
                    flags=sorted(flags_set),
                    provenance=provenance,
                    taxes_cents=taxes_cents,
                    currency=currency,
                )
            )

    return sorted(options, key=lambda o: o.cpp, reverse=True)
