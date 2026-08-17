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


class AwardSpace(str, Enum):
    """Three states, because two is a lie.

    The system previously had one flag, `no_live_space`, covering two entirely
    different situations: "we asked and there are no seats" and "nothing ever
    asked". Twelve days of sweeps showed ~50 path-slots reporting the same
    `no_live_space` with zero variation — which turned out to be the second
    case wearing the first case's clothes. A silent default that reads as a
    confirmed negative is the worst failure mode available here, because it
    looks exactly like a real answer.

    CONFIRMED — a live source returned seats for this program/route.
    NONE      — a live source WAS queried and returned zero. A real negative.
    UNKNOWN   — no live source covered this. We do not know. Not a negative.
    """

    CONFIRMED = "space_confirmed"
    NONE = "no_space"
    UNKNOWN = "space_unknown"

    @property
    def label(self) -> str:
        return {
            AwardSpace.CONFIRMED: "seats confirmed",
            AwardSpace.NONE: "checked — no seats",
            AwardSpace.UNKNOWN: "availability not checked",
        }[self]


# Ranking order for §6.1 step 1. Cabin class dominates every other criterion.
CLASS_RANK: dict[str, int] = {
    Cabin.FIRST.value: 4,
    Cabin.BUSINESS.value: 3,
    Cabin.PREMIUM_ECONOMY.value: 2,
    Cabin.ECONOMY.value: 1,
}


# --------------------------------------------------------------------------- #
# Gates (§4.1) — requirements that qualify a route without hiding it
# --------------------------------------------------------------------------- #
class GateKind(str, Enum):
    HARD = "hard"              # bank tier requirement (Chase premium card, …)
    ACQUIRABLE = "acquirable"  # co-brand card — anyone can apply
    ACCOUNT_AGE = "accountAge"  # e.g. Iberia Plus needs a 90-day-old account


@dataclass(frozen=True)
class Gate:
    """A requirement on a route. Gated routes are NEVER hidden (§6.3).

    `acquirable` gates never disqualify anything — anyone can apply for a
    co-brand card — so they annotate rather than filter. `hard` and
    `accountAge` gates can genuinely block a booking today, but the route is
    still shown, because "this card would unlock this trip" is itself the
    useful answer.
    """

    kind: GateKind
    card_ids: tuple[str, ...] = ()
    program_id: Optional[str] = None
    min_days: Optional[int] = None
    annual_fee_usd: Optional[float] = None
    approval_days: Optional[int] = None
    note: str = ""

    def satisfied_by(self, cards: frozenset[str], account_age_days: dict[str, int]) -> bool:
        if self.kind is GateKind.HARD:
            return bool(cards & set(self.card_ids))
        if self.kind is GateKind.ACQUIRABLE:
            return True  # never disqualifying — annotate only
        if self.kind is GateKind.ACCOUNT_AGE:
            if self.program_id is None or self.min_days is None:
                return True
            # We usually have NO idea how old someone's Iberia Plus account is.
            # Treating "unknown" as "too young" would bury a good route behind a
            # requirement we never checked — the same mistake as reporting
            # `no_space` for availability nobody looked up. Unknown means the
            # route stays visible and carries the requirement as a caveat;
            # only a known-too-young account actually blocks.
            known = account_age_days.get(self.program_id)
            if known is None:
                return True
            return known >= self.min_days
        return True

    def is_advisory(self, account_age_days: dict[str, int]) -> bool:
        """True when this gate informs rather than blocks for this user."""
        if self.kind is GateKind.ACQUIRABLE:
            return True
        if self.kind is GateKind.ACCOUNT_AGE and self.program_id is not None:
            return account_age_days.get(self.program_id) is None
        return False

    def describe(self) -> str:
        if self.kind is GateKind.HARD:
            cards = ", ".join(c.replace("_", " ").title() for c in self.card_ids)
            return f"requires {cards}"
        if self.kind is GateKind.ACQUIRABLE:
            cards = ", ".join(c.replace("_", " ").title() for c in self.card_ids)
            fee = f" — ${self.annual_fee_usd:,.0f}/yr" if self.annual_fee_usd else ""
            wait = f", ~{self.approval_days}d approval" if self.approval_days else ""
            return f"requires {cards}{fee}{wait}"
        if self.kind is GateKind.ACCOUNT_AGE:
            prog = (self.program_id or "").replace("_", " ").title()
            return f"{prog} account must be {self.min_days}+ days old"
        return self.note or "gated"


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
    # Which airline's metal this award books. Required to price surcharges,
    # because the same currency prices very differently by operating carrier
    # (§4.4). None means the source didn't say.
    operating_carrier: Optional[str] = None
    carrier_name: Optional[str] = None
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
    # §4.1 — requirements attached to this edge. Never used to hide a route.
    gates: list[Gate] = field(default_factory=list)
    # How long the points take to land. Ranking tiebreak (§6.1 step 5) and a
    # real booking risk: award space can vanish while a transfer settles.
    settlement_minutes: int = 0
    min_qty: int = 0
    increment_qty: int = 1

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
    """One concrete way to pay for the seat.

    Two cash numbers, deliberately distinct:

      price_paid_cents — what LEAVES YOUR POCKET: taxes + fuel charges, from
                         our own tables (§4.4). Always known. This is the
                         number §6.2 puts on screen.
      cash_cents       — the market fare this redemption displaces. Requires an
                         external fare feed, which §1 puts out of scope for v1,
                         so it is frequently 0 and `cpp` is then None.

    `cpp` is Optional for that reason. It used to be a float that silently read
    0.0 with no fare, and the pipeline responded by throwing away a fully
    computed ranking and returning an error string. Points and price paid are
    always available, so a missing fare degrades one column rather than the
    whole answer.
    """

    label: str                 # human label, e.g. "Capital One -> Turkish"
    kind: str                  # "portal" | "transfer"
    source_points: int         # source-currency points required
    cpp: Optional[float] = None  # cents per source point; None = no fare basis
    cash_cents: int = 0        # market fare displaced (0 when unknown)
    price_paid_cents: int = 0  # taxes + fuel charges — always known
    program: Optional[str] = None
    affordable: bool = True    # does the user hold enough points?
    confidence: float = 0.5
    flags: list[str] = field(default_factory=list)
    provenance: list[Provenance] = field(default_factory=list)
    taxes_cents: int = 0       # government + airport portion of price paid
    fuel_cents: int = 0        # carrier-imposed portion of price paid
    fuel_policy: Optional[str] = None
    currency: Optional[str] = None  # source currency for multi-wallet ranking
    cabin: str = Cabin.ECONOMY.value
    operating_carrier: Optional[str] = None  # the metal you actually fly
    carrier_name: Optional[str] = None
    space: AwardSpace = AwardSpace.UNKNOWN
    transfer_hops: int = 0
    settlement_minutes: int = 0
    gates: list[Gate] = field(default_factory=list)
    # Why this row ranks where it does / why it is or isn't the winner. The
    # sweep showed `best` vs `tentative_best` was not reconstructable from the
    # output — if a reader can't infer the rule from the data, neither can a
    # user, so the rule is stated rather than implied.
    reason: str = ""

    @property
    def price_paid_usd(self) -> float:
        return self.price_paid_cents / 100.0

    @property
    def blocking_gates(self) -> list[Gate]:
        """Gates that could stop a booking today. Acquirable ones never do."""
        return [g for g in self.gates if g.kind is not GateKind.ACQUIRABLE]


@dataclass
class Verdict:
    label: VerdictLabel
    route: Route
    portal: Optional[PathOption]
    best_transfer: Optional[PathOption]
    options: list[PathOption]
    rationale: str
    flags: list[str] = field(default_factory=list)
    # The RULE that produced this label, in words. Twelve days of sweep output
    # left `best` vs `tentative_best` unreconstructable by a careful reader —
    # so the deciding rule is now stated instead of implied.
    reason: str = ""
    # §6.3 — routes held back by a card or account-age gate. Never hidden;
    # surfaced separately so the UI can offer "3 routes require a Sapphire card ›".
    gated: list[PathOption] = field(default_factory=list)
