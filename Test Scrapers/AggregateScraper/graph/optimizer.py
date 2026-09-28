import math
from datetime import datetime, timezone

import networkx as nx

from graph.carriers import airport_region, build_path_label, get_carrier_codes
from graph.partners import (
    c1_miles_required,
    filter_relevant_partners,
    get_airline_partners,
    partner_by_id,
)
from graph.zones import airport_to_zone, zone_slug
from verify.bounds import bounds_check

WIN_MARGIN = 0.20


def compute_cpp(
    effective_ratio: float,
    partner_miles: int,
    ticket_cash_usd: float,
    fees_usd: float = 0.0,
    surcharge_usd: float = 0.0,
) -> float:
    net_value = ticket_cash_usd - fees_usd - surcharge_usd
    if net_value <= 0 or partner_miles <= 0:
        return 0.0
    c1_miles_needed = c1_miles_required(partner_miles, effective_ratio)
    return (net_value * 100) / c1_miles_needed


def _data_age_days(source_updated_at: str | None, scraped_at: datetime | None = None) -> int | None:
    if source_updated_at:
        try:
            dt = datetime.fromisoformat(source_updated_at.replace("Z", "+00:00"))
            return (datetime.now(timezone.utc) - dt).days
        except ValueError:
            pass
    if scraped_at:
        return (datetime.now(timezone.utc) - scraped_at).days
    return None


def rank_paths(
    graph: nx.DiGraph,
    origin_airport: str,
    dest_airport: str,
    cabin: str,
    ticket_cash_usd: float,
    fees_usd: float,
    portal_cpp: float,
    zone_mapping: dict,
    live_award_miles: dict[str, int] | None = None,
    *,
    show_all: bool = False,
    nonstop_only: bool = False,
) -> list[dict]:
    results: list[dict] = []
    partners = get_airline_partners()
    partner_map = partner_by_id(partners)
    relevant = filter_relevant_partners(
        partners, origin_airport, dest_airport,
        show_all=show_all, nonstop_only=nonstop_only,
    )
    relevant_ids = {p["id"] for p in relevant}

    c1_for_portal = round((ticket_cash_usd * 100) / portal_cpp)
    portal_result = {
        "path": "C1 → portal → cash booking",
        "method": "portal",
        "c1_miles": c1_for_portal,
        "partner_miles": None,
        "cpp": portal_cpp,
        "confidence": "high",
        "source": "capitalone.com",
        "flags": [],
        "stale": False,
        "sources": 1,
        "data_age_days": 0,
        "carrier": "—",
        "non_1to1": False,
    }
    portal_result = bounds_check(portal_result, cabin)
    results.append(portal_result)

    hidden_connecting = 0

    for partner_info in relevant:
        partner = partner_info["id"]
        if not graph.has_edge("capital_one", partner):
            continue

        origin_zone = airport_to_zone(origin_airport, partner, zone_mapping)
        dest_zone = airport_to_zone(dest_airport, partner, zone_mapping)
        if origin_zone is None or dest_zone is None:
            continue

        node = f"{partner}_{cabin}_{zone_slug(origin_zone)}_{zone_slug(dest_zone)}"
        if not graph.has_edge(partner, node):
            continue

        te = graph["capital_one"][partner]
        ae = graph[partner][node]

        if ae["confidence"] == "unverified":
            continue

        effective_ratio = te.get("effective_ratio", te["ratio"])
        award_miles = ae["miles"]
        miles_low = ae.get("miles_range_low")
        miles_high = ae.get("miles_range_high")

        if live_award_miles and partner in live_award_miles:
            award_miles = live_award_miles[partner]

        surcharge = partner_info.get("surcharge_estimate_usd", 0) or 0
        cpp = compute_cpp(
            effective_ratio, award_miles, ticket_cash_usd, fees_usd, surcharge
        )
        c1_needed = c1_miles_required(award_miles, effective_ratio)
        non_1to1 = abs(effective_ratio - 1.0) > 0.001

        program_name = partner_info.get("name", partner)
        path_label = build_path_label(
            partner,
            program_name,
            origin_airport,
            dest_airport,
            routing_type=partner_info.get("routing_type"),
            connection_hub=partner_info.get("connection_hub"),
        )

        origin_region = airport_region(origin_airport)
        dest_region = airport_region(dest_airport)
        carrier = get_carrier_codes(partner, origin_region, dest_region)

        flags: list[str] = []
        ae_flags = [f for f in ae["flags"].split(",") if f]
        stale = "stale" in ae_flags
        source_count = ae.get("source_count", 1)
        data_age = _data_age_days(ae.get("source_updated_at"))

        if ae["confidence"] == "low":
            flags.append("⚠ low-confidence chart — verify before booking")
        if stale:
            flags.append("⚠ stale data — possible devaluation since last scrape")
        if any("sources_disagree" in f for f in ae_flags):
            flags.append("⚠ sources disagreed on mile cost — using consensus")
        if any("hardcoded_fallback" in f for f in ae_flags):
            flags.append("⚠ hardcoded fallback")
        if live_award_miles and partner in live_award_miles:
            flags.append("✓ live mileage from seats.aero")
        if surcharge > 0:
            flags.append(f"† CPP adjusted for ~${surcharge:.0f} carrier surcharges")
        if partner_info.get("routing_type") == "connecting":
            hub = partner_info.get("connection_hub", "?")
            flags.append(f"⤳ Connecting itinerary — transit at {hub}")

        cpp_display = cpp
        if miles_low and miles_high and miles_low != miles_high:
            cpp_lo = compute_cpp(effective_ratio, miles_high, ticket_cash_usd, fees_usd, surcharge)
            cpp_hi = compute_cpp(effective_ratio, miles_low, ticket_cash_usd, fees_usd, surcharge)
            cpp_display_str = f"~{cpp_lo:.2f}–{cpp_hi:.2f}¢"
        else:
            cpp_display_str = f"{cpp:.2f}¢"

        result = {
            "path": path_label,
            "method": f"transfer:{partner}",
            "c1_miles": c1_needed,
            "partner_miles": award_miles,
            "cpp": cpp,
            "cpp_display": cpp_display_str,
            "confidence": ae["confidence"],
            "source": ae["source"],
            "flags": flags,
            "stale": stale,
            "sources": source_count,
            "data_age_days": data_age,
            "carrier": carrier,
            "non_1to1": non_1to1,
            "miles_range_low": miles_low,
            "miles_range_high": miles_high,
        }
        result = bounds_check(result, cabin)
        results.append(result)

    for p in partners:
        if p.get("routing_type") == "connecting" and nonstop_only:
            if p["id"] not in relevant_ids and p.get("relevant_for_transpacific"):
                hidden_connecting += 1

    results.sort(key=lambda r: r["cpp"], reverse=True)

    if hidden_connecting and nonstop_only:
        results.append({
            "path": f"({hidden_connecting} connecting itineraries hidden — --nonstop-only)",
            "method": "hidden",
            "c1_miles": 0,
            "partner_miles": None,
            "cpp": 0,
            "cpp_display": "—",
            "confidence": "unverified",
            "source": "",
            "flags": [],
            "stale": False,
            "sources": 0,
            "data_age_days": None,
            "carrier": "—",
            "non_1to1": False,
        })

    return results


def conclude_winner(results: list[dict], portal_cpp: float) -> dict:
    """Apply conclusion logic — never declare winner without verified data on both sides."""
    portal = next((r for r in results if r["method"] == "portal"), None)
    transfers = [r for r in results if r["method"] not in ("portal", "hidden")]

    if not portal:
        return {"verdict": "insufficient_data", "message": "Portal path unavailable."}

    verified_transfers = [
        r for r in transfers
        if r["confidence"] in ("high", "medium") and not r.get("stale")
    ]

    if not verified_transfers:
        return {
            "verdict": "portal_only",
            "message": (
                f"Transfer path data unavailable for this route — "
                f"portal at {portal_cpp:.2f}¢ is your confirmed floor."
            ),
            "portal_cpp": portal_cpp,
        }

    best = verified_transfers[0]
    margin = (best["cpp"] - portal_cpp) / portal_cpp if portal_cpp > 0 else 0

    if margin < WIN_MARGIN:
        return {
            "verdict": "comparable",
            "message": (
                f"Best transfer ({best['path']}) at {best['cpp']:.2f}¢ is within "
                f"{WIN_MARGIN:.0%} of portal ({portal_cpp:.2f}¢) — weigh availability, "
                f"transfer time, and fees before deciding."
            ),
            "best_transfer": best,
            "portal_cpp": portal_cpp,
        }

    has_flags = bool(best.get("flags"))
    verdict = "tentative_best" if has_flags else "best"
    prefix = "Tentatively best, pending verification" if has_flags else "Best"

    return {
        "verdict": verdict,
        "message": (
            f"{prefix}: {best['path']} at {best['cpp']:.2f}¢ "
            f"vs portal {portal_cpp:.2f}¢ (+{margin:.0%})."
        ),
        "best_transfer": best,
        "portal_cpp": portal_cpp,
        "margin": margin,
    }
