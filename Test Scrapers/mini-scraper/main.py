import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()

from scrapers.capital_one_scraper import CapitalOneScraper
from scrapers.lifemiles_scraper import LifeMilesScraper
from scrapers.turkish_scraper import TurkishScraper
from scrapers.krisflyer_scraper import KrisFlyerScraper
from scrapers.aeroplan_scraper import AeroplanScraper
from scrapers.tpg_scraper import TPGScraper, sanity_check_tpg
from scrapers.nerdwallet_scraper import NerdWalletScraper
from scrapers.awardwallet_scraper import AwardWalletScraper
from scrapers.awardfares_scraper import AwardFaresScraper
from scrapers.base_scraper import get_logger

from optimizer.graph import build_graph, get_static_edges
from optimizer.models import UserPortfolio, OptimizationResult, ScraperStatus, Currency
from optimizer.optimizer import PointOptimizer
from storage.database import Database

FAKE_PORTFOLIO = UserPortfolio(
    c1_miles=85_000,
    c1_cashback_usd=200.0,
    lifemiles=0,
    turkish_miles=15_000,
    krisflyer_miles=0,
    aeroplan_miles=0,
)

MAX_RUNTIME_SECONDS = int(os.getenv("SCRAPE_MAX_MINUTES", "60")) * 60

SCRAPER_ORDER = [
    CapitalOneScraper,
    AwardWalletScraper,
    LifeMilesScraper,
    TurkishScraper,
    KrisFlyerScraper,
    AeroplanScraper,
    TPGScraper,
    NerdWalletScraper,
    AwardFaresScraper,
]

CPP_BASELINES = {
    "statement credit": 0.50,
    "gift cards": 0.80,
    "travel portal": 1.00,
}

logger = get_logger("main")


async def main() -> None:
    console = Console()
    Path("logs").mkdir(exist_ok=True)
    Path("storage").mkdir(exist_ok=True)
    Path("config").mkdir(exist_ok=True)

    db = Database(os.getenv("SQLITE_PATH", "./storage/optimizer.db"))
    await db.init()

    run_start = time.monotonic()
    console.rule("[bold blue]Capital One Point Optimizer — Scrape Run Starting")

    scrapers = [cls() for cls in SCRAPER_ORDER]
    results = []
    all_edges = []
    live_data = {}
    tpg_data: dict = {}
    awardfares_data: dict = {}

    for scraper in scrapers:
        elapsed = time.monotonic() - run_start
        if elapsed > MAX_RUNTIME_SECONDS:
            console.print(
                f"[yellow]⏱  Max runtime {MAX_RUNTIME_SECONDS}s reached — "
                f"stopping scrape, proceeding with data collected so far."
            )
            break

        result = await scraper.scrape()
        results.append(result)

        if scraper.name == "tpg_scraper" and result.data:
            tpg_data = result.data
        if scraper.name == "awardfares_scraper" and result.data:
            awardfares_data = result.data

        if result.status.value in ("SUCCESS", "CACHED") and result.data:
            edges = scraper.to_edges(result.data)
            all_edges.extend(edges)
            if result.status.value == "SUCCESS":
                live_data[scraper.name] = result.data

            if scraper.name == "awardwallet_scraper":
                for article in result.data.get("articles", []):
                    if article.get("is_alert"):
                        await db.flag_stale(article.get("programs", []))
                        await db.save_devaluation_alert(
                            ", ".join(article.get("programs", [])),
                            article["headline"],
                            article["url"],
                        )

    all_edges.extend(get_static_edges())
    await db.upsert_edges(all_edges)
    _update_rates_cache(live_data)

    db_edges = await db.load_edges()
    if not db_edges:
        db_edges = all_edges

    graph = build_graph(db_edges)
    optimizer = PointOptimizer(graph, FAKE_PORTFOLIO)
    ranked_paths = optimizer.find_all_paths()

    if not ranked_paths:
        console.print("[red]No redemption paths found — check scraper logs for failures.")
        return

    _validate_cpp_baselines(ranked_paths)
    _sanity_checks(ranked_paths, tpg_data, awardfares_data)

    best = ranked_paths[0]
    if not best.has_stale:
        best.recommended = True

    holding_valuations = optimizer.value_portfolio(ranked_paths)
    total_value = sum(hv.value_usd for hv in holding_valuations)

    opt_result = OptimizationResult(
        portfolio=FAKE_PORTFOLIO,
        holding_valuations=holding_valuations,
        all_paths=ranked_paths,
        total_value_usd=round(total_value, 2),
    )

    optimizer.display_results(opt_result)
    json_path = optimizer.save_json(opt_result)

    counts = {s.value: 0 for s in ScraperStatus}
    for r in results:
        counts[r.status.value] = counts.get(r.status.value, 0) + 1

    duration_ms = int((time.monotonic() - run_start) * 1000)
    await db.save_run(results, db_edges, opt_result, duration_ms=duration_ms)

    console.print(
        f"\n[green]✓ Done in {duration_ms/1000:.1f}s — "
        f"SUCCESS={counts.get('SUCCESS', 0)} CACHED={counts.get('CACHED', 0)} "
        f"FAILED={counts.get('FAILED', 0)}"
    )
    console.print(f"[dim]Results → {json_path}")
    console.print(f"[dim]Database → {os.getenv('SQLITE_PATH', './storage/optimizer.db')}")


def _update_rates_cache(live_data: dict) -> None:
    cache_path = os.getenv("RATES_CACHE_PATH", "./config/rates_cache.json")
    try:
        with open(cache_path) as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {}
    now = datetime.now(timezone.utc).isoformat()
    for name, data in live_data.items():
        existing[name] = {**data, "scraped_at": now}
    with open(cache_path, "w") as f:
        json.dump(existing, f, indent=2)


def _validate_cpp_baselines(paths: list) -> None:
    for path in paths[:20]:
        # Cashback face-value paths legitimately compound to ~100¢ (a dollar, not a
        # cent), so they fall far outside cent-scale baselines — skip them.
        if path.source_start and "cashback" in str(path.source_start).lower():
            continue
        label_lower = path.path_label.lower()
        for key, baseline in CPP_BASELINES.items():
            if key in label_lower:
                if baseline > 0:
                    deviation = abs(path.total_cpp - baseline) / baseline
                    if deviation > 0.30:
                        logger.warning(
                            f"CPP {path.total_cpp:.3f}¢ deviates >30% from "
                            f"baseline {baseline:.2f}¢ for [{path.path_label}]"
                        )


def _sanity_checks(paths: list, tpg_data: dict, awardfares_data: dict) -> None:
    program_map = {
        Currency.LIFEMILES: "avianca_lifemiles",
        Currency.TURKISH_MILES: "turkish_miles_smiles",
        Currency.KRISFLYER: "singapore_krisflyer",
        Currency.AEROPLAN: "air_canada_aeroplan",
        Currency.C1_MILES: "capital_one_miles",
    }
    seen: set[str] = set()
    for path in paths:
        if path.source_start not in program_map:
            continue
        key = program_map[path.source_start]
        if key in seen:
            continue
        seen.add(key)
        sanity_check_tpg(path.total_cpp, key, tpg_data)

    avg = awardfares_data.get("awardfares_avg_miles")
    if avg:
        # AwardFares publishes a single aggregate "sweet spot" figure, not a
        # per-route chart. Comparing it against every LifeMiles route produced a
        # warning storm (one per long-haul path). Instead compare only against the
        # closest comparable chart mileage and emit at most one warning.
        chart_miles: list[int] = []
        for path in paths:
            if "lifemiles" in path.path_label.lower() and "economy" in path.path_label.lower():
                for hop in path.hops:
                    if hop.from_currency == Currency.LIFEMILES:
                        m = re_extract_miles(hop.label)
                        if m:
                            chart_miles.append(m)
                        break
        if chart_miles:
            closest = min(chart_miles, key=lambda m: abs(m - avg))
            if abs(closest - avg) / avg > 0.4:
                logger.warning(
                    f"AwardFares aggregate {avg:,} mi differs >40% from the "
                    f"nearest LifeMiles chart route ({closest:,} mi) — aggregate "
                    f"figure may not match your specific routes"
                )


def re_extract_miles(label: str) -> int | None:
    import re
    m = re.search(r"([\d,]+)\s*miles", label, re.I)
    return int(m.group(1).replace(",", "")) if m else None


if __name__ == "__main__":
    asyncio.run(main())
