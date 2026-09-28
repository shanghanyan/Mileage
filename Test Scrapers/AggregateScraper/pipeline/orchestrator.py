"""Pipeline orchestrator: scrape → verify → graph → optimize → display."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scrapers.base import ScrapedRow
from scrapers.c1_transfer_partners import scrape_transfer_partners
from scrapers.chart_scraper import scrape_all_programs
from scrapers.seats_aero import query_route
from scrapers.rss_monitor import check_devaluation_feeds, DevaluationAlert
from scrapers.scraper_logger import reset_run_stats, run_summary
from verify.cross_check import cross_check, group_transfer_rows, group_award_rows
from verify.freshness import apply_freshness, age_days
from verify.trust import attach_trust
from graph.edges import TransferEdge, AwardEdge, PortalEdge
from graph.builder import build_graph
from graph.zones import load_zone_mapping
from graph.optimizer import rank_paths, conclude_winner
from graph.partners import get_airline_partners, partner_by_id, filter_relevant_partners
from scrapers.ana_pricing import get_ow_miles
from pipeline.fallback import fallback_rows_for_route
from db.store import Database

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = ROOT / "config" / "rates_cache.json"


def max_cache_age_days() -> int:
    return int(os.getenv("MAX_CACHE_AGE_DAYS", "7"))


def _load_cache() -> dict:
    try:
        with open(CACHE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)


def _rows_from_cache_section(section: dict | list) -> list[ScrapedRow]:
    if isinstance(section, list):
        return [ScrapedRow.from_dict(r) for r in section]
    rows = section.get("rows", [])
    return [ScrapedRow.from_dict(r) for r in rows]


def _cache_is_fresh(scraped_at_str: str | None, max_age: timedelta) -> bool:
    if not scraped_at_str:
        return False
    try:
        scraped_at = datetime.fromisoformat(scraped_at_str.replace("Z", "+00:00"))
    except ValueError:
        return False
    return datetime.now(timezone.utc) - scraped_at < max_age


def _normalize_ana_rows(rows: list[ScrapedRow]) -> list[ScrapedRow]:
    """Apply RT→OW correction to cached ANA chart values."""
    for row in rows:
        if row.from_program != "ana_mileage":
            continue
        for field in ("economy_miles", "business_miles", "first_miles"):
            val = getattr(row, field)
            if val and val >= 60000:
                ow, basis = get_ow_miles(val, row.raw_cell_text, rt_ow_context="round_trip")
                setattr(row, field, ow)
                if basis == "RT":
                    row.flags.append("rt_to_ow_normalized")
    return rows


def _load_award_rows_for_program(
    prog_key: str,
    prog_cache: dict,
    *,
    offline: bool,
    max_age_days: int,
    origin: str,
    dest: str,
    cabin: str,
) -> list[ScrapedRow]:
    stale = _cache_exceeds_max_age(prog_cache, max_age_days)
    if offline and stale:
        log.info("Cache for %s exceeds %d days — using fallback in offline mode", prog_key, max_age_days)
        return fallback_rows_for_route(origin, dest, cabin, programs=[prog_key])

    rows = _rows_from_cache_section(prog_cache)
    if prog_key == "ana_mileage":
        rows = _normalize_ana_rows(rows)
    if stale and not offline:
        return rows  # will be replaced by fresh scrape
    return rows


def _cache_exceeds_max_age(cache_section: dict, max_days: int) -> bool:
    at = cache_section.get("scraped_at")
    if not at:
        return True
    try:
        scraped_at = datetime.fromisoformat(at.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - scraped_at).days
        return age > max_days
    except ValueError:
        return True


def _promote_transfer_rows(raw_rows: list[ScrapedRow]) -> list[TransferEdge]:
    edges: list[TransferEdge] = []
    for (_from, _to), group in group_transfer_rows(raw_rows).items():
        row, flags = cross_check(group, field="transfer_ratio")
        if row is None:
            log.info("Transfer %s→%s not promoted: %s", _from, _to, flags)
            continue
        row = apply_freshness(row)
        attach_trust(row)
        edges.append(
            TransferEdge(
                from_currency=row.from_program,
                to_currency=row.to_program,
                ratio=row.transfer_ratio or 1.0,
                confidence=row.confidence,
                source=row.source_name,
                scraped_at=row.scraped_at,
                flags=row.flags + flags,
            )
        )
    return edges


def _promote_award_rows(
    raw_rows: list[ScrapedRow],
    cabin: str = "business",
) -> list[AwardEdge]:
    edges: list[AwardEdge] = []
    field = f"{cabin}_miles"
    for key, group in group_award_rows(raw_rows).items():
        for r in group:
            attach_trust(r)
        row, flags = cross_check(group, field=field)
        if row is None:
            log.debug("Award %s not promoted: %s", key, flags)
            continue
        row = apply_freshness(row)
        edges.append(
            AwardEdge(
                from_currency=row.from_program,
                origin_zone=row.origin_zone or "",
                destination_zone=row.destination_zone or "",
                economy_miles=row.economy_miles,
                business_miles=row.business_miles,
                first_miles=row.first_miles,
                source=row.source_name,
                confidence=row.confidence,
                scraped_at=row.scraped_at,
                flags=row.flags + flags,
                source_count=row.source_count,
                source_trust=row.source_trust,
                miles_range_low=row.miles_range_low,
                miles_range_high=row.miles_range_high,
            )
        )
    return edges


async def scrape_all(
    config: dict,
    *,
    offline: bool = False,
    refresh_all: bool = False,
    origin: str = "JFK",
    dest: str = "NRT",
    cabin: str = "business",
) -> tuple[list[TransferEdge], list[AwardEdge], dict, list[str]]:
    reset_run_stats()
    cache = _load_cache()
    schedule = config.get("refresh_schedule", {})
    transfer_max_age = timedelta(days=schedule.get("transfer_partners_days", 7))
    chart_max_age = timedelta(days=schedule.get("award_charts_days", 7))
    max_age_days = max_cache_age_days()
    warnings: list[str] = []

    transfer_rows: list[ScrapedRow] = []
    award_rows_by_program: dict[str, list[ScrapedRow]] = {}

    tp_cache = cache.get("transfer_partners", {})
    need_transfer = refresh_all or not _cache_is_fresh(tp_cache.get("scraped_at"), transfer_max_age)
    if offline and _cache_exceeds_max_age(tp_cache, max_age_days):
        warnings.append(
            f"⚠ Transfer partner cache is older than {max_age_days} days (--offline mode)"
        )
    elif not offline and _cache_exceeds_max_age(tp_cache, max_age_days):
        need_transfer = True

    if not offline and need_transfer:
        log.info("Scraping transfer partners...")
        transfer_rows = await scrape_transfer_partners(config)
        if transfer_rows:
            cache["transfer_partners"] = {
                "scraped_at": datetime.now(timezone.utc).isoformat(),
                "rows": [r.to_dict() for r in transfer_rows],
            }
    else:
        transfer_rows = _rows_from_cache_section(tp_cache)
        log.info("Using cached transfer partners (%d rows)", len(transfer_rows))

    # Supplement missing partners from partners.yaml
    partners_cfg = partner_by_id(get_airline_partners())
    existing = {r.to_program for r in transfer_rows}
    for pid, pinfo in partners_cfg.items():
        if pid not in existing:
            transfer_rows.append(
                ScrapedRow(
                    source_name="partners.yaml",
                    source_url="config/partners.yaml",
                    scraped_at=datetime.now(timezone.utc),
                    from_program="capital_one",
                    to_program=pid,
                    transfer_ratio=pinfo["effective_ratio"],
                    raw_cell_text=pinfo.get("c1_ratio", "1:1"),
                    selector_matched=True,
                    confidence="medium",
                    flags=["seeded_from_config"],
                )
            )

    partners = get_airline_partners()
    relevant_ids = {
        p["id"] for p in filter_relevant_partners(partners, origin, dest)
    }

    charts_cache = cache.get("award_charts", {})
    need_charts = refresh_all
    if not need_charts:
        for pid in relevant_ids:
            prog_cache = charts_cache.get(pid, {})
            if not _cache_is_fresh(prog_cache.get("scraped_at"), chart_max_age):
                need_charts = True
                break
            if not offline and _cache_exceeds_max_age(prog_cache, max_age_days):
                need_charts = True
                break

    if not offline and need_charts:
        log.info("Scraping award charts...")
        scraped = await scrape_all_programs(config, programs=list(relevant_ids))
        for program, rows in scraped.items():
            award_rows_by_program[program] = rows
            if rows:
                charts_cache[program] = {
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                    "rows": [r.to_dict() for r in rows],
                }
        cache["award_charts"] = charts_cache
    else:
        for prog_key, prog_cache in charts_cache.items():
            if prog_key in relevant_ids:
                award_rows_by_program[prog_key] = _load_award_rows_for_program(
                    prog_key, prog_cache,
                    offline=offline, max_age_days=max_age_days,
                    origin=origin, dest=dest, cabin=cabin,
                )
        log.info("Using cached award charts")

    # Inject fallback rates for programs with no scraped data
    programs_missing = relevant_ids - {
        p for p, rows in award_rows_by_program.items() if rows
    }
    if programs_missing:
        fallback = fallback_rows_for_route(
            origin, dest, cabin, programs=list(programs_missing)
        )
        for row in fallback:
            award_rows_by_program.setdefault(row.from_program, []).append(row)

    # Merge fallback with stale cached data when online cache is stale but scrape skipped
    if not offline:
        for pid in list(relevant_ids):
            if _cache_exceeds_max_age(charts_cache.get(pid, {}), max_age_days):
                fb = fallback_rows_for_route(origin, dest, cabin, programs=[pid])
                if fb:
                    existing = award_rows_by_program.get(pid, [])
                    award_rows_by_program[pid] = existing + fb

    _save_cache(cache)

    all_award_rows: list[ScrapedRow] = []
    for rows in award_rows_by_program.values():
        all_award_rows.extend(rows)

    transfer_edges = _promote_transfer_rows(transfer_rows)
    award_edges = _promote_award_rows(all_award_rows, cabin=cabin)

    source_ages = _collect_source_ages(cache, all_award_rows)
    summary = run_summary()
    if summary:
        log.info(summary)
        print(summary)

    return transfer_edges, award_edges, source_ages, warnings


def _collect_source_ages(cache: dict, award_rows: list[ScrapedRow]) -> dict[str, int]:
    ages: dict[str, int] = {}
    tp_at = cache.get("transfer_partners", {}).get("scraped_at")
    if tp_at:
        try:
            dt = datetime.fromisoformat(tp_at.replace("Z", "+00:00"))
            ages["capitalone.com"] = (datetime.now(timezone.utc) - dt).days
        except ValueError:
            pass
    for row in award_rows:
        if row.source_updated_at:
            days = (datetime.now(timezone.utc) - row.source_updated_at).days
            key = f"{row.source_name} ({row.from_program})"
            ages[key] = days
    return ages


def _dedupe_freshness_entries(entries: list[dict]) -> list[dict]:
    """One row per (program, source, scraped_at) — not one per parsed chart row."""
    seen: dict[tuple, dict] = {}
    for entry in entries:
        if entry["name"].startswith("Award chart:") or entry["name"] == "Transfer partners":
            key = (entry["name"], entry.get("source"), entry.get("scraped_at"))
        else:
            key = (entry["name"], entry.get("source"), entry.get("scraped_at"))
        if key not in seen:
            seen[key] = entry
        elif entry.get("used"):
            seen[key]["used"] = True
    return list(seen.values())


def freshness_report(cache: dict | None = None) -> list[dict]:
    cache = cache or _load_cache()
    schedule = config_freshness_thresholds()
    entries: list[dict] = []

    tp = cache.get("transfer_partners", {})
    at = tp.get("scraped_at")
    age = _age_days(at)
    entries.append({
        "name": "Transfer partners",
        "source": "aggregate",
        "scraped_at": at or "never",
        "age_days": age,
        "trust": 1.0 if age is not None and age <= 30 else 0.65,
        "status": _status_for_age(age, schedule["transfer_days"]),
        "used": True,
    })

    for row_dict in tp.get("rows", []):
        if not isinstance(row_dict, dict):
            continue
        row = ScrapedRow.from_dict(row_dict)
        age = age_days(row)
        entries.append({
            "name": f"Transfer: {row.to_program}",
            "source": row.source_name,
            "scraped_at": row.scraped_at.isoformat() if row.scraped_at else "—",
            "age_days": age,
            "trust": row.source_trust,
            "status": _status_for_age(age, schedule["transfer_days"]),
            "used": row.source_name in ("capitalone.com", "partners.yaml"),
        })

    for prog, data in cache.get("award_charts", {}).items():
        at = data.get("scraped_at")
        age = _age_days(at)
        entries.append({
            "name": f"Award chart: {prog}",
            "source": "aggregate",
            "scraped_at": at or "never",
            "age_days": age,
            "trust": 1.0 if age is not None and age <= 30 else 0.65,
            "status": _status_for_age(age, schedule["chart_days"]),
            "used": True,
        })
        seen_sources: set[str] = set()
        for row_dict in data.get("rows", []):
            row = ScrapedRow.from_dict(row_dict)
            source_key = f"{row.source_name}@{row.source_url}"
            if source_key in seen_sources:
                continue
            seen_sources.add(source_key)
            src_age = None
            if row.source_updated_at:
                src_age = (datetime.now(timezone.utc) - row.source_updated_at).days
            elif row.scraped_at:
                src_age = age_days(row)
            entries.append({
                "name": prog,
                "source": row.source_name[:60],
                "scraped_at": (
                    row.source_updated_at.isoformat() if row.source_updated_at
                    else row.scraped_at.isoformat()
                ),
                "age_days": src_age,
                "trust": row.source_trust,
                "status": _status_for_age(src_age, schedule["chart_days"]),
                "used": False,
            })

    # Mark winning sources (highest trust per program)
    by_prog: dict[str, list] = {}
    for e in entries:
        if e["name"] not in ("Transfer partners",) and not e["name"].startswith("Award chart:"):
            if e["name"] not in by_prog:
                by_prog[e["name"]] = []
            by_prog[e["name"]].append(e)
    for prog, prog_entries in by_prog.items():
        if prog_entries:
            best = max(prog_entries, key=lambda x: x.get("trust", 0))
            best["used"] = True

    return _dedupe_freshness_entries(entries)


def config_freshness_thresholds() -> dict:
    return {"transfer_days": 7, "chart_days": 7, "stale_days": 90}


def _age_days(scraped_at: str | None) -> int | None:
    if not scraped_at:
        return None
    try:
        dt = datetime.fromisoformat(scraped_at.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days
    except ValueError:
        return None


def _status_for_age(age: int | None, warn_days: int) -> str:
    if age is None:
        return "missing"
    if age > 90:
        return "stale"
    if age > warn_days:
        return "aging"
    return "fresh"


async def run_optimizer(
    *,
    origin: str,
    dest: str,
    cabin: str,
    cash: float,
    fees: float,
    config: dict,
    offline: bool = False,
    refresh_all: bool = False,
    check_alerts: bool = False,
    show_all: bool = False,
    nonstop_only: bool = False,
) -> dict:
    db = Database(os.getenv("SQLITE_PATH", str(ROOT / "storage" / "aggregator.db")))
    db.init()

    alerts: list[DevaluationAlert] = []
    if check_alerts:
        alerts = await check_devaluation_feeds(config)
        for alert in alerts:
            db.save_alert(alert.feed_name, alert.title, alert.url, alert.programs)
            db.flag_stale_programs(alert.programs)

    transfer_edges, award_edges, source_ages, cache_warnings = await scrape_all(
        config,
        offline=offline,
        refresh_all=refresh_all,
        origin=origin,
        dest=dest,
        cabin=cabin,
    )

    portal_cpp = float(os.getenv("PORTAL_CPP", "1.25"))
    portal_edge = PortalEdge(cpp=portal_cpp)
    graph = build_graph(transfer_edges, award_edges, portal_edge)
    zone_mapping = load_zone_mapping()

    live_miles: dict[str, int] = {}
    live_dates: list[str] = []
    if not offline:
        live_rows = await query_route(origin, dest, cabin, config)
        for row in live_rows:
            if row.is_usable():
                cabin_field = f"{cabin}_miles"
                miles = getattr(row, cabin_field, None)
                if miles:
                    live_miles[row.from_program] = miles
        if live_miles:
            source_ages["seats.aero"] = 0

    results = rank_paths(
        graph,
        origin,
        dest,
        cabin,
        cash,
        fees,
        portal_cpp,
        zone_mapping,
        live_award_miles=live_miles or None,
        show_all=show_all,
        nonstop_only=nonstop_only,
    )

    if live_miles and results:
        for r in results:
            if r["method"] != "portal" and any("seats.aero" in f for f in r.get("flags", [])):
                r["flags"].append("✓ avail")

    conclusion = conclude_winner(results, portal_cpp)

    output = {
        "origin": origin,
        "dest": dest,
        "cabin": cabin,
        "cash_usd": cash,
        "fees_usd": fees,
        "portal_cpp": portal_cpp,
        "results": results,
        "conclusion": conclusion,
        "source_ages": source_ages,
        "cache_warnings": cache_warnings,
        "alerts": [
            {"feed": a.feed_name, "title": a.title, "programs": a.programs}
            for a in alerts
        ],
    }

    db.save_run("optimize", origin, dest, cabin, cash, fees, output)
    return output
