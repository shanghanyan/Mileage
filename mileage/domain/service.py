"""Table 3 (service) + partner rights (§4.5) — the two filters that run first.

§5.1 puts these ahead of any graph traversal. That ordering is the whole point
of Table 3: it collapses ~60 candidate currencies to a handful before Dijkstra
runs, which is what keeps multi-hop search affordable. Running the graph first
and filtering after would evaluate redeem edges for carriers that don't fly the
requested pair at all — most of the search space wasted on impossible bookings.

Two questions, deliberately answered by two different tables:
    serving_carriers()  — who actually flies O→D in this cabin      (Table 3)
    bookable_carriers() — who this currency is allowed to book      (§4.5)
A route must satisfy both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

from .geo import AirportTable, airports
from .models import Cabin, Route

_KNOWLEDGE = Path(__file__).resolve().parent.parent / "knowledge"


@dataclass(frozen=True)
class Carrier:
    id: str
    name: str
    program: str
    alliance: str
    hubs: frozenset[str]
    serves: frozenset[str]
    cabins: frozenset[str]
    intra_cabins: frozenset[str]
    # Explicit verified pairs: frozenset of (origin, dest) sorted tuples -> cabins
    routes: dict[tuple[str, str], frozenset[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class ServiceHit:
    carrier: Carrier
    verified: bool  # explicit `routes` entry vs. hub-rule inference

    @property
    def flags(self) -> list[str]:
        return ["service_verified"] if self.verified else ["service_inferred"]

    @property
    def confidence(self) -> float:
        # An inferred edge is a plausible network claim, not a schedule lookup.
        return 0.9 if self.verified else 0.55


@dataclass(frozen=True)
class ServiceMap:
    carriers: dict[str, Carrier]

    def by_program(self, program: str) -> list[Carrier]:
        return [c for c in self.carriers.values() if c.program == program]

    def get(self, carrier_id: str) -> Optional[Carrier]:
        return self.carriers.get(carrier_id.upper())


def _pair(a: str, b: str) -> tuple[str, str]:
    return (a.upper(), b.upper()) if a.upper() <= b.upper() else (b.upper(), a.upper())


def load_service_map(knowledge_dir: Optional[Path] = None) -> ServiceMap:
    root = Path(knowledge_dir) if knowledge_dir else _KNOWLEDGE
    path = root / "carriers.yaml"
    if not path.exists():
        return ServiceMap(carriers={})
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    default_intra = frozenset(
        str(c) for c in (data.get("default_intra_cabins") or ["economy", "business"])
    )

    carriers: dict[str, Carrier] = {}
    for cid, spec in (data.get("carriers") or {}).items():
        if not isinstance(spec, dict):
            continue
        cabins = frozenset(str(c) for c in (spec.get("cabins") or ["economy"]))
        routes: dict[tuple[str, str], frozenset[str]] = {}
        for row in spec.get("routes") or []:
            if not isinstance(row, dict) or not row.get("from") or not row.get("to"):
                continue
            routes[_pair(str(row["from"]), str(row["to"]))] = frozenset(
                str(c) for c in (row.get("cabins") or cabins)
            )
        carriers[str(cid).upper()] = Carrier(
            id=str(cid).upper(),
            name=str(spec.get("name") or cid),
            program=str(spec.get("program") or ""),
            alliance=str(spec.get("alliance") or "independent"),
            hubs=frozenset(str(h).upper() for h in (spec.get("hubs") or [])),
            serves=frozenset(str(r) for r in (spec.get("serves") or [])),
            cabins=cabins,
            intra_cabins=frozenset(
                str(c) for c in (spec.get("intra_cabins") or default_intra)
            ),
            routes=routes,
        )
    return ServiceMap(carriers=carriers)


@lru_cache(maxsize=8)
def _cached_service(root: str) -> ServiceMap:
    return load_service_map(Path(root))


def service_map(knowledge_dir: Optional[Path] = None) -> ServiceMap:
    return _cached_service(str(Path(knowledge_dir) if knowledge_dir else _KNOWLEDGE))


def serves(
    carrier: Carrier,
    route: Route,
    *,
    table: Optional[AirportTable] = None,
) -> Optional[ServiceHit]:
    """Does `carrier` fly this pair in this cabin? None if not."""
    table = table or airports()
    cabin = route.cabin.value
    o, d = route.origin.upper(), route.dest.upper()

    explicit = carrier.routes.get(_pair(o, d))
    if explicit is not None:
        return ServiceHit(carrier=carrier, verified=True) if cabin in explicit else None

    r_o, r_d = table.region_of(o), table.region_of(d)
    if r_o is None or r_d is None:
        return None  # unknown airport — never guess service into a void

    # Hub rule: one endpoint is a hub, the other sits in a served region.
    if o in carrier.hubs and r_d in carrier.serves:
        pass
    elif d in carrier.hubs and r_o in carrier.serves:
        pass
    else:
        return None

    # Intra-region flying uses the short-haul product, which rarely includes a
    # true first-class cabin. Claiming first on a domestic hop is a real way to
    # surface a route that cannot be booked.
    allowed = carrier.intra_cabins if r_o == r_d else carrier.cabins
    if cabin not in allowed:
        return None
    return ServiceHit(carrier=carrier, verified=False)


def serving_carriers(
    route: Route,
    *,
    smap: Optional[ServiceMap] = None,
    table: Optional[AirportTable] = None,
) -> list[ServiceHit]:
    """Every carrier that flies this pair in this cabin (Table 3 filter)."""
    smap = smap or service_map()
    table = table or airports()
    hits = [
        hit
        for c in smap.carriers.values()
        if (hit := serves(c, route, table=table)) is not None
    ]
    # Verified schedule beats inferred network shape when both are present.
    return sorted(hits, key=lambda h: (not h.verified, h.carrier.id))


# --------------------------------------------------------------------------- #
# Partner rights (§4.5)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PartnerRights:
    by_currency: dict[str, frozenset[str]]

    def bookable(self, currency: str) -> frozenset[str]:
        return self.by_currency.get(currency, frozenset())

    def can_book(self, currency: str, carrier_id: str) -> bool:
        return carrier_id.upper() in self.bookable(currency)


def load_partner_rights(
    knowledge_dir: Optional[Path] = None,
    *,
    smap: Optional[ServiceMap] = None,
) -> PartnerRights:
    """Expand alliance membership + explicit extras into carrier id sets."""
    root = Path(knowledge_dir) if knowledge_dir else _KNOWLEDGE
    smap = smap or service_map(root)
    path = root / "partners.yaml"
    if not path.exists():
        return PartnerRights(by_currency={})
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    grants = bool(data.get("alliance_grants_booking", True))

    by_alliance: dict[str, set[str]] = {}
    for c in smap.carriers.values():
        by_alliance.setdefault(c.alliance, set()).add(c.id)

    out: dict[str, frozenset[str]] = {}
    for currency, spec in (data.get("currencies") or {}).items():
        if not isinstance(spec, dict):
            continue
        allowed: set[str] = set()
        alliance = str(spec.get("alliance") or "independent")
        if grants and alliance != "independent":
            allowed |= by_alliance.get(alliance, set())
        allowed |= {str(c).upper() for c in (spec.get("extra") or [])}
        allowed -= {str(c).upper() for c in (spec.get("excluded") or [])}
        # A program can always book its own metal.
        allowed |= {c.id for c in smap.carriers.values() if c.program == currency}
        out[str(currency)] = frozenset(allowed)
    return PartnerRights(by_currency=out)


@lru_cache(maxsize=8)
def _cached_rights(root: str) -> PartnerRights:
    return load_partner_rights(Path(root))


def partner_rights(knowledge_dir: Optional[Path] = None) -> PartnerRights:
    return _cached_rights(str(Path(knowledge_dir) if knowledge_dir else _KNOWLEDGE))


@dataclass(frozen=True)
class RedeemCandidate:
    """One viable REDEEM edge: this currency, on this carrier's metal."""

    currency: str
    carrier: Carrier
    service_verified: bool

    @property
    def flags(self) -> list[str]:
        return ["service_verified"] if self.service_verified else ["service_inferred"]


def viable_redeem_edges(
    route: Route,
    currencies: list[str],
    *,
    smap: Optional[ServiceMap] = None,
    rights: Optional[PartnerRights] = None,
    table: Optional[AirportTable] = None,
) -> list[RedeemCandidate]:
    """SERVICE FILTER then PARTNER FILTER (§5.1), in that order.

    Returns the (currency, carrier) pairs that both fly the route and are
    bookable — the only redeem edges worth building a graph for.
    """
    smap = smap or service_map()
    rights = rights or partner_rights()
    hits = serving_carriers(route, smap=smap, table=table)
    out: list[RedeemCandidate] = []
    for currency in currencies:
        allowed = rights.bookable(currency)
        for hit in hits:
            if hit.carrier.id in allowed:
                out.append(
                    RedeemCandidate(
                        currency=currency,
                        carrier=hit.carrier,
                        service_verified=hit.verified,
                    )
                )
    return out


def reachable_programs_for_route(
    route: Route,
    *,
    smap: Optional[ServiceMap] = None,
    rights: Optional[PartnerRights] = None,
    table: Optional[AirportTable] = None,
) -> set[str]:
    """Currencies with at least one viable redeem edge on this route.

    This is the pruning set: any program outside it cannot produce a bookable
    award here, so there is no reason to fetch a chart or walk the graph for it.
    """
    smap = smap or service_map()
    rights = rights or partner_rights()
    hits = {h.carrier.id for h in serving_carriers(route, smap=smap, table=table)}
    if not hits:
        return set()
    return {
        currency
        for currency, allowed in rights.by_currency.items()
        if allowed & hits
    }


__all__ = [
    "Carrier",
    "PartnerRights",
    "RedeemCandidate",
    "ServiceHit",
    "ServiceMap",
    "load_partner_rights",
    "load_service_map",
    "partner_rights",
    "reachable_programs_for_route",
    "serves",
    "service_map",
    "serving_carriers",
    "viable_redeem_edges",
]
