"""Alliance membership + program→program transfer hops.

Pure logic + YAML load helpers. Data lives in ``knowledge/alliances.yaml``.
Airline programs rarely transfer *out*; alliance booking (redeem program A
miles on carrier B metal) is labeled on paths, not modeled as a second transfer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import yaml

from .models import Gate, GateKind, Provenance


def parse_gates(rows: Optional[Iterable[dict]]) -> list[Gate]:
    """Parse §4.1 gate specs from YAML. Unknown kinds are dropped loudly-ish.

    A gate never removes a route (§6.3) — it annotates one. Parsing failures
    therefore fail open (no gate) rather than silently marking a route blocked,
    which would hide it from the very list that exists to show it.
    """
    out: list[Gate] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        raw = str(row.get("kind") or "")
        try:
            kind = GateKind(raw)
        except ValueError:
            continue
        out.append(
            Gate(
                kind=kind,
                card_ids=tuple(str(c) for c in (row.get("cardIds") or row.get("cards") or [])),
                program_id=(
                    str(row["program"]) if row.get("program") else row.get("programId")
                ),
                min_days=int(row["min_days"]) if row.get("min_days") is not None else None,
                annual_fee_usd=(
                    float(row["annual_fee_usd"])
                    if row.get("annual_fee_usd") is not None
                    else None
                ),
                approval_days=(
                    int(row["approval_days"])
                    if row.get("approval_days") is not None
                    else None
                ),
                note=str(row.get("note") or ""),
            )
        )
    return out


@dataclass(frozen=True)
class Alliance:
    id: str
    name: str
    programs: frozenset[str]


@dataclass
class ProgramTransfer:
    """Loyalty→loyalty point move (second hop), not alliance booking."""

    from_program: str
    to_program: str
    ratio: float = 1.0
    provenance: Provenance = field(
        default_factory=lambda: Provenance(source_name="program transfer")
    )
    confidence: float = 0.7
    flags: list[str] = field(default_factory=lambda: ["program_transfer"])
    bonus_multiplier: float = 1.0
    bonus_label: Optional[str] = None
    gates: list[Gate] = field(default_factory=list)
    settlement_minutes: int = 0

    @property
    def effective_ratio(self) -> float:
        return self.ratio * self.bonus_multiplier


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def load_alliances_yaml(path: Path) -> tuple[dict[str, Alliance], list[ProgramTransfer]]:
    if not path.exists():
        return {}, []
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    alliances: dict[str, Alliance] = {}
    for aid, spec in (data.get("alliances") or {}).items():
        if not isinstance(spec, dict):
            continue
        programs = frozenset(str(p) for p in (spec.get("programs") or []))
        alliances[str(aid)] = Alliance(
            id=str(aid),
            name=str(spec.get("name") or aid.replace("_", " ").title()),
            programs=programs,
        )

    transfers: list[ProgramTransfer] = []
    for row in data.get("program_transfers") or []:
        if not isinstance(row, dict):
            continue
        frm = row.get("from") or row.get("from_program")
        to = row.get("to") or row.get("to_program")
        if not frm or not to:
            continue
        trust = float(row.get("trust", 0.7))
        transfers.append(
            ProgramTransfer(
                from_program=str(frm),
                to_program=str(to),
                ratio=float(row.get("ratio", 1.0)),
                provenance=Provenance(
                    source_name=str(row.get("source", "program transfer")),
                    source_url=row.get("url"),
                    trust=trust,
                    source_updated_at=_parse_date(row.get("updated_at")),
                ),
                confidence=trust,
                flags=["program_transfer"],
                gates=parse_gates(row.get("gates")),
                settlement_minutes=int(row.get("settlement_minutes") or 0),
            )
        )
    return alliances, transfers


def program_to_alliance(alliances: dict[str, Alliance]) -> dict[str, Alliance]:
    """Map each program id → its alliance (independent last if overlapping)."""
    out: dict[str, Alliance] = {}
    # Prefer major alliances over "independent" when a program appears once.
    for aid, alliance in alliances.items():
        if aid == "independent":
            continue
        for prog in alliance.programs:
            out[prog] = alliance
    indep = alliances.get("independent")
    if indep:
        for prog in indep.programs:
            out.setdefault(prog, indep)
    return out



def currency_display_name(currency: str) -> str:
    """Human label for path prefixes (Capital One, Amex Mr, …)."""
    special = {
        "capital_one": "Capital One",
        "amex_mr": "Amex MR",
        "chase_ur": "Chase UR",
        "citi_typ": "Citi ThankYou",
        "bilt": "Bilt",
        "wells_fargo": "Wells Fargo",
        "marriott_bonvoy": "Marriott Bonvoy",
        "hilton": "Hilton Honors",
    }
    if currency in special:
        return special[currency]
    return currency.replace("_", " ").title()


