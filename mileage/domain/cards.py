"""Card products and the §4.1 gates they satisfy.

Two facts a card decides: what its travel portal pays per point, and whether it
can transfer to airline partners at all. The second is a HARD gate — several
issuers only open partner transfers from their premium tiers, and a route that
depends on one is not bookable today by someone holding only a no-fee card.

Gated routes are still shown (§6.3). "This card would unlock this trip, and it
costs $95" is the useful answer, and it is only useful if the route is visible.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

from .models import Gate, GateKind

_KNOWLEDGE = Path(__file__).resolve().parent.parent / "knowledge"


@dataclass(frozen=True)
class Card:
    id: str
    name: str
    currency: str
    portal_cpp: Optional[float] = None
    annual_fee_usd: float = 0.0
    transfers_to_partners: bool = True


@dataclass(frozen=True)
class CobrandCard:
    id: str
    name: str
    program: str
    annual_fee_usd: float = 0.0
    approval_days: int = 14


@dataclass(frozen=True)
class CardTable:
    cards: dict[str, Card]
    cobrand: dict[str, CobrandCard]

    def get(self, card_id: str) -> Optional[Card]:
        return self.cards.get(card_id)

    def ids(self) -> list[str]:
        return sorted(self.cards)

    def for_currency(self, currency: str) -> list[Card]:
        return [c for c in self.cards.values() if c.currency == currency]

    def transfer_capable(self, currency: str) -> tuple[str, ...]:
        """Cards of `currency` that may transfer to airline partners."""
        return tuple(
            sorted(
                c.id
                for c in self.cards.values()
                if c.currency == currency and c.transfers_to_partners
            )
        )

    def transfer_gate(self, currency: str) -> Optional[Gate]:
        """The HARD gate on transferring this currency out, if there is one.

        Returns None when every card of the currency can transfer — inventing a
        gate that doesn't exist would push real routes into the gated list.
        """
        capable = self.transfer_capable(currency)
        all_cards = {c.id for c in self.cards.values() if c.currency == currency}
        if not capable or set(capable) == all_cards:
            return None
        return Gate(
            kind=GateKind.HARD,
            card_ids=capable,
            note=f"{currency} transfers require a premium card",
        )

    def cobrand_gate(self, program: str) -> Optional[Gate]:
        """Acquirable co-brand gate for `program`, if one exists."""
        matches = [c for c in self.cobrand.values() if c.program == program]
        if not matches:
            return None
        cheapest = min(matches, key=lambda c: c.annual_fee_usd)
        return Gate(
            kind=GateKind.ACQUIRABLE,
            card_ids=(cheapest.id,),
            annual_fee_usd=cheapest.annual_fee_usd,
            approval_days=cheapest.approval_days,
        )


def load_cards(knowledge_dir: Optional[Path] = None) -> CardTable:
    root = Path(knowledge_dir) if knowledge_dir else _KNOWLEDGE
    path = root / "cards.yaml"
    if not path.exists():
        return CardTable(cards={}, cobrand={})
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    cards: dict[str, Card] = {}
    for cid, spec in (data.get("cards") or {}).items():
        if not isinstance(spec, dict):
            continue
        cards[str(cid)] = Card(
            id=str(cid),
            name=str(spec.get("name") or cid),
            currency=str(spec.get("currency") or ""),
            portal_cpp=(
                float(spec["portal_cpp"]) if spec.get("portal_cpp") is not None else None
            ),
            annual_fee_usd=float(spec.get("annual_fee_usd") or 0),
            transfers_to_partners=bool(spec.get("transfers_to_partners", True)),
        )

    cobrand: dict[str, CobrandCard] = {}
    for cid, spec in (data.get("cobrand") or {}).items():
        if not isinstance(spec, dict):
            continue
        cobrand[str(cid)] = CobrandCard(
            id=str(cid),
            name=str(spec.get("name") or cid),
            program=str(spec.get("program") or ""),
            annual_fee_usd=float(spec.get("annual_fee_usd") or 0),
            approval_days=int(spec.get("approval_days") or 14),
        )

    return CardTable(cards=cards, cobrand=cobrand)


@lru_cache(maxsize=8)
def _cached(root: str) -> CardTable:
    return load_cards(Path(root))


def cards(knowledge_dir: Optional[Path] = None) -> CardTable:
    return _cached(str(Path(knowledge_dir) if knowledge_dir else _KNOWLEDGE))


__all__ = ["Card", "CardTable", "CobrandCard", "cards", "load_cards"]
