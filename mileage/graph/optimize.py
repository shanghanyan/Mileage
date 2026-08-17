"""Enumerate `TRANSFER* → REDEEM` paths and price each one (§5, §6.2).

What changed and why: this module used to compute cents-per-point as
`(market_fare − taxes) / points`, which made an external cash fare a hard
input. When the fare was missing the caller threw the entire ranking away and
returned an error string — after doing the full graph search. Twelve days of
sweeps spent ~67s/run producing error text for answers it had already computed,
on exactly the long-haul routes the product exists for.

Now every path is priced from our own tables:

    price_paid = government taxes + airport add-ons + carrier surcharge

which is always available (§4.4). `cpp` is computed only when a market fare
happens to exist, and is None otherwise — one missing column instead of one
missing answer.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import networkx as nx

from ..domain.alliances import currency_display_name
from ..domain.cpp import compound_ratio, portal_points_needed, source_points_for_award
from ..domain.cpp import cpp as cpp_fn
from ..domain.fuel import FuelMatrix, PricePaid, fuel_matrix, price_paid
from ..domain.geo import AirportTable, airports, route_distance_miles
from ..domain.models import AwardSpace, Gate, PathOption, Provenance, Route
from .build import MAX_TRANSFER_HOPS, carrier_of_seat, is_seat, seat_nodes


def _portal_option(
    route: Route,
    cash_cents: int,
    portal_cpp: float,
    balance: int,
    fare_confidence: float,
    fare_flags: list[str],
    *,
    currency: str,
) -> PathOption:
    """The 'just book it with the portal' floor.

    Only meaningful when a cash fare exists — a portal redemption IS a cash
    fare paid in points, so with no fare there is nothing to convert.
    """
    pts = portal_points_needed(cash_cents, portal_cpp)
    return PathOption(
        label=f"{currency_display_name(currency)} portal",
        kind="portal",
        cpp=portal_cpp,
        source_points=int(pts),
        cash_cents=cash_cents,
        price_paid_cents=0,  # the portal price IS the fare; nothing extra owed
        program=None,
        affordable=balance >= pts,
        confidence=round(fare_confidence, 3),
        flags=list(fare_flags),
        currency=currency,
        cabin=route.cabin.value,
        space=AwardSpace.CONFIRMED,  # portal inventory is revenue inventory
        reason=f"portal floor at {portal_cpp:.2f}c/pt — always bookable",
    )


def _hop_label(node: str, edge: dict, graph: nx.MultiDiGraph) -> str:
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
    graph: nx.MultiDiGraph,
    currency: str,
    cash_cents: int,
    *,
    route: Optional[Route] = None,
    portal_cpp: Optional[float] = None,
    balance: int = 0,
    fare_confidence: float = 1.0,
    fare_flags: Optional[list[str]] = None,
    max_transfer_hops: int = MAX_TRANSFER_HOPS,
    matrix: Optional[FuelMatrix] = None,
    table: Optional[AirportTable] = None,
    as_of: Optional[date] = None,
) -> list[PathOption]:
    """Every `TRANSFER* → REDEEM` path from `currency`, priced.

    Returned unsorted-by-policy: ordering is domain/rank.py's job, which is pure
    and testable in isolation. This function's job is enumeration + pricing.
    """
    fare_flags = fare_flags or []
    matrix = matrix or fuel_matrix()
    table = table or airports()
    options: list[PathOption] = []

    if route is None:  # legacy callers passed no route; nothing to price
        route = Route("XXX", "XXX")

    has_fare = cash_cents > 0
    if portal_cpp is not None and portal_cpp > 0 and has_fare:
        options.append(
            _portal_option(
                route,
                cash_cents,
                portal_cpp,
                balance,
                fare_confidence,
                fare_flags,
                currency=currency,
            )
        )

    if currency not in graph:
        return options

    prefix = currency_display_name(currency)
    # +1 for the terminating REDEEM edge.
    cutoff = max_transfer_hops + 1
    distance = route_distance_miles(route, table=table)

    for sink in seat_nodes(graph):
        for edge_path in nx.all_simple_edge_paths(
            graph, currency, sink, cutoff=cutoff
        ):
            option = _price_path(
                graph,
                edge_path,
                currency=currency,
                prefix=prefix,
                route=route,
                cash_cents=cash_cents,
                balance=balance,
                fare_confidence=fare_confidence,
                fare_flags=fare_flags,
                matrix=matrix,
                table=table,
                distance=distance,
            )
            if option is not None:
                options.append(option)

    return options


def _price_path(
    graph: nx.MultiDiGraph,
    edge_path: list,
    *,
    currency: str,
    prefix: str,
    route: Route,
    cash_cents: int,
    balance: int,
    fare_confidence: float,
    fare_flags: list[str],
    matrix: FuelMatrix,
    table: AirportTable,
    distance: Optional[float],
) -> Optional[PathOption]:
    ratios: list[float] = []
    confidences: list[float] = [fare_confidence]
    provenance: list[Provenance] = []
    flags: set[str] = set(fare_flags)
    gates: list[Gate] = []
    hop_labels: list[str] = []
    program = edge_path[-1][0]
    seats_available: Optional[int] = None
    space = AwardSpace.UNKNOWN
    transfer_hops = 0
    settlement_minutes = 0
    live_taxes_cents: Optional[int] = None
    miles = 0
    carrier: Optional[str] = None
    carrier_name: Optional[str] = None

    for u, v, key in edge_path:
        edge = graph.edges[u, v, key]
        confidences.append(edge.get("confidence", 0.5))
        if edge.get("provenance"):
            provenance.append(edge["provenance"])
        flags.update(edge.get("flags", []))
        for gate in edge.get("gates") or []:
            # A currency-level gate (e.g. "Chase transfers need a premium
            # card") sits on every hop out of that currency, so a 3-hop path
            # would otherwise list it three times.
            if gate not in gates:
                gates.append(gate)

        if is_seat(v):
            miles = edge["miles"]
            seats_available = edge.get("seats_available")
            space = edge.get("space") or AwardSpace.UNKNOWN
            carrier = edge.get("operating_carrier") or carrier_of_seat(v)
            carrier_name = edge.get("carrier_name")
            raw_taxes = edge.get("taxes_cents")
            # `is not None`, not truthiness: 0 means a source CONFIRMED there
            # are no taxes, which is a stronger statement than our estimate and
            # must not be discarded as if it were missing.
            live_taxes_cents = int(raw_taxes) if raw_taxes is not None else None
            if edge.get("alliance_id") and edge["alliance_id"] != "independent":
                flags.add(f"alliance:{edge['alliance_id']}")
        else:
            ratios.append(edge.get("ratio", edge.get("effective_ratio", 1.0)))
            hop_labels.append(_hop_label(v, edge, graph))
            transfer_hops += 1
            settlement_minutes += int(edge.get("settlement_minutes") or 0)
            if "program_transfer" in edge.get("flags", []):
                flags.add("program_transfer")

    if not miles:
        return None

    # Price paid — §4.4, keyed on (currency actually redeemed × operating
    # carrier). `program` is the currency being spent at the redeem step, which
    # for a multi-hop path is the LAST program, not the wallet we started from.
    paid: PricePaid = price_paid(
        program,
        carrier,
        route,
        matrix=matrix,
        table=table,
        distance_miles=distance,
    )
    flags.update(paid.flags)
    confidences.append(paid.confidence)

    # A live tax quote, when one exists, beats our estimate for the government
    # portion; the carrier surcharge estimate still applies on top.
    taxes_cents = live_taxes_cents if live_taxes_cents is not None else paid.taxes_cents
    if live_taxes_cents is not None:
        flags.discard("estimated_surcharge")
        flags.add("live_tax_quote")
    total_paid = taxes_cents + paid.fuel_cents

    if seats_available is not None:
        flags.add(f"{seats_available} seats")
    if transfer_hops > 1:
        flags.add("multi_hop")

    eff_ratio = compound_ratio(ratios)
    source_points = source_points_for_award(miles, eff_ratio)

    # cpp only when there is a fare to divide by. Previously this silently
    # returned 0.0 with no fare, which sorts a real route below every other
    # real route — a missing input quietly rendered as a terrible answer.
    path_cpp = (
        round(cpp_fn(cash_cents, source_points, taxes_cents=total_paid), 4)
        if cash_cents > 0
        else None
    )
    if cash_cents <= 0:
        flags.add("no_market_fare")

    path_conf = 1.0
    for c in confidences:
        path_conf *= c

    label = f"{prefix} -> " + " -> ".join(hop_labels)
    if carrier_name or carrier:
        label = f"{label} on {carrier_name or carrier}"

    return PathOption(
        label=label,
        kind="transfer",
        cpp=path_cpp,
        source_points=int(source_points),
        cash_cents=cash_cents,
        price_paid_cents=total_paid,
        program=program,
        affordable=balance >= source_points,
        confidence=round(path_conf, 3),
        flags=sorted(flags),
        provenance=provenance,
        taxes_cents=taxes_cents,
        fuel_cents=paid.fuel_cents,
        fuel_policy=paid.policy,
        currency=currency,
        cabin=route.cabin.value,
        operating_carrier=carrier,
        carrier_name=carrier_name,
        space=space if isinstance(space, AwardSpace) else AwardSpace.UNKNOWN,
        transfer_hops=transfer_hops,
        settlement_minutes=settlement_minutes,
        gates=gates,
    )
