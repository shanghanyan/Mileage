"""aviationstack — L1 schedules (weak fallback only). §1, §5

Flight status/tracker API: Layer 1 only — no cash fares and no award space,
so it must never back cents-per-point. When ``AVIATIONSTACK_API_KEY`` is set we
probe ``/v1/flights`` for the route (confirms metal exists); responses are not
turned into FareQuote/AwardQuote. Reports DOWN without a key.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

from ..domain.models import Layer
from .base import ProviderHealth, Query, Quote

log = logging.getLogger("mileage.aviationstack")

_BASE = "https://api.aviationstack.com/v1"


class AviationstackProvider:
    name = "aviationstack"
    trust = 0.3

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = _BASE,
        timeout: float = 10.0,
    ) -> None:
        self.api_key = (api_key or os.getenv("AVIATIONSTACK_API_KEY") or "").strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def capabilities(self) -> set[Layer]:
        return {Layer.SCHEDULES}

    def health(self) -> ProviderHealth:
        if not self.api_key:
            return ProviderHealth.DOWN
        return ProviderHealth.HEALTHY

    def remaining_quota(self) -> Optional[int]:
        return None

    def fetch(self, q: Query) -> list[Quote]:
        if q.layer != Layer.SCHEDULES or self.health() == ProviderHealth.DOWN:
            return []
        try:
            self._probe_route(q.route.origin, q.route.dest)
        except Exception as exc:  # never crash the run
            log.warning("aviationstack fetch failed: %s", exc)
        # No ScheduleQuote type yet — schedules are informational only.
        return []

    def _probe_route(self, origin: str, dest: str) -> int:
        resp = httpx.get(
            f"{self.base_url}/flights",
            params={
                "access_key": self.api_key,
                "dep_iata": origin.upper(),
                "arr_iata": dest.upper(),
                "limit": 5,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or []
        log.debug(
            "aviationstack %s-%s: %d flight(s)",
            origin,
            dest,
            len(data),
        )
        return len(data)
