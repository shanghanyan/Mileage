#!/usr/bin/env python3
"""Capital One Miles Optimizer — route-specific CPP ranking from verified scraped data."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from scrapers.http_util import load_sources_config
from scrapers.rss_monitor import check_devaluation_feeds
from pipeline.orchestrator import run_optimizer, freshness_report, _load_cache
from display.rich_table import render_leaderboard, render_freshness_report, render_alerts


def setup_logging() -> None:
    level = __import__("os").getenv("LOG_LEVEL", "INFO")
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    )
    Path(ROOT / "logs").mkdir(exist_ok=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Rank C1 → United paths by cents-per-mile using verified scraped data."
    )
    ap.add_argument("--origin", default="JFK", help="Origin airport IATA code")
    ap.add_argument("--dest", default="NRT", help="Destination airport IATA code")
    ap.add_argument("--cabin", choices=["economy", "business", "first"], default="business")
    ap.add_argument("--cash", type=float, required=False, help="Cash ticket price USD")
    ap.add_argument("--fees", type=float, default=0.0, help="Award ticket fees/taxes USD")
    ap.add_argument("--offline", action="store_true", help="Use cached data only")
    ap.add_argument("--refresh-all", action="store_true", help="Force refresh all sources")
    ap.add_argument("--check-freshness", action="store_true", help="Report data age only")
    ap.add_argument("--check-alerts", action="store_true", help="Check devaluation RSS feeds")
    ap.add_argument("--show-all", action="store_true", help="Include route-irrelevant partners")
    ap.add_argument("--nonstop-only", action="store_true", help="Exclude connecting itineraries")
    ap.add_argument("--validate-urls", action="store_true", help="HEAD-check all scrape target URLs")
    return ap.parse_args(argv)


async def async_main(args: argparse.Namespace) -> int:
    config = load_sources_config()

    if args.check_freshness:
        entries = freshness_report(_load_cache())
        render_freshness_report(entries)
        return 0

    if args.validate_urls:
        from scrapers.validate_urls import run_health_check
        report = await run_health_check(fail_on_bad=False)
        return 1 if report["failed"] > 0 else 0

    if args.check_alerts and not args.cash:
        alerts = await check_devaluation_feeds(config)
        render_alerts(alerts)
        return 0

    if args.cash is None:
        print("ERROR: --cash is required for optimization runs.", file=sys.stderr)
        return 1

    result = await run_optimizer(
        origin=args.origin.upper(),
        dest=args.dest.upper(),
        cabin=args.cabin,
        cash=args.cash,
        fees=args.fees,
        config=config,
        offline=args.offline,
        refresh_all=args.refresh_all,
        check_alerts=args.check_alerts,
        show_all=args.show_all,
        nonstop_only=args.nonstop_only,
    )

    if args.check_alerts and result.get("alerts"):
        from scrapers.rss_monitor import DevaluationAlert
        alerts = [
            DevaluationAlert(a["feed"], a["title"], "", a["programs"])
            for a in result["alerts"]
        ]
        render_alerts(alerts)

    render_leaderboard(
        result["results"],
        result["origin"],
        result["dest"],
        result["cabin"],
        result["cash_usd"],
        result["fees_usd"],
        result["portal_cpp"],
        result["conclusion"],
        result.get("source_ages"),
        result.get("cache_warnings"),
    )
    return 0


def main() -> int:
    setup_logging()
    args = parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
