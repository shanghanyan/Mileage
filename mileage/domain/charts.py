"""Partner award-chart logic (region resolution + band lookup).

Pure logic only: the chart *data* lives in knowledge/charts.yaml and is loaded
by providers/curated.py. This module resolves a `Route` against a parsed chart
spec for one program and returns the one-way miles, handling round-trip charts
(e.g. ANA) by normalizing to one-way and flagging it (§6 carried-over fixes).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .geo import great_circle_miles
from .models import Cabin, Route


@dataclass
class ChartHit:
    program: str
    miles: int
    flags: list[str] = field(default_factory=list)


def region_of(airport: str, region_map: dict[str, str]) -> Optional[str]:
    return region_map.get(airport.upper())


def route_region_tokens(
    program: str,
    route: Route,
    region_map: dict[str, str],
    program_zones: Optional[dict[str, dict[str, int]]] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Map a route's airports to chart band tokens.

    When ``program_zones`` lists a zone index for an airport, the token becomes
    ``{program}_zone_{n}`` so zone-matrix PDF rows (KrisFlyer zones 1–13) resolve
    without collapsing distinct zones that share a canonical region. Otherwise
    falls back to the global ``region_map`` (LifeMiles / Aeroplan region pairs).
    """
    zones_raw = (program_zones or {}).get(program) or {}
    zones = {str(ap).upper(): int(z) for ap, z in zones_raw.items()}

    def _token(airport: str) -> Optional[str]:
        ap = airport.upper()
        zone = zones.get(ap)
        if zone is not None:
            return f"{program}_zone_{zone}"
        return region_map.get(ap)

    return _token(route.origin), _token(route.dest)


def _bands_match(band_regions: list[str], a: str, b: str) -> bool:
    """A band matches a route if its unordered region pair equals {a, b}."""
    return sorted(x.lower() for x in band_regions) == sorted([a, b])


def _distance_only_bands(program_chart: dict) -> list[dict]:
    """Bands priced purely by segment distance, with no region pair (§4.3).

    This is the `distance_band` chart kind — BA/Avios and the rest of the Avios
    family price a segment by how long it is, full stop. Modeling that as a
    region matrix is what forced a single flat "North America ↔ North Asia"
    number and erased the short-haul sweet spot the whole product opens with.
    """
    return [
        b
        for b in program_chart.get("bands", [])
        if not b.get("regions") and not b.get("airports") and b.get("distance")
    ]


def _distance_band_hit(
    program: str,
    program_chart: dict,
    route: Route,
    cabin_key: str,
    airport_coords: Optional[dict[str, tuple[float, float]]],
) -> Optional[ChartHit]:
    bands = _distance_only_bands(program_chart)
    if not bands or not airport_coords:
        return None
    co = airport_coords.get(route.origin.upper())
    cd = airport_coords.get(route.dest.upper())
    if not (co and cd):
        return None
    gcm = great_circle_miles(co, cd)
    for band in bands:
        lo, hi = float(band["distance"][0]), float(band["distance"][1])
        if not (lo <= gcm <= hi):
            continue
        raw = band.get("miles", {}).get(cabin_key)
        if raw is None:
            continue
        miles = int(raw)
        flags = [f"distance_band:{int(lo)}-{int(hi)}mi"]
        if band.get("roundtrip", False):
            miles = math.ceil(miles / 2)
            flags.append("rt_to_ow_normalized")
        if program_chart.get("scope") == "per_segment":
            # §4.3: per-segment pricing. A one-stop itinerary is priced as two
            # segments, so the single-segment number is a FLOOR, not the fare.
            flags.append("per_segment_floor")
        return ChartHit(program=program, miles=miles, flags=flags)
    return None


def lookup_award_miles(
    program: str,
    program_chart: dict,
    route: Route,
    region_map: dict[str, str],
    *,
    airport_coords: Optional[dict[str, tuple[float, float]]] = None,
    program_zones: Optional[dict[str, dict[str, int]]] = None,
) -> Optional[ChartHit]:
    """Resolve `route` against one program's chart. None if unresolvable.

    `program_chart` shape (from charts.yaml / parsed rows):
        {
          "bands": [
            {"regions": ["north_america", "europe"],
             "roundtrip": false,
             "miles": {"economy": 30000, "business": 45000}},
            # distance-banded (Aeroplan): the band also carries a [lo, hi] mile
            # range; it matches only when the route's great-circle distance falls
            # inside it (§A.4). Needs `airport_coords`.
            {"regions": ["north_america", "europe"],
             "roundtrip": false,
             "distance": [4001, 6000],
             "miles": {"business": 70000}},
            ...
          ]
        }
    """
    cabin_key = route.cabin.value
    o = route.origin.upper()
    d = route.dest.upper()
    route_airports = sorted([o, d])

    if airport_coords is None:
        # Callers used to have to remember to pass this, and curated.py didn't —
        # so every distance-banded chart silently declined to resolve and the
        # route just produced no options. Default to the shared reference table.
        from .geo import airports as _airports

        airport_coords = _airports().coord_map()

    # Exact per-airport bands (hub-based "destination table" charts) take
    # precedence: they carry the queried city's OWN price, so we never collapse a
    # region to one (often wrong) number when we have the specific O->D pair.
    # These don't need region_map, so they resolve even for airports we can't
    # classify into a region.
    for band in program_chart.get("bands", []):
        airports = band.get("airports")
        if not airports:
            continue
        if sorted(str(a).upper() for a in airports) != route_airports:
            continue
        raw = band.get("miles", {}).get(cabin_key)
        if raw is None:
            continue  # this pair matched but not this cabin — keep scanning
        miles = int(raw)
        flags: list[str] = []
        if band.get("roundtrip", False):
            miles = math.ceil(miles / 2)
            flags.append("rt_to_ow_normalized")
        return ChartHit(program=program, miles=miles, flags=flags)

    # Pure distance charts (§4.3 `distance_band`) resolve on geometry alone and
    # need no region pair at all.
    hit = _distance_band_hit(program, program_chart, route, cabin_key, airport_coords)
    if hit is not None:
        return hit

    r_o, r_d = route_region_tokens(
        program, route, region_map, program_zones=program_zones
    )
    token_pairs: list[tuple[Optional[str], Optional[str]]] = [(r_o, r_d)]
    # Discovery prose emits canonical region pairs (north_america/europe); zone-
    # matrix charts emit turkish_zone_N. Retry with region_map when zone tokens
    # miss so both shapes coexist for the same program.
    if (program_zones or {}).get(program):
        ro2, rd2 = region_map.get(o), region_map.get(d)
        if ro2 and rd2 and (ro2, rd2) != (r_o, r_d):
            token_pairs.append((ro2, rd2))

    gcm: Optional[float] = None
    best: Optional[ChartHit] = None
    for r_o, r_d in token_pairs:
        if r_o is None or r_d is None:
            continue
        for band in program_chart.get("bands", []):
            if band.get("airports"):
                continue  # exact-airport bands were handled above
            regions = band.get("regions", [])
            if len(regions) != 2 or not _bands_match(regions, r_o, r_d):
                continue
            # Distance-banded charts: the geography matched, but the band only
            # applies to a great-circle range. Compute the route distance once and
            # skip bands whose [lo, hi] the route falls outside.
            dist = band.get("distance")
            if dist:
                if airport_coords is None:
                    continue
                co = airport_coords.get(route.origin.upper())
                cd = airport_coords.get(route.dest.upper())
                if not (co and cd):
                    continue
                if gcm is None:
                    gcm = great_circle_miles(co, cd)
                lo, hi = float(dist[0]), float(dist[1])
                if not (lo <= gcm <= hi):
                    continue
            miles_map = band.get("miles", {})
            raw = miles_map.get(cabin_key)
            if raw is None:
                continue
            flags: list[str] = []
            miles = int(raw)
            if band.get("roundtrip", False):
                miles = math.ceil(miles / 2)
                flags.append("rt_to_ow_normalized")
            hit = ChartHit(program=program, miles=miles, flags=flags)
            if dist:
                return hit  # distance bands are disjoint ranges — first match wins
            if best is None or hit.miles < best.miles:
                best = hit  # duplicate region pairs (e.g. Saver + Advantage PDF rows)
    return best


