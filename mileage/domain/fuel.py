"""§4.4 + §6.2 — price paid, from our own tables.

    price_paid = government taxes + airport add-ons + carrier surcharge

Every term comes from knowledge/fuel_charges.yaml and knowledge/airports.yaml.
Nothing here needs a live cash fare, which is what lets §1 drop the external
market-fare dependency and still put a dollar figure in front of the user.

The load-bearing detail is the key: (award currency × OPERATING CARRIER), not
the program. Keyed on the program alone, Avios-on-JAL and Avios-on-BA collapse
to one number and the engine confidently ranks the worse one first. That
collapse is exactly the bug this module exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

from .geo import AirportTable, airports, route_distance_miles, haul_band
from .models import Cabin, Route

_KNOWLEDGE = Path(__file__).resolve().parent.parent / "knowledge"

WILDCARD = "*"

# Above this, a route is demoted a tier in ranking (§6.1 step 2). One boolean,
# not a valuation — without market fares there is no honest way to trade points
# against dollars, but a 60k-point award carrying $600 of surcharges must not
# outrank a 70k-point award carrying $5.
HIGH_FUEL_USD = 300.0


@dataclass(frozen=True)
class PricePaid:
    """The cash actually leaving the user's pocket, broken out by cause."""

    total_cents: int
    taxes_cents: int          # government + airport
    fuel_cents: int           # carrier-imposed surcharge
    policy: str               # none | minimal | moderate | passes_full
    policy_label: str
    carrier: Optional[str] = None
    confidence: float = 0.55
    flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def total_usd(self) -> float:
        return self.total_cents / 100.0

    @property
    def is_high_fuel(self) -> bool:
        return self.total_usd > HIGH_FUEL_USD


@dataclass(frozen=True)
class FuelMatrix:
    policies: dict[str, dict]
    cabin_multiplier: dict[str, float]
    taxes: dict[str, dict[str, int]]
    airport_taxes: dict[str, dict]
    matrix: dict[tuple[str, str], dict]
    default_policy: str
    confidence: float

    def policy_for(self, currency: str, carrier: Optional[str]) -> tuple[str, dict, bool]:
        """Resolve (currency, carrier) → policy name, row, exact_match?

        Specific carrier beats the currency wildcard; absence of both falls back
        to `default_policy` and is reported as inexact so the caller can flag it.
        """
        if carrier:
            row = self.matrix.get((currency, carrier.upper()))
            if row is not None:
                return str(row.get("policy", self.default_policy)), row, True
        row = self.matrix.get((currency, WILDCARD))
        if row is not None:
            return str(row.get("policy", self.default_policy)), row, True
        return self.default_policy, {}, False


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# §4 "`.strict()` everywhere — a typo'd YAML key should fail the build, not be
# silently ignored." A bare `on:` key parses as boolean True under YAML 1.1,
# which is how the directional airport tax silently stopped applying.
_AIRPORT_TAX_KEYS = frozenset({"levied_on", "cents", "note"})
_MATRIX_KEYS = frozenset({"currency", "carrier", "policy", "note"})
_LEVIED_ON = frozenset({"departure", "any"})


class FuelMatrixError(ValueError):
    """A malformed fuel_charges.yaml. Raised at load, never worked around."""


def _validate_airport_taxes(raw: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for code, spec in (raw or {}).items():
        if not isinstance(code, str):
            raise FuelMatrixError(
                f"airport_taxes key {code!r} is not a string — a bare YAML token "
                "such as `on`, `no` or `yes` parses as a boolean; quote it"
            )
        if not isinstance(spec, dict):
            raise FuelMatrixError(f"airport_taxes[{code}] must be a mapping")
        unknown = {k for k in spec if not isinstance(k, str)} | (
            {k for k in spec if isinstance(k, str)} - _AIRPORT_TAX_KEYS
        )
        if unknown:
            raise FuelMatrixError(
                f"airport_taxes[{code}] has unknown key(s) {sorted(map(str, unknown))}; "
                f"expected {sorted(_AIRPORT_TAX_KEYS)}"
            )
        levied = str(spec.get("levied_on") or "any")
        if levied not in _LEVIED_ON:
            raise FuelMatrixError(
                f"airport_taxes[{code}].levied_on={levied!r}, expected one of "
                f"{sorted(_LEVIED_ON)}"
            )
        out[code.upper()] = spec
    return out


def load_fuel_matrix(knowledge_dir: Optional[Path] = None) -> FuelMatrix:
    root = Path(knowledge_dir) if knowledge_dir else _KNOWLEDGE
    data = _load_yaml(root / "fuel_charges.yaml")

    policies = data.get("policies") or {}
    matrix: dict[tuple[str, str], dict] = {}
    for row in data.get("matrix") or []:
        if not isinstance(row, dict) or not row.get("currency"):
            continue
        unknown = set(map(str, row)) - _MATRIX_KEYS
        if unknown:
            raise FuelMatrixError(
                f"matrix row {row.get('currency')}/{row.get('carrier')} has unknown "
                f"key(s) {sorted(unknown)}; expected {sorted(_MATRIX_KEYS)}"
            )
        policy = str(row.get("policy") or "")
        if policy not in policies:
            raise FuelMatrixError(
                f"matrix row {row.get('currency')}/{row.get('carrier')} references "
                f"undefined policy {policy!r}; defined: {sorted(policies)}"
            )
        carrier = str(row.get("carrier") or WILDCARD)
        key = (str(row["currency"]), carrier if carrier == WILDCARD else carrier.upper())
        matrix[key] = row

    taxes = {}
    for band, by_cabin in (data.get("taxes", {}).get("default") or {}).items():
        taxes[str(band)] = {str(c): int(v) for c, v in (by_cabin or {}).items()}

    return FuelMatrix(
        policies=policies,
        cabin_multiplier={
            str(k): float(v) for k, v in (data.get("cabin_multiplier") or {}).items()
        },
        taxes=taxes,
        airport_taxes=_validate_airport_taxes(data.get("airport_taxes") or {}),
        matrix=matrix,
        default_policy=str(data.get("default_policy") or "moderate"),
        confidence=float(data.get("confidence", 0.55)),
    )


@lru_cache(maxsize=8)
def _cached_matrix(root: str) -> FuelMatrix:
    return load_fuel_matrix(Path(root))


def fuel_matrix(knowledge_dir: Optional[Path] = None) -> FuelMatrix:
    return _cached_matrix(str(Path(knowledge_dir) if knowledge_dir else _KNOWLEDGE))


def _airport_addons(
    route: Route, cabin: str, matrix: FuelMatrix
) -> tuple[int, list[str]]:
    """Departure-directional airport taxes (UK APD and friends).

    Direction matters: APD is levied leaving the UK, so LHR→JFK carries it and
    JFK→LHR does not. Treating it as symmetric would overstate half of every
    transatlantic award by ~$216 in a premium cabin.
    """
    total = 0
    notes: list[str] = []
    for code, spec in matrix.airport_taxes.items():
        levied_on = str(spec.get("levied_on") or "any")
        if levied_on == "departure":
            touches = route.origin.upper() == code
        else:
            touches = code in (route.origin.upper(), route.dest.upper())
        if not touches:
            continue
        cents = (spec.get("cents") or {}).get(cabin)
        if cents is None:
            continue
        total += int(cents)
        if spec.get("note"):
            notes.append(f"{code.upper()}: {spec['note']}")
    return total, notes


def price_paid(
    currency: str,
    carrier: Optional[str],
    route: Route,
    *,
    matrix: Optional[FuelMatrix] = None,
    table: Optional[AirportTable] = None,
    distance_miles: Optional[float] = None,
) -> PricePaid:
    """Cash owed at booking for this award, in this currency, on this metal."""
    matrix = matrix or fuel_matrix()
    table = table or airports()
    cabin = route.cabin.value

    if distance_miles is None:
        distance_miles = route_distance_miles(route, table=table)
    flags: list[str] = ["estimated_surcharge"]
    notes: list[str] = []

    if distance_miles is None:
        # Unknown airport: haul band is unresolvable. Assume the long band
        # rather than the cheap one — an unknown must not look like a bargain.
        band = "long"
        flags.append("distance_unknown")
    else:
        band = haul_band(distance_miles)

    policy_name, row, exact = matrix.policy_for(currency, carrier)
    if not exact:
        flags.append("fuel_policy_unknown")
    policy = matrix.policies.get(policy_name) or {}

    base = int((policy.get("surcharge_cents") or {}).get(band, 0))
    mult = matrix.cabin_multiplier.get(cabin, 1.0)
    fuel_cents = int(round(base * mult))

    gov = int((matrix.taxes.get(band) or {}).get(cabin, 0))
    addons, addon_notes = _airport_addons(route, cabin, matrix)
    notes.extend(addon_notes)
    if row.get("note"):
        notes.append(str(row["note"]))

    taxes_cents = gov + addons
    total = taxes_cents + fuel_cents
    if total / 100.0 > HIGH_FUEL_USD:
        flags.append("high_cash_outlay")

    return PricePaid(
        total_cents=total,
        taxes_cents=taxes_cents,
        fuel_cents=fuel_cents,
        policy=policy_name,
        policy_label=str(policy.get("label") or policy_name),
        carrier=carrier.upper() if carrier else None,
        confidence=matrix.confidence if exact else matrix.confidence * 0.7,
        flags=flags,
        notes=notes,
    )


def format_usd(cents: int) -> str:
    """Display form for §6.2. Whole dollars — cents are noise at this precision."""
    return f"${cents / 100:,.0f}"


__all__ = [
    "HIGH_FUEL_USD",
    "FuelMatrix",
    "PricePaid",
    "format_usd",
    "fuel_matrix",
    "load_fuel_matrix",
    "price_paid",
]
