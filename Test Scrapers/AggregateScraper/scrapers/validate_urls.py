"""Monthly URL health check — HEAD every URL in scrape_targets.yaml."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml

from scrapers.chart_scraper import load_scrape_targets

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT = ROOT / "logs" / "url_health.json"

_HEADERS = {
    "User-Agent": "AggregateScraper-URLHealth/1.0 (+https://github.com/)",
}


async def check_url(client: httpx.AsyncClient, url: str) -> dict:
    result = {
        "url": url,
        "status": None,
        "ok": False,
        "error": None,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        resp = await client.head(url, follow_redirects=True)
        result["status"] = resp.status_code
        result["ok"] = resp.status_code == 200
        if resp.status_code == 405:
            resp = await client.get(url, follow_redirects=True)
            result["status"] = resp.status_code
            result["ok"] = resp.status_code == 200
    except httpx.HTTPStatusError as exc:
        result["status"] = exc.response.status_code
        result["error"] = str(exc)
    except Exception as exc:
        result["error"] = str(exc)
    return result


def collect_urls(targets: dict) -> list[tuple[str, str]]:
    """Return (program_id, url) pairs from scrape_targets."""
    pairs: list[tuple[str, str]] = []
    for program, entries in targets.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if url := entry.get("url"):
                pairs.append((program, url))
    return pairs


async def run_health_check(
    *,
    report_path: Path | None = None,
    fail_on_bad: bool = False,
) -> dict:
    targets = load_scrape_targets()
    pairs = collect_urls(targets)
    report_path = report_path or DEFAULT_REPORT

    results: list[dict] = []
    bad: list[dict] = []

    async with httpx.AsyncClient(headers=_HEADERS, timeout=20.0) as client:
        for program, url in pairs:
            check = await check_url(client, url)
            check["program"] = program
            check["host"] = urlparse(url).netloc
            results.append(check)
            if not check["ok"]:
                bad.append(check)
            await asyncio.sleep(0.3)

    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "ok": sum(1 for r in results if r["ok"]),
        "failed": len(bad),
        "results": results,
        "failures": bad,
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"URL health: {report['ok']}/{report['total']} OK · {report['failed']} failed")
    for f in bad:
        status = f.get("status") or f.get("error", "?")
        print(f"  [{f['program']}] {status} — {f['url']}")

    if fail_on_bad and bad:
        return report

    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HEAD-check all scrape target URLs")
    ap.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    ap.add_argument("--fail-on-bad", action="store_true", help="Exit 1 if any URL fails")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    report = asyncio.run(run_health_check(report_path=args.report, fail_on_bad=False))

    if args.fail_on_bad and report["failed"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
