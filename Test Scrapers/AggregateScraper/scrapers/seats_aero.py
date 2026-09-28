"""seats.aero API wrapper — live award availability and actual mileage."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import httpx

from scrapers.base import ScrapedRow

log = logging.getLogger(__name__)

PROGRAM_MAP = {
    "lifemiles": "lifemiles",
    "aeroplan": "aeroplan",
    "turkish": "turkish_miles",
    "ana": "ana_mileage",
    "singapore": "krisflyer",
}


async def query_route(
    origin: str,
    dest: str,
    cabin: str,
    config: dict,
) -> list[ScrapedRow]:
    """Query seats.aero for live award pricing on a specific O&D."""
    api_key = os.getenv("SEATS_AERO_API_KEY", "").strip()
    if not api_key:
        log.info("SEATS_AERO_API_KEY not set — skipping live query")
        return []

    live_cfg = config.get("live_awards", {}).get("seats_aero", {})
    base_url = live_cfg.get("base_url", "https://seats.aero/api")
    now = datetime.now(timezone.utc)
    rows: list[ScrapedRow] = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        for api_program, from_program in PROGRAM_MAP.items():
            try:
                resp = await client.get(
                    f"{base_url}/availability",
                    params={
                        "origin": origin.upper(),
                        "destination": dest.upper(),
                        "program": api_program,
                        "cabin": cabin,
                    },
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                if resp.status_code != 200:
                    log.warning("seats.aero %s: HTTP %s", api_program, resp.status_code)
                    continue
                data = resp.json()
                for item in data.get("results", data if isinstance(data, list) else []):
                    miles = item.get("miles") or item.get("points")
                    if miles is None:
                        continue
                    rows.append(
                        ScrapedRow(
                            source_name="seats.aero",
                            source_url=f"{base_url}/availability",
                            scraped_at=now,
                            from_program=from_program,
                            to_program="united",
                            origin_zone=origin.upper(),
                            destination_zone=dest.upper(),
                            economy_miles=int(miles) if cabin == "economy" else None,
                            business_miles=int(miles) if cabin == "business" else None,
                            first_miles=int(miles) if cabin == "first" else None,
                            raw_cell_text=str(miles),
                            selector_matched=True,
                            confidence="high",
                        )
                    )
            except Exception as exc:
                log.warning("seats.aero query for %s failed: %s", api_program, exc)

    return rows
