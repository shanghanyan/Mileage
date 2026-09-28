#!/usr/bin/env python3
"""LifeMiles scraping lab — run multiple extraction strategies and rank them.

The production ``lifemiles_scraper`` keeps falling back to stale cache because it
vision-scrapes an Akamai-protected JS booking page that contains no award chart.
This lab pits five strategies against the same goal — get the LifeMiles award
chart (origin → destination → miles) — and produces a scored leaderboard plus a
machine-readable winner you can drop into the scraper / rates cache.

Usage:
    python run_lab.py                     # run every strategy, print leaderboard
    python run_lab.py --only aggregator_html,vision
    python run_lab.py --list              # list strategies and exit
    python run_lab.py --headful           # show the browser (debugging)
    python run_lab.py --vision-models llava:latest
    python run_lab.py --vision-chart-url https://awardtravelfinder.com/award-charts/lifemiles

Browser strategies need a real Chromium; inside restrictive sandboxes Chromium
can SIGABRT on launch (environmental). Run it in a normal shell if so.
"""

from __future__ import annotations

import sys
import json
import asyncio
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rich.table import Table  # noqa: E402
from rich.panel import Panel   # noqa: E402

from common import (  # noqa: E402
    console,
    get_logger,
    RUN_ID,
    RUN_LOG_PATH,
    OUTPUT_DIR,
    utcnow_iso,
    StrategyResult,
)
from strategies import ALL_STRATEGIES  # noqa: E402

log = get_logger("runner")


# Best-effort mapping of aggregator region labels → the mini-scraper's zone names,
# so a winning result can be promoted into the existing rows schema.
_ZONE_MAP = {
    "us domestic": ("US", "US"),
    "north america": ("US", None),
    "central america": (None, "Mexico"),
    "colombia": (None, "S.America"),
    "south america": (None, "S.America"),
    "europe": (None, "Europe"),
    "asia": (None, "Asia"),
    "north asia": (None, "Asia"),
    "southeast asia": (None, "Asia"),
    "australia": (None, "Asia"),
    "middle east": (None, "Asia"),
    "africa": (None, "Asia"),
    "canada": (None, "Canada"),
    "mexico": (None, "Mexico"),
}


def _zone(label: str, default: str) -> str:
    t = label.strip().lower()
    for key, (origin, dest) in _ZONE_MAP.items():
        if key in t:
            return dest or origin or default
    return default


def parse_args(argv) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="LifeMiles scraping strategy lab")
    ap.add_argument("--only", default="", help="comma-separated strategy names to run")
    ap.add_argument("--list", action="store_true", help="list strategies and exit")
    ap.add_argument("--headful", action="store_true", help="show the browser window")
    ap.add_argument("--nav-timeout", type=int, default=45000, help="nav timeout (ms)")
    ap.add_argument("--http-timeout", type=int, default=25, help="httpx timeout (s)")
    ap.add_argument("--ollama-host", default="http://localhost:11434")
    ap.add_argument("--vision-models", default="", help="comma-separated Ollama models")
    ap.add_argument(
        "--vision-chart-url", default="",
        help="screenshot this URL for vision instead of the LifeMiles page",
    )
    ap.add_argument(
        "--no-vision-schema", action="store_true",
        help="use format='json' instead of a strict JSON schema",
    )
    ap.add_argument(
        "--vision-timeout", type=int, default=180,
        help="per-model Ollama call timeout in seconds (default 180)",
    )
    return ap.parse_args(argv)


def build_context(args) -> dict:
    ctx = {
        "headless": not args.headful,
        "nav_timeout_ms": args.nav_timeout,
        "http_timeout": args.http_timeout,
        "ollama_host": args.ollama_host,
        "vision_chart_url": args.vision_chart_url or None,
        "vision_schema": not args.no_vision_schema,
        "vision_timeout_s": args.vision_timeout,
    }
    if args.vision_models:
        ctx["vision_models"] = [m.strip() for m in args.vision_models.split(",") if m.strip()]
    return ctx


def select_strategies(only: str):
    if not only:
        return ALL_STRATEGIES
    wanted = {s.strip() for s in only.split(",") if s.strip()}
    chosen = [s for s in ALL_STRATEGIES if s.name in wanted]
    unknown = wanted - {s.name for s in ALL_STRATEGIES}
    if unknown:
        log.warning(f"unknown strategies ignored: {sorted(unknown)}")
    return chosen


async def run_all(strategies, ctx) -> list[StrategyResult]:
    results: list[StrategyResult] = []
    for cls in strategies:
        results.append(await cls(ctx).execute())
    return results


def render_leaderboard(results: list[StrategyResult]) -> None:
    ranked = sorted(results, key=lambda r: r.score, reverse=True)
    table = Table(title="LifeMiles Strategy Leaderboard", show_lines=True)
    table.add_column("#", justify="right", style="bold")
    table.add_column("Strategy")
    table.add_column("Score", justify="right")
    table.add_column("Priced rows", justify="right")
    table.add_column("Conf", justify="right")
    table.add_column("Time", justify="right")
    table.add_column("Cost")
    table.add_column("Outcome")
    for i, r in enumerate(ranked, 1):
        priced = sum(1 for row in r.rows if row.has_any_price())
        outcome = "OK" if r.ok and priced else (r.error or "no data")
        table.add_row(
            str(i), r.name, f"{r.score:.1f}", str(priced), f"{r.confidence:.2f}",
            f"{r.duration_ms/1000:.1f}s", r.cost, outcome[:60],
        )
    console.print(table)


def promote_winner(results: list[StrategyResult]) -> dict | None:
    """Write the top scoring strategy's rows in the mini-scraper rows schema."""
    ranked = sorted(results, key=lambda r: r.score, reverse=True)
    winner = next((r for r in ranked if r.score > 0), None)
    if not winner:
        return None
    rows = []
    for row in winner.rows:
        rows.append({
            "origin_zone": _zone(row.origin, "US"),
            "destination_zone": _zone(row.destination, "US"),
            "economy_miles": row.economy_miles,
            "business_miles": row.business_miles,
            "first_miles": row.first_miles,
            "_origin_label": row.origin,
            "_destination_label": row.destination,
            "_one_way": row.one_way,
        })
    promoted = {
        "winner": winner.name,
        "source": winner.label,
        "scraped_at": utcnow_iso(),
        "rows": rows,
    }
    out = OUTPUT_DIR / f"{RUN_ID}_lifemiles_rows.json"
    out.write_text(json.dumps(promoted, indent=2))
    # Also write a stable, un-timestamped copy for easy consumption.
    (OUTPUT_DIR / "lifemiles_rows.latest.json").write_text(json.dumps(promoted, indent=2))
    return promoted


def write_report(results: list[StrategyResult], ctx: dict, promoted: dict | None) -> None:
    ranked = sorted(results, key=lambda r: r.score, reverse=True)
    report = {
        "run_id": RUN_ID,
        "generated_at": utcnow_iso(),
        "context": {k: v for k, v in ctx.items()},
        "winner": ranked[0].name if ranked and ranked[0].score > 0 else None,
        "results": [r.to_dict() for r in ranked],
        "promoted_rows": promoted,
    }
    (OUTPUT_DIR / f"{RUN_ID}_report.json").write_text(json.dumps(report, indent=2))

    md = [f"# LifeMiles Lab Report — {RUN_ID}\n", f"_Generated {report['generated_at']}_\n"]
    md.append(f"\n**Winner:** `{report['winner']}`\n")
    md.append("\n| # | Strategy | Score | Priced rows | Conf | Time | Outcome |")
    md.append("|---|----------|------:|------------:|-----:|-----:|---------|")
    for i, r in enumerate(ranked, 1):
        priced = sum(1 for row in r.rows if row.has_any_price())
        outcome = "OK" if r.ok and priced else (r.error or "no data")
        md.append(
            f"| {i} | {r.name} | {r.score:.1f} | {priced} | {r.confidence:.2f} | "
            f"{r.duration_ms/1000:.1f}s | {outcome} |"
        )
    md.append("\n## Per-strategy notes\n")
    for r in ranked:
        md.append(f"\n### {r.name} — {r.label}\n")
        for n in r.notes:
            md.append(f"- {n}")
        if r.error:
            md.append(f"- **error:** {r.error}")
        if r.artifacts:
            md.append(f"- artifacts: {', '.join(Path(a).name for a in r.artifacts)}")
    (OUTPUT_DIR / f"{RUN_ID}_report.md").write_text("\n".join(md))


def main(argv=None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if args.list:
        console.print(Panel.fit("Available strategies", style="bold"))
        for s in ALL_STRATEGIES:
            console.print(f"  [bold]{s.name}[/bold] ({s.cost}) — {s.label}")
        return 0

    ctx = build_context(args)
    strategies = select_strategies(args.only)
    if not strategies:
        log.error("no strategies selected")
        return 1

    log.info(f"run_id={RUN_ID} strategies={[s.name for s in strategies]}")
    log.info(f"context={ctx}")
    results = asyncio.run(run_all(strategies, ctx))

    console.print()
    render_leaderboard(results)
    promoted = promote_winner(results)
    write_report(results, ctx, promoted)

    if promoted:
        console.print()
        console.print(Panel.fit(
            f"Winner: [bold green]{promoted['winner']}[/bold green] — "
            f"{len(promoted['rows'])} routes promoted to "
            f"output/lifemiles_rows.latest.json",
            title="Result",
        ))
    console.print(f"\nReport: output/{RUN_ID}_report.md   Log: {RUN_LOG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
