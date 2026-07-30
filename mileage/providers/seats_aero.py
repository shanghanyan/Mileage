"""seats.aero Partner API — L3 live award availability (OPTIONAL, paid). §1, §5

Off without SEATS_AERO_API_KEY. When keyed, cached search returns real award
space across programs. Independent of the aggregator scrape → real cross-check.
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import Optional

import httpx

from ..domain.models import AwardQuote, Cabin, Layer, Provenance
from .base import ProviderHealth, Query, Quote

log = logging.getLogger("mileage.providers.seats_aero")

_API_BASE = "https://seats.aero/partnerapi"

_CABIN_MAP = {
    Cabin.ECONOMY: "economy",
    Cabin.PREMIUM_ECONOMY: "premium",
    Cabin.BUSINESS: "business",
    Cabin.FIRST: "first",
}

# seats.aero source ids → our program ids
_SOURCE_TO_PROGRAM = {
    "aeroplan": "aeroplan",
    "united": "united",
    "turkish": "turkish",
    "lifemiles": "lifemiles",
    "ana": "ana",
    "singapore": "krisflyer",
    "krisflyer": "krisflyer",
    "eva": "eva",
    "flyingblue": "flying_blue",
    "virginatlantic": "virgin_atlantic",
    "britishairways": "avios",
    "avios": "avios",
    "american": "american",
    "delta": "delta",
    "alaska": "alaska",
    "qatar": "qatar",
    "emirates": "emirates",
    "etihad": "etihad",
    "cathay": "cathay_pacific",
    "qantas": "qantas",
    "jetblue": "jetblue",
}


class SeatsAeroProvider:
    name = "seats_aero"
    trust = 0.85

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = api_key or os.getenv("SEATS_AERO_API_KEY")

    def capabilities(self) -> set[Layer]:
        return {Layer.AWARD}

    def health(self) -> ProviderHealth:
        return ProviderHealth.HEALTHY if self.api_key else ProviderHealth.DOWN

    def remaining_quota(self) -> Optional[int]:
        return None

    def fetch(self, q: Query) -> list[Quote]:
        if not self.api_key or q.layer != Layer.AWARD:
            return []
        start = q.start_date or (date.today() + timedelta(days=14)).isoformat()
        end = q.end_date or (date.today() + timedelta(days=44)).isoformat()
        cabin = _CABIN_MAP.get(q.route.cabin, "economy")
        params: dict = {
            "origin_airport": q.route.origin,
            "destination_airport": q.route.dest,
            "start_date": start,
            "end_date": end,
            "cabins": cabin,
            "order_by": "lowest_mileage",
            "take": 100,
        }
        if q.nonstop_only:
            params["only_direct_flights"] = "true"
        if q.programs:
            # Map our ids back to seats.aero source names where known.
            rev = {v: k for k, v in _SOURCE_TO_PROGRAM.items()}
            sources = [rev[p] for p in q.programs if p in rev]
            if sources:
                params["sources"] = ",".join(sources)

        try:
            resp = httpx.get(
                f"{_API_BASE}/search",
                params=params,
                headers={"Partner-Authorization": self.api_key},
                timeout=30.0,
            )
            if resp.status_code >= 400:
                log.warning("seats.aero HTTP %s: %s", resp.status_code, resp.text[:200])
                return []
            data = resp.json()
        except Exception as exc:
            log.warning("seats.aero fetch failed: %s", exc)
            return []

        rows = data.get("data") or data.get("results") or data
        if not isinstance(rows, list):
            return []

        out: list[Quote] = []
        seen: set[tuple] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            source = str(row.get("Source") or row.get("source") or "").lower()
            program = _SOURCE_TO_PROGRAM.get(source, source.replace(" ", "_"))
            if q.programs and program not in q.programs:
                continue
            miles = _miles_for_cabin(row, cabin)
            if miles is None or miles <= 0:
                continue
            seats = _seats_for_cabin(row, cabin)
            taxes = _taxes_for_cabin(row, cabin)
            key = (program, miles, seats)
            if key in seen:
                continue
            seen.add(key)
            flags = ["live_award_space"]
            if q.nonstop_only or row.get("Direct") or row.get(f"{cabin[0].upper()}Direct"):
                flags.append("nonstop")
            out.append(
                AwardQuote(
                    program=program,
                    route=q.route,
                    miles=int(miles),
                    seats_available=seats,
                    taxes_cents=taxes,
                    provenance=Provenance(
                        source_name="seats.aero",
                        source_url="https://seats.aero/",
                        trust=self.trust,
                    ),
                    confidence=self.trust,
                    flags=flags,
                )
            )
        return out


def _miles_for_cabin(row: dict, cabin: str) -> Optional[int]:
    # Common seats.aero cached fields: YMiles / WMiles / JMiles / FMiles
    letter = {"economy": "Y", "premium": "W", "business": "J", "first": "F"}.get(
        cabin, "Y"
    )
    for key in (f"{letter}Miles", f"{cabin}_miles", "Miles", "miles"):
        val = row.get(key)
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                continue
    return None


def _seats_for_cabin(row: dict, cabin: str) -> Optional[int]:
    letter = {"economy": "Y", "premium": "W", "business": "J", "first": "F"}.get(
        cabin, "Y"
    )
    for key in (f"{letter}RemainingSeats", f"{letter}Seats", "RemainingSeats", "seats"):
        val = row.get(key)
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                continue
    return 1  # availability row implies ≥1 seat when miles present


def _taxes_for_cabin(row: dict, cabin: str) -> Optional[int]:
    letter = {"economy": "Y", "premium": "W", "business": "J", "first": "F"}.get(
        cabin, "Y"
    )
    # Taxes often in dollars; convert to cents when clearly dollar-scale.
    for key in (f"{letter}Taxes", f"{cabin}_taxes", "Taxes", "taxes"):
        val = row.get(key)
        if val is None:
            continue
        try:
            num = float(val)
        except (TypeError, ValueError):
            continue
        if num <= 0:
            return 0
        # Heuristic: values < 500 are dollars; larger already cents.
        return int(round(num * 100)) if num < 500 else int(round(num))
    return None
