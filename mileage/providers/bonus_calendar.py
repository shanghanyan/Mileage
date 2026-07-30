"""Transfer-bonus calendar — scrape live offers, don't hand-seed ratios.yaml.

Primary source: Roame's points-transfer-bonuses page (JSON-LD Offer schema).
Results are written to knowledge/bonus_calendar.yaml and merged into transfer
ratios at quote time by CuratedProvider / bonus overlay.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from ..domain.models import Provenance, TransferRatio

DEFAULT_URL = "https://roame.travel/guides/points-transfer-bonuses"

# Map scraped issuer / program names → our currency / program ids.
_CURRENCY_ALIASES = {
    "capital one": "capital_one",
    "chase": "chase_ur",
    "chase ultimate rewards": "chase_ur",
    "amex": "amex_mr",
    "amex membership rewards": "amex_mr",
    "american express": "amex_mr",
    "citi": "citi_typ",
    "citi thankyou": "citi_typ",
    "bilt": "bilt",
    "bilt rewards": "bilt",
    "wells fargo": "wells_fargo",
}

_PROGRAM_ALIASES = {
    "eva infinity mileagelands": "eva",
    "eva": "eva",
    "virgin atlantic flying club": "virgin_atlantic",
    "virgin atlantic": "virgin_atlantic",
    "air france/klm flying blue": "flying_blue",
    "air france / klm flying blue": "flying_blue",
    "flying blue": "flying_blue",
    "ihg one rewards": "ihg",
    "ihg": "ihg",
    "avianca lifemiles": "lifemiles",
    "lifemiles": "lifemiles",
    "air canada aeroplan": "aeroplan",
    "aeroplan": "aeroplan",
    "turkish miles&smiles": "turkish",
    "turkish": "turkish",
    "british airways": "avios",
    "british airways executive club": "avios",
    "avios": "avios",
    "qatar airways privilege club": "qatar",
    "hilton honors": "hilton",
    "marriott bonvoy": "marriott_bonvoy",
    "frontier miles": "frontier",
    "qantas frequent flyer": "qantas",
    "jetblue trueblue": "jetblue",
    "united mileageplus": "united",
    "hyatt": "hyatt",
    "world of hyatt": "hyatt",
}


@dataclass
class BonusOffer:
    from_currency: str
    to_program: str
    bonus_multiplier: float
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    label: Optional[str] = None
    source_url: str = DEFAULT_URL
    status: str = "active"  # active | expired | upcoming
    raw_name: str = ""

    @property
    def pct(self) -> int:
        return int(round((self.bonus_multiplier - 1.0) * 100))


def _parse_iso_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _normalize_currency(name: str) -> Optional[str]:
    key = name.strip().lower()
    if key in _CURRENCY_ALIASES:
        return _CURRENCY_ALIASES[key]
    for alias, cid in _CURRENCY_ALIASES.items():
        if alias in key:
            return cid
    return None


def _normalize_program(name: str) -> Optional[str]:
    key = name.strip().lower()
    if key in _PROGRAM_ALIASES:
        return _PROGRAM_ALIASES[key]
    for alias, pid in _PROGRAM_ALIASES.items():
        if alias in key:
            return pid
    # last resort: slug
    slug = re.sub(r"[^a-z0-9]+", "_", key).strip("_")
    return slug or None


_OFFER_NAME_RE = re.compile(
    r"(?P<pct>\d+)%\s+transfer bonus from\s+(?P<from>.+?)\s+to\s+(?P<to>.+?)\s*$",
    re.I,
)


def parse_offer_name(name: str) -> Optional[tuple[str, str, float]]:
    m = _OFFER_NAME_RE.search(name.strip())
    if not m:
        return None
    pct = int(m.group("pct"))
    frm = _normalize_currency(m.group("from"))
    to = _normalize_program(m.group("to"))
    if not frm or not to:
        return None
    return frm, to, 1.0 + pct / 100.0


def parse_json_ld_offers(html: str, *, today: Optional[date] = None) -> list[BonusOffer]:
    """Extract Offer nodes that look like transfer bonuses."""
    import json

    today = today or date.today()
    offers: list[BonusOffer] = []
    for block in re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.S
    ):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            obj = stack.pop()
            if isinstance(obj, list):
                stack.extend(obj)
                continue
            if not isinstance(obj, dict):
                continue
            for v in obj.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)
            if obj.get("@type") != "Offer":
                continue
            name = str(obj.get("name") or "")
            if "transfer bonus" not in name.lower():
                continue
            parsed = parse_offer_name(name)
            if not parsed:
                continue
            frm, to, mult = parsed
            vf = str(obj.get("validFrom") or "")[:10] or None
            vu = str(obj.get("validThrough") or "")[:10] or None
            start = _parse_iso_date(vf)
            end = _parse_iso_date(vu)
            if end is not None and today > end:
                status = "expired"
            elif start is not None and today < start:
                status = "upcoming"
            else:
                status = "active"
            pct = int(round((mult - 1.0) * 100))
            offers.append(
                BonusOffer(
                    from_currency=frm,
                    to_program=to,
                    bonus_multiplier=mult,
                    valid_from=vf,
                    valid_until=vu,
                    label=f"+{pct}% {to.replace('_', ' ').title()} transfer bonus",
                    status=status,
                    raw_name=name,
                )
            )
    # de-dupe by (currency, program, until)
    seen: set[tuple] = set()
    unique: list[BonusOffer] = []
    for o in offers:
        key = (o.from_currency, o.to_program, o.valid_until, o.bonus_multiplier)
        if key in seen:
            continue
        seen.add(key)
        unique.append(o)
    return unique


def fetch_roame_bonuses(
    *,
    url: str = DEFAULT_URL,
    today: Optional[date] = None,
    timeout: float = 30.0,
) -> list[BonusOffer]:
    import httpx

    resp = httpx.get(
        url,
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "Mileage/1.0 (+bonus-calendar)"},
    )
    resp.raise_for_status()
    return parse_json_ld_offers(resp.text, today=today)


def offers_to_yaml_doc(
    offers: list[BonusOffer],
    *,
    source_url: str = DEFAULT_URL,
    fetched_at: Optional[str] = None,
) -> dict:
    fetched_at = fetched_at or datetime.now(timezone.utc).isoformat()
    return {
        "source": "Roame transfer-bonus calendar (JSON-LD)",
        "url": source_url,
        "fetched_at": fetched_at,
        "trust": 0.85,
        "offers": [
            {
                "from_currency": o.from_currency,
                "to_program": o.to_program,
                "bonus_multiplier": o.bonus_multiplier,
                "valid_from": o.valid_from,
                "valid_until": o.valid_until,
                "label": o.label,
                "status": o.status,
                "raw_name": o.raw_name,
            }
            for o in offers
        ],
    }


def save_bonus_calendar(path: Path, offers: list[BonusOffer], *, url: str = DEFAULT_URL) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = offers_to_yaml_doc(offers, source_url=url)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


def load_bonus_calendar(path: Path) -> list[BonusOffer]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: list[BonusOffer] = []
    for row in data.get("offers") or []:
        if not isinstance(row, dict):
            continue
        out.append(
            BonusOffer(
                from_currency=str(row["from_currency"]),
                to_program=str(row["to_program"]),
                bonus_multiplier=float(row.get("bonus_multiplier", 1.0)),
                valid_from=row.get("valid_from"),
                valid_until=row.get("valid_until"),
                label=row.get("label"),
                source_url=str(data.get("url") or DEFAULT_URL),
                status=str(row.get("status") or "active"),
                raw_name=str(row.get("raw_name") or ""),
            )
        )
    return out


def active_bonus_ratios(
    offers: list[BonusOffer],
    *,
    currency: str,
    today: Optional[date] = None,
    base_ratios: Optional[dict[str, float]] = None,
) -> list[TransferRatio]:
    """Emit TransferRatio bonus rows for active offers matching `currency`."""
    today = today or date.today()
    base_ratios = base_ratios or {}
    out: list[TransferRatio] = []
    for o in offers:
        if o.from_currency != currency:
            continue
        if o.status == "expired":
            continue
        start = _parse_iso_date(o.valid_from)
        end = _parse_iso_date(o.valid_until)
        if start and today < start:
            continue
        if end and today > end:
            continue
        base = float(base_ratios.get(o.to_program, 1.0))
        out.append(
            TransferRatio(
                from_currency=o.from_currency,
                to_program=o.to_program,
                ratio=base,
                provenance=Provenance(
                    source_name="bonus calendar (Roame)",
                    source_url=o.source_url,
                    trust=0.85,
                ),
                confidence=0.85,
                flags=["transfer_bonus", "live_bonus_calendar"],
                bonus_multiplier=o.bonus_multiplier,
                valid_from=o.valid_from,
                valid_until=o.valid_until,
                bonus_label=o.label,
            )
        )
    return out


def recent_expired_partners(
    offers: list[BonusOffer],
    *,
    currency: str,
    today: Optional[date] = None,
    within_days: int = 180,
) -> list[BonusOffer]:
    """Partners that recently had a bonus — for wait_for_bonus signals."""
    today = today or date.today()
    out: list[BonusOffer] = []
    for o in offers:
        if o.from_currency != currency:
            continue
        end = _parse_iso_date(o.valid_until)
        if end is None:
            continue
        age = (today - end).days
        if 0 < age <= within_days:
            out.append(o)
    return out
