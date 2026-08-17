"""Airport reference: IATA → region, position, great-circle distance, haul band.

One loader, one file (``knowledge/airports.yaml``). Before this existed the
region map and the coordinate table were two independent blocks inside
charts.yaml, and they drifted: an airport could carry a region but no
coordinates, in which case every distance-banded chart silently declined to
resolve for it and the route just produced no options — indistinguishable from
"no award exists". Keeping both facts in one row makes that failure impossible
to introduce.

charts.yaml is still read as a fallback so an older knowledge dir keeps working.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

import yaml

from .models import Cabin, Route

_KNOWLEDGE = Path(__file__).resolve().parent.parent / "knowledge"

# Great-circle haul bands in statute miles. Carrier surcharges and government
# taxes both scale with haul, so one banding serves both.
HAUL_BANDS: tuple[tuple[str, float, float], ...] = (
    ("short", 0.0, 1500.0),
    ("medium", 1500.0, 3500.0),
    ("long", 3500.0, 7000.0),
    ("ultra_long", 7000.0, 30000.0),
)

EARTH_RADIUS_MILES = 3958.7613


@dataclass(frozen=True)
class Airport:
    iata: str
    lat: float
    lon: float
    region: str


@dataclass(frozen=True)
class AirportTable:
    by_iata: dict[str, Airport]

    def get(self, iata: str) -> Optional[Airport]:
        return self.by_iata.get(iata.upper())

    def region_of(self, iata: str) -> Optional[str]:
        ap = self.get(iata)
        return ap.region if ap else None

    def coords_of(self, iata: str) -> Optional[tuple[float, float]]:
        ap = self.get(iata)
        return (ap.lat, ap.lon) if ap else None

    def region_map(self) -> dict[str, str]:
        """Legacy shape consumed by chart band matching."""
        return {k: v.region for k, v in self.by_iata.items()}

    def coord_map(self) -> dict[str, tuple[float, float]]:
        """Legacy shape consumed by the distance-band resolver."""
        return {k: (v.lat, v.lon) for k, v in self.by_iata.items()}

    def __len__(self) -> int:
        return len(self.by_iata)


def great_circle_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in statute miles between two [lat, lon] points."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_MILES * math.asin(min(1.0, math.sqrt(h)))


def haul_band(miles: float) -> str:
    for name, lo, hi in HAUL_BANDS:
        if lo <= miles < hi:
            return name
    return HAUL_BANDS[-1][0]


def _parse_airports(data: dict) -> dict[str, Airport]:
    out: dict[str, Airport] = {}
    for iata, row in (data.get("airports") or {}).items():
        code = str(iata).upper()
        try:
            lat, lon, region = float(row[0]), float(row[1]), str(row[2])
        except (TypeError, ValueError, IndexError):
            continue
        out[code] = Airport(iata=code, lat=lat, lon=lon, region=region)
    return out


def _parse_legacy_charts(data: dict) -> dict[str, Airport]:
    """charts.yaml's split region_map + airports blocks (pre-airports.yaml)."""
    regions = {str(k).upper(): str(v) for k, v in (data.get("region_map") or {}).items()}
    coords: dict[str, tuple[float, float]] = {}
    for k, v in (data.get("airports") or {}).items():
        try:
            coords[str(k).upper()] = (float(v[0]), float(v[1]))
        except (TypeError, ValueError, IndexError):
            continue
    out: dict[str, Airport] = {}
    for code, region in regions.items():
        lat, lon = coords.get(code, (0.0, 0.0))
        out[code] = Airport(iata=code, lat=lat, lon=lon, region=region)
    return out


def load_airports(knowledge_dir: Optional[Path] = None) -> AirportTable:
    """Load airports.yaml, falling back to charts.yaml's legacy blocks.

    airports.yaml wins on conflict — it is the maintained table.
    """
    root = Path(knowledge_dir) if knowledge_dir else _KNOWLEDGE
    merged: dict[str, Airport] = {}

    legacy = root / "charts.yaml"
    if legacy.exists():
        merged.update(
            _parse_legacy_charts(yaml.safe_load(legacy.read_text(encoding="utf-8")) or {})
        )

    primary = root / "airports.yaml"
    if primary.exists():
        merged.update(
            _parse_airports(yaml.safe_load(primary.read_text(encoding="utf-8")) or {})
        )

    return AirportTable(by_iata=merged)


@lru_cache(maxsize=8)
def _cached_table(root: str) -> AirportTable:
    return load_airports(Path(root))


def airports(knowledge_dir: Optional[Path] = None) -> AirportTable:
    """Process-cached airport table. The file is version-controlled and only
    changes on a deploy, so re-parsing it per query is pure waste — the daily
    sweep was re-reading and re-parsing it once per program per route."""
    return _cached_table(str(Path(knowledge_dir) if knowledge_dir else _KNOWLEDGE))


def route_distance_miles(
    route: Route, *, table: Optional[AirportTable] = None
) -> Optional[float]:
    table = table or airports()
    a = table.coords_of(route.origin)
    b = table.coords_of(route.dest)
    if not a or not b:
        return None
    return great_circle_miles(a, b)


def route_haul(route: Route, *, table: Optional[AirportTable] = None) -> Optional[str]:
    miles = route_distance_miles(route, table=table)
    return haul_band(miles) if miles is not None else None


def unknown_airports(
    codes: Iterable[str], *, table: Optional[AirportTable] = None
) -> list[str]:
    table = table or airports()
    return sorted({c.upper() for c in codes if table.get(c) is None})


__all__ = [
    "Airport",
    "AirportTable",
    "Cabin",
    "HAUL_BANDS",
    "airports",
    "great_circle_miles",
    "haul_band",
    "load_airports",
    "route_distance_miles",
    "route_haul",
    "unknown_airports",
]
