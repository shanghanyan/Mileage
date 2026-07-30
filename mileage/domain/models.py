"""Source-agnostic domain models.

These types are the contract between the provider layer and the verification /
graph core. A scrape, an API call, or a curated YAML row all normalize to the
same `AwardQuote` / `FareQuote`, so the core cannot tell them apart
(Cursor-Mileage-Plan.md §2.2).

Every datum carries provenance + confidence as first-class fields (§2.7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class Layer(str, Enum):
    """The four data layers a provider can serve (§1)."""

    SCHEDULES = "schedules"  # L1: which flights fly O->D, and when
    FARES = "fares"          # L2: the cash price-to-beat
    AWARD = "award"          # L3: is there a saver seat in miles
    CHARTS = "charts"        # L4: ratios + award charts (how points convert)


class Cabin(str, Enum):
    ECONOMY = "economy"
    PREMIUM_ECONOMY = "premium_economy"
    BUSINESS = "business"
    FIRST = "first"


class VerdictLabel(str, Enum):
    """Honest conclusions (§7). Never name a winner without verified data."""

    PORTAL_ONLY = "portal_only"      # no verified transfer path beats the floor
    COMPARABLE = "comparable"        # best transfer within 20% of portal
    BEST = "best"                    # transfer beats portal by >=20%
    TENTATIVE_BEST = "tentative_best"  # best, but the winner carries a warning flag


# Portal floor, cents-per-point.
# Capital One: fixed by product (Venture / Venture X).
# Other currencies: honest baseline portal rates (not Points Boost peaks).
# Sources: issuer travel portals / NerdWallet / TPG — Jul 2026.
# Keyed as "{currency}:{card}" or "{currency}" for a currency-wide floor.
PORTAL_CPP: dict[str, float] = {
    # Capital One
    "capital_one:venture": 1.0,
    "capital_one:venture_x": 1.25,
    "venture": 1.0,          # legacy card-only keys
    "venture_x": 1.25,
    # Chase Travel baseline is 1.0¢ after Points Boost reform (Boost is
    # variable 1.5–2.0¢ — we use the guaranteed floor, not the peak).
    "chase_ur": 1.0,
    "chase_ur:sapphire_preferred": 1.0,
    "chase_ur:sapphire_reserve": 1.0,
    # Amex MR: flights via Amex Travel generally ~1.0¢.
    "amex_mr": 1.0,
    # Citi Travel: ~1.0¢ for ThankYou.
    "citi_typ": 1.0,
    # Bilt Travel portal: 1.25¢ advertised for travel.
    "bilt": 1.25,
    # Wells Fargo Rewards travel redemption ~1.0¢.
    "wells_fargo": 1.0,
    # Hotel banks have no airline "portal floor" comparable to card travel
    # portals — omit marriott_bonvoy / hilton so we don't invent one.
}

# Currencies that have an honest, bookable travel-portal floor.
PORTAL_CURRENCIES: frozenset[str] = frozenset(
    {
        "capital_one",
        "chase_ur",
        "amex_mr",
        "citi_typ",
        "bilt",
        "wells_fargo",
    }
)


def portal_cpp_for(currency: str, card: str = "venture_x") -> Optional[float]:
    """Return portal CPP if this currency has a real portal floor, else None."""
    if currency not in PORTAL_CURRENCIES:
        return None
    keyed = f"{currency}:{card}"
    if keyed in PORTAL_CPP:
        return PORTAL_CPP[keyed]
    if currency in PORTAL_CPP:
        return PORTAL_CPP[currency]
    # Cap One legacy: card name alone.
    if currency == "capital_one" and card in PORTAL_CPP:
        return PORTAL_CPP[card]
    return None



def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Provenance — attached to every datum
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Provenance:
    """Where a datum came from, how much we trust it, and how old it is."""

    source_name: str
    source_url: Optional[str] = None
    fetched_at: datetime = field(default_factory=utcnow)
    # Trust weight in [0, 1]; authoritative sources (Capital One) ~1.0.
    trust: float = 0.5
    # When the *source* last updated the underlying value (vs. when we fetched).
    source_updated_at: Optional[datetime] = None

    def age_seconds(self, *, now: Optional[datetime] = None) -> float:
        now = now or utcnow()
        basis = self.source_updated_at or self.fetched_at
        return max(0.0, (now - basis).total_seconds())


# --------------------------------------------------------------------------- #
# Route + user
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Route:
    origin: str          # IATA, e.g. "LAX"
    dest: str            # IATA, e.g. "JFK"
    cabin: Cabin = Cabin.ECONOMY

    def __post_init__(self) -> None:
        object.__setattr__(self, "origin", self.origin.upper())
        object.__setattr__(self, "dest", self.dest.upper())

    def key(self) -> str:
        return f"{self.origin}-{self.dest}-{self.cabin.value}"


@dataclass
class User:
    """The only user-scoped data in the system (§9). Market data is shared.

    Multi-user-ready: a `user_id` exists from Phase 0 even though the CLI runs
    single-user, so the Repository can carry a user dimension later without a
    schema rewrite.
    """

    user_id: str = "local"
    # currency -> point balance, e.g. {"capital_one": 20000}
    balances: dict[str, int] = field(default_factory=dict)
    # Card product within a currency (Cap One Venture/X; Chase Sapphire, …).
    card: str = "venture_x"
    preferences: dict[str, str] = field(default_factory=dict)

    def portal_cpp(self, currency: str = "capital_one") -> Optional[float]:
        """Honest portal floor for `currency`, or None if none exists."""
        return portal_cpp_for(currency, self.card)


# --------------------------------------------------------------------------- #
# Normalized quotes (provider output)
# --------------------------------------------------------------------------- #
@dataclass
class FareQuote:
    """L2 — the cash price-to-beat for a route/cabin, in US cents."""

    route: Route
    cash_cents: int
    currency: str = "USD"
    provenance: Provenance = field(
        default_factory=lambda: Provenance(source_name="unknown")
    )
    confidence: float = 0.5
    flags: list[str] = field(default_factory=list)

    @property
    def cash_dollars(self) -> float:
        return self.cash_cents / 100.0


@dataclass
class AwardQuote:
    """L3/L4 — miles required to fly a route/cabin in a given program.

    A chart-derived quote (no confirmed seat) carries the `no_live_space` flag;
    a live-availability quote (Phase 1+) does not.
    """

    program: str               # e.g. "turkish", "aeroplan", "lifemiles"
    route: Route
    miles: int                 # one-way miles in the program's own currency
    seats_available: Optional[int] = None  # None => unknown (chart-only)
    # Taxes / carrier surcharges still owed in USD cents when booking the award.
    # None = unknown; 0 = confirmed zero. Charts often omit these — use
    # knowledge/surcharges.yaml estimates when live tax quotes are missing.
    taxes_cents: Optional[int] = None
    provenance: Provenance = field(
        default_factory=lambda: Provenance(source_name="unknown")
    )
    confidence: float = 0.5
    flags: list[str] = field(default_factory=list)


@dataclass
class TransferRatio:
    """L4 — how a transferable currency converts into a program.

    ratio = program_points received per 1 source point (base rate). Capital One
    -> most Star Alliance partners is 1:1. Capital One -> United does NOT exist;
    that absence is load-bearing and is represented by the lack of a row.

    Optional transfer bonuses: ``bonus_multiplier`` (e.g. 1.3 = +30%) applied
    on top of ``ratio`` while ``valid_from``/``valid_until`` (ISO dates) contain
    "today". Effective ratio = ratio * bonus_multiplier. Inactive bonus rows
    are filtered out by the curated loader before they reach the graph.
    """

    from_currency: str         # e.g. "capital_one"
    to_program: str            # e.g. "turkish"
    ratio: float = 1.0
    provenance: Provenance = field(
        default_factory=lambda: Provenance(source_name="unknown")
    )
    confidence: float = 1.0
    flags: list[str] = field(default_factory=list)
    bonus_multiplier: float = 1.0
    valid_from: Optional[str] = None   # ISO date inclusive, or None
    valid_until: Optional[str] = None  # ISO date inclusive, or None
    bonus_label: Optional[str] = None  # e.g. "+30% transfer bonus"

    @property
    def effective_ratio(self) -> float:
        return self.ratio * self.bonus_multiplier

    @property
    def is_bonus(self) -> bool:
        return self.bonus_multiplier != 1.0 or "transfer_bonus" in self.flags


# --------------------------------------------------------------------------- #
# Optimizer output
# --------------------------------------------------------------------------- #
@dataclass
class PathOption:
    """One concrete way to pay for the seat, ranked by cents-per-point."""

    label: str                 # human label, e.g. "Capital One -> Turkish"
    kind: str                  # "portal" | "transfer"
    cpp: float                 # cents per source point (net of award taxes)
    source_points: int         # source-currency points required
    cash_cents: int            # cash value being unlocked (gross fare)
    program: Optional[str] = None
    affordable: bool = True    # does the user hold enough points?
    confidence: float = 0.5
    flags: list[str] = field(default_factory=list)
    provenance: list[Provenance] = field(default_factory=list)
    taxes_cents: int = 0       # award taxes/surcharges still owed
    currency: Optional[str] = None  # source currency for multi-wallet ranking


@dataclass
class Verdict:
    label: VerdictLabel
    route: Route
    portal: Optional[PathOption]
    best_transfer: Optional[PathOption]
    options: list[PathOption]
    rationale: str
    flags: list[str] = field(default_factory=list)
