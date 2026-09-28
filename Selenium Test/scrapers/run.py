"""CLI: run tier fallback for one or all site contracts."""

from __future__ import annotations

import argparse
import os
import shutil
import sys

from scrapers.contracts import SITE_CONTRACTS
from scrapers.http_fetch import tier1_http_fetch
from scrapers.test_results import live_scrape_record, start_run, write_result, write_summary
from scrapers.tier_fallback import attempt_to_dict, run_tier_fallback


def _ensure_xvfb(argv: list[str]) -> None:
    """Re-exec under xvfb-run when the VPS has no DISPLAY (headless box)."""
    if os.environ.get("DISPLAY") or os.environ.get("SCRAPE_NO_XVFB") == "1":
        return
    xvfb = shutil.which("xvfb-run")
    if xvfb is None:
        print(
            "No DISPLAY and xvfb-run not found. Install xvfb or run: sudo apt install -y xvfb",
            file=sys.stderr,
        )
        sys.exit(3)
    os.execvp(xvfb, [xvfb, "-a", sys.executable, "-m", "scrapers.run", *argv])


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description="One-time point conversion chart scrape")
    parser.add_argument(
        "--site",
        action="append",
        dest="sites",
        help="Contract key (repeatable). Default: all sites.",
    )
    parser.add_argument("--tier1-only", action="store_true", help="HTTP fetch only (no browser)")
    parser.add_argument("--no-tor", action="store_true", help="Skip experimental Tor tier")
    parser.add_argument("--no-login", action="store_true", help="Skip Tier 4 even if LOGIN_REQUIRED")
    parser.add_argument("--no-rate-limit", action="store_true", help="Disable 60s same-domain delay")
    args = parser.parse_args(argv)

    if not args.tier1_only:
        print("[scrape] wrapping in xvfb-run (virtual display)...", file=sys.stderr, flush=True)
        _ensure_xvfb(argv)

    keys = args.sites or list(SITE_CONTRACTS)
    unknown = [k for k in keys if k not in SITE_CONTRACTS]
    if unknown:
        print(f"Unknown site keys: {unknown}. Known: {sorted(SITE_CONTRACTS)}", file=sys.stderr)
        return 2

    run_dir = start_run("scrape")
    write_result(
        run_dir,
        "run",
        {
            "kind": "scrape",
            "status": "running",
            "sites": keys,
            "results_dir": str(run_dir),
        },
    )
    print(f"[scrape] writing live results to {run_dir}", file=sys.stderr, flush=True)
    print(f"[scrape] sites: {', '.join(keys)}", file=sys.stderr, flush=True)
    summaries = []
    for key in keys:
        print(f"[scrape] {key}: starting", file=sys.stderr, flush=True)

        def _record_attempt(attempt, site_key=key) -> None:
            write_result(
                run_dir,
                f"{site_key}__tier{attempt.tier}_{attempt.name}",
                attempt_to_dict(attempt) | {"site": site_key, "contract_key": site_key},
            )
            print(
                f"[scrape] {site_key}: tier {attempt.tier} {attempt.name} "
                f"→ {attempt.validation.outcome} "
                f"(deliverable={attempt.validation.deliverable_met}, "
                f"confidence={attempt.validation.chart_confidence:.2f})",
                file=sys.stderr,
                flush=True,
            )

        if args.tier1_only:
            contract = SITE_CONTRACTS[key]
            t1 = tier1_http_fetch(contract, rate_limit=not args.no_rate_limit)
            payload = live_scrape_record(
                site=key,
                browser="httpx",
                validation=t1.validation,
                artifact=t1.artifact,
                extra={
                    "contract_key": key,
                    "winning_tier": 1 if t1.validation.deliverable_met else None,
                    "attempts": [
                        {
                            "tier": 1,
                            "name": "httpx",
                            "outcome": t1.validation.outcome,
                            "deliverable_met": t1.validation.deliverable_met,
                            "chart_confidence": t1.validation.chart_confidence,
                            "messages": t1.validation.messages,
                        }
                    ],
                },
            )
            write_result(run_dir, f"{key}__tier1_httpx", payload)
        else:
            result = run_tier_fallback(
                key,
                include_browser=True,
                include_tor=not args.no_tor,
                include_login=not args.no_login,
                rate_limit=not args.no_rate_limit,
                on_attempt=_record_attempt,
            )
            payload = result.to_dict()
        summaries.append(payload)
        write_result(run_dir, key, payload)
        print(
            f"[scrape] {key}: finished deliverable_met={payload.get('deliverable_met')} "
            f"winning_tier={payload.get('winning_tier')}",
            file=sys.stderr,
            flush=True,
        )

    won = sum(1 for s in summaries if s.get("deliverable_met"))
    summary_path = write_summary(
        run_dir,
        {
            "kind": "scrape",
            "sites": keys,
            "deliverable_met_count": won,
            "site_count": len(summaries),
            "results": summaries,
        },
    )
    print(f"Saved {len(summaries)} site result(s) to {run_dir}", file=sys.stderr)
    print(f"Summary: {summary_path}", file=sys.stderr)
    print(f"Deliverable met for {won}/{len(summaries)} site(s).", file=sys.stderr)
    return 0 if won else 1


if __name__ == "__main__":
    sys.exit(main())
