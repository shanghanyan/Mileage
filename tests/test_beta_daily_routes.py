"""Beta daily route sweep — wipe Redis, quote fixed routes, append JSONL log.

Intended for the phone beta month-long demo: run once per day with a cold
cache, record verdict + top-3 paths, compare logs over time.

Run (live network; optional Redis from repo `.env`):
    pytest tests/test_beta_daily_routes.py -m beta_daily -s

Or directly (reads ``.env`` for ``MILEAGE_REDIS_URL``, ``AMADEUS_*``, etc.):
    python tests/test_beta_daily_routes.py

Optional env:
    BETA_DAILY_LIMIT=3     — only first N routes (smoke)
    BETA_DAILY_SKIP_BONUS=1 — skip ``refresh-bonuses`` network call
    BETA_DAILY_OFFLINE=1   — force offline fixtures (fast, hermetic)

Redis wipe status is recorded every run, not just printed: each day's
``.jsonl`` gets a leading ``{"type": "run_meta", ...}`` line and each day's
``.md`` gets a "Redis wipe" banner, so a stale/unwiped cache is visible in
the log itself (not just console output that scrolls away).
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_LOG_DIR = _REPO / "logs" / "beta_daily"

# --------------------------------------------------------------------------- #
# Route matrix — 100k pts, rotate wallet currency + card product
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class BetaRouteCase:
    origin: str
    dest: str
    cabin: str
    currency: str
    card: str
    note: str = ""


BETA_ROUTE_CASES: tuple[BetaRouteCase, ...] = (
    BetaRouteCase("LAX", "JFK", "economy", "capital_one", "venture_x", "Demo A floor"),
    BetaRouteCase("JFK", "LAX", "economy", "amex_mr", "venture_x", "NYC return"),
    BetaRouteCase("EWR", "LAX", "economy", "chase_ur", "sapphire_reserve", "NYC alt"),
    BetaRouteCase("SFO", "JFK", "economy", "citi_typ", "venture_x", "Transcon"),
    BetaRouteCase("LAX", "IST", "business", "capital_one", "venture_x", "Demo B value"),
    BetaRouteCase("IST", "LAX", "business", "bilt", "venture_x", "Turkey return"),
    BetaRouteCase("SFO", "NRT", "business", "capital_one", "venture_x", "Cap1→EVA +30%"),
    BetaRouteCase("NRT", "SFO", "business", "amex_mr", "venture_x", "Tokyo return"),
    BetaRouteCase("LAX", "NRT", "business", "citi_typ", "venture_x", "Tokyo business"),
    BetaRouteCase("LAX", "LHR", "business", "capital_one", "venture_x", "London"),
    BetaRouteCase("SFO", "LHR", "business", "chase_ur", "sapphire_reserve", "London west"),
    BetaRouteCase("LAX", "CDG", "business", "citi_typ", "venture_x", "Paris / Flying Blue"),
    BetaRouteCase("LAX", "FCO", "business", "bilt", "venture_x", "Rome"),
    BetaRouteCase("SJC", "LAX", "economy", "capital_one", "venture", "CA short"),
    BetaRouteCase("SFO", "LAX", "economy", "amex_mr", "venture_x", "CA hop"),
    BetaRouteCase("SAN", "SFO", "economy", "bilt", "venture_x", "CA hop"),
    BetaRouteCase("SEA", "LAX", "economy", "chase_ur", "sapphire_reserve", "West coast"),
    BetaRouteCase("LAX", "SEA", "economy", "capital_one", "venture_x", "West return"),
    BetaRouteCase("SFO", "IST", "business", "amex_mr", "venture_x", "Star long-haul"),
    BetaRouteCase("LAX", "SIN", "business", "capital_one", "venture_x", "KrisFlyer"),
    # --- The thesis pair (§11). Same bank, same currency, same alliance ----- #
    BetaRouteCase("HND", "ITM", "business", "chase_ur", "sapphire_reserve",
                  "THESIS: Avios on JAL — per-segment banding, minimal fuel"),
    BetaRouteCase("LHR", "JFK", "business", "chase_ur", "sapphire_reserve",
                  "THESIS: Avios on BA — same currency, fuel charges demote it"),
    # --- Wider international coverage --------------------------------------- #
    BetaRouteCase("JFK", "LHR", "business", "amex_mr", "amex_platinum", "NYC-London"),
    BetaRouteCase("JFK", "CDG", "business", "chase_ur", "sapphire_preferred", "NYC-Paris"),
    BetaRouteCase("SFO", "HKG", "business", "capital_one", "venture_x", "Cathay"),
    BetaRouteCase("LAX", "SYD", "business", "citi_typ", "venture_x", "Qantas"),
    BetaRouteCase("JFK", "DXB", "business", "bilt", "venture_x", "Emirates"),
    BetaRouteCase("JFK", "GRU", "business", "amex_mr", "amex_platinum", "Sao Paulo"),
    BetaRouteCase("JFK", "JNB", "business", "chase_ur", "sapphire_reserve", "Johannesburg"),
    BetaRouteCase("JFK", "DEL", "business", "capital_one", "venture_x", "Delhi"),
    BetaRouteCase("SFO", "ICN", "business", "citi_typ", "venture_x", "Seoul"),
    BetaRouteCase("LHR", "CDG", "economy", "chase_ur", "sapphire_reserve",
                  "Intra-Europe short-haul (per-segment banding)"),
    # A wallet that CANNOT transfer out — proves the gated list is real, not a
    # theoretical feature (§6.3).
    BetaRouteCase("LHR", "JFK", "business", "chase_ur", "freedom_unlimited",
                  "GATED: Freedom-only holder — routes shown, card named"),
)

# Not run yet — add when chart or availability coverage reliably reaches them.
FUTURE_ROUTE_IDEAS: tuple[str, ...] = (
    "First class: LHR↔JFK first, SFO↔HND first (needs first-cabin chart rows)",
    "Secondary Europe: ORY, BVA, MXP, LGW",
    "Intra-Asia depth: HND↔CTS, SIN↔BKK, HKG↔TPE",
    "Award-space coverage beyond the two pairs the live feed reaches",
)

BALANCE = 100_000


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _read_dotenv_value(key: str) -> Optional[str]:
    path = _REPO / ".env"
    if not path.exists():
        return os.environ.get(key)
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        if k.strip() == key:
            return v.strip().strip('"').strip("'") or None
    return os.environ.get(key)


def _apply_live_env() -> None:
    """Re-enable live keys scrubbed by tests/conftest.py for this sweep only."""
    if os.environ.get("BETA_DAILY_OFFLINE") == "1":
        os.environ["MILEAGE_OFFLINE"] = "1"
    else:
        os.environ["MILEAGE_OFFLINE"] = "0"
    for key in (
        "MILEAGE_REDIS_URL",
        "REDIS_URL",
        "AMADEUS_CLIENT_ID",
        "AMADEUS_CLIENT_SECRET",
        "SEATS_AERO_API_KEY",
    ):
        val = _read_dotenv_value(key)
        if val:
            os.environ[key] = val
    if not os.environ.get("MILEAGE_REDIS_URL") and _read_dotenv_value("REDIS_URL"):
        os.environ["MILEAGE_REDIS_URL"] = _read_dotenv_value("REDIS_URL") or ""


def wipe_redis_mileage_cache(redis_url: str) -> int:
    from mileage.store.redis_impl import redis_from_url

    client = redis_from_url(redis_url)
    deleted = 0
    for key in client.scan_iter(match="mileage:cache:*"):
        client.delete(key)
        deleted += 1
    return deleted


def _check_and_wipe_redis(cfg: Any, stores: Any, registry: Any) -> dict[str, Any]:
    """Attempt the redis wipe and return a status block that always says
    plainly whether the cache was actually cleared before this run.

    Status values:
        "wiped"                — wipe ran, no error (0 deleted still counts as wiped)
        "error"                — wipe was attempted but raised
        "not_applicable"       — cache backend isn't redis; nothing to wipe
        "skipped_misconfigured" — backend IS redis but no redis_url was found,
                                   so the wipe could not run — this is the
                                   "not wiped beforehand" case worth flagging
    """
    redis_url = cfg.redis_url or os.environ.get("MILEAGE_REDIS_URL")
    backend = stores.backend

    status: dict[str, Any] = {
        "redis_backend": backend,
        "redis_url_configured": bool(redis_url),
        "redis_keys_deleted": 0,
        "redis_wiped": False,
        "redis_wipe_status": None,
        "redis_wipe_note": None,
        "redis_wipe_error": None,
        "redis_wipe_warning": False,
    }

    if backend != "redis":
        status["redis_wipe_status"] = "not_applicable"
        status["redis_wipe_note"] = f"cache backend is '{backend}', not redis — nothing to wipe"
        return status

    if not redis_url:
        status["redis_wipe_status"] = "skipped_misconfigured"
        status["redis_wipe_note"] = (
            "cache backend is 'redis' but no MILEAGE_REDIS_URL/REDIS_URL was found — "
            "wipe SKIPPED, this run may be reading a stale cache"
        )
        status["redis_wipe_warning"] = True
        return status

    try:
        deleted = wipe_redis_mileage_cache(redis_url)
        registry.cache.clear()
        status["redis_keys_deleted"] = deleted
        status["redis_wiped"] = True
        status["redis_wipe_status"] = "wiped"
        status["redis_wipe_note"] = f"wiped {deleted} key(s) before this run"
    except Exception as exc:
        status["redis_wipe_status"] = "error"
        status["redis_wipe_error"] = str(exc)
        status["redis_wipe_note"] = f"wipe FAILED ({exc}) — cache was NOT confirmed clear"
        status["redis_wipe_warning"] = True

    return status


def _top3(payload: dict[str, Any]) -> list[dict[str, Any]]:
    opts = payload.get("options") or []
    out: list[dict[str, Any]] = []
    for o in opts[:3]:
        out.append(
            {
                "label": o.get("label"),
                "kind": o.get("kind"),
                # Points and cash are always present; cpp may legitimately be
                # null when no market fare existed. Logging cpp alone made a
                # degraded row indistinguishable from a broken one.
                "source_points": o.get("source_points"),
                "price_paid_usd": o.get("price_paid_usd"),
                "cpp": o.get("cpp"),
                "operating_carrier": o.get("operating_carrier"),
                "fuel_policy": o.get("fuel_policy"),
                "space": o.get("space"),
                "transfer_hops": o.get("transfer_hops"),
                "reason": o.get("reason"),
                "flags": [
                    f
                    for f in (o.get("flags") or [])
                    if "bonus" in f or "live" in f or "portal" in f
                    or f.startswith("collapsed_variants")
                ],
            }
        )
    return out


def _log_paths(stamp: datetime) -> tuple[Path, Path]:
    """Log files are named by the RUN's UTC timestamp, not by `date.today()`.

    The old naming used local `date.today()` while every row carried a UTC
    stamp, so the 8/2 file actually held a run from 8/3T04:13 and 8/3 held two
    runs. The filename was not a usable time axis, which makes twelve days of
    logs much harder to reason about than they should be. UTC everywhere, and
    the date in the name is the date in the rows.
    """
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    stem = stamp.date().isoformat()
    return _LOG_DIR / f"{stem}.jsonl", _LOG_DIR / f"{stem}.md"


def _append_run_banner(
    jsonl_path: Path,
    md_path: Path,
    *,
    stamp: datetime,
    redis_status: dict[str, Any],
    snapshot: str,
) -> None:
    """Stamp the start of THIS run into both logs.

    Carries the knowledge-snapshot hash (§8) so a reader can tell which chart
    version produced a block. Two runs on 7/29 disagreed about LAX-JFK — 17,300
    points versus 12,500 — with nothing in the file saying which chart version
    was authoritative, or that one was a 0.03s cache hit and the other a 15.8s
    fresh fetch. Both facts are recorded now.
    """
    banner = {
        "type": "run_meta",
        "ts": stamp.isoformat(),
        "snapshot": snapshot,
        **redis_status,
    }
    with jsonl_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(banner, ensure_ascii=False) + "\n")

    is_new_file = not md_path.exists()
    lines: list[str] = []
    if is_new_file:
        lines.append(f"# Beta daily sweep — {stamp.date().isoformat()} (UTC)\n")
        lines.append(f"Balance: **{BALANCE:,}** pts per route\n\n")
    if redis_status["redis_wipe_warning"]:
        lines.append(f"### ⚠️ Run @ {banner['ts']} — Redis NOT wiped: {redis_status['redis_wipe_note']}\n")
    elif redis_status["redis_wipe_status"] == "wiped":
        lines.append(f"### ✅ Run @ {banner['ts']} — Redis wiped: {redis_status['redis_wipe_note']}\n")
    else:
        lines.append(f"### Run @ {banner['ts']} — Redis: {redis_status['redis_wipe_note']}\n")
    lines.append(f"Knowledge snapshot: `{snapshot}`\n\n")
    with md_path.open("a", encoding="utf-8") as fh:
        fh.writelines(lines)


def run_beta_daily_sweep(*, limit: Optional[int] = None) -> dict[str, Any]:
    """Wipe Redis (if configured), quote each route, append log lines."""
    _apply_live_env()

    from mileage.cli import run_quote
    from mileage.config import Config, build_registry, build_repository, build_stores
    from mileage.domain.models import Cabin, Route, User
    from mileage.serialize import quote_result_to_dict

    limit = limit if limit is not None else int(os.environ.get("BETA_DAILY_LIMIT", "0") or 0)
    cases = list(BETA_ROUTE_CASES)
    if limit > 0:
        cases = cases[:limit]

    cfg = Config.from_env()
    repo = build_repository(cfg)
    stores = build_stores(cfg, repo)
    registry = build_registry(cfg, repo, stores=stores)

    meta: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "offline": cfg.offline,
        "cache_backend": stores.backend,
        "routes": len(cases),
        "bonus_refresh": None,
    }

    redis_status = _check_and_wipe_redis(cfg, stores, registry)
    meta.update(redis_status)
    if redis_status["redis_wipe_warning"]:
        print(f"⚠️  {redis_status['redis_wipe_note']}", file=sys.stderr, flush=True)

    if os.environ.get("BETA_DAILY_SKIP_BONUS") != "1" and not cfg.offline:
        try:
            from mileage.cli import run_refresh_bonuses

            meta["bonus_refresh"] = run_refresh_bonuses(cfg)
        except Exception as exc:
            meta["bonus_refresh_error"] = str(exc)

    from mileage.knowledge_snapshot import snapshot_version

    snapshot = snapshot_version(cfg.knowledge_dir)
    meta["snapshot"] = snapshot

    run_stamp = datetime.now(timezone.utc)
    jsonl_path, md_path = _log_paths(run_stamp)
    _append_run_banner(
        jsonl_path, md_path, stamp=run_stamp,
        redis_status=redis_status, snapshot=snapshot,
    )
    rows: list[dict[str, Any]] = []

    window_start = run_stamp.date()
    window_end = window_start + timedelta(days=90)

    for i, case in enumerate(cases, start=1):
        route = Route(case.origin, case.dest, Cabin(case.cabin))
        user = User(
            user_id="beta_daily",
            card=case.card,
            balances={case.currency: BALANCE},
        )
        # Per-route cache accounting, so a 0.03s cached block and a 15.8s fresh
        # block are never confused for two disagreeing measurements.
        registry.reset_stats()
        t0 = time.perf_counter()
        try:
            result = run_quote(
                route,
                user,
                case.currency,
                registry=registry,
                config=cfg,
                start_date=window_start.isoformat(),
                end_date=window_end.isoformat(),
            )
            payload = quote_result_to_dict(result)
        except Exception as exc:
            payload = {"error": "exception", "message": str(exc)}
        elapsed = round(time.perf_counter() - t0, 2)

        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "index": i,
            "route": route.key(),
            "currency": case.currency,
            "card": case.card,
            "balance": BALANCE,
            "note": case.note,
            "elapsed_s": elapsed,
            "verdict": payload.get("verdict"),
            "rationale": payload.get("rationale"),
            "reason": payload.get("reason"),
            "error": payload.get("error"),
            "message": payload.get("message"),
            "snapshot": snapshot,
            "cache_hits": registry.stats.cache_hits,
            "cache_misses": registry.stats.cache_misses,
            "fare_cents": payload.get("fare_cents"),
            "fare_flags": payload.get("fare_flags"),
            "degraded": payload.get("degraded"),
            "portal_cpp": payload.get("portal_cpp"),
            "top3": _top3(payload),
            # Three states, logged separately. A silent default that reads as a
            # checked negative is what made twelve days of `no_live_space`
            # unfalsifiable from the logs alone.
            "space_checked": payload.get("space_checked"),
            "award_space": payload.get("award_space"),
            "coverage": payload.get("coverage"),
            "options_considered": payload.get("options_considered"),
            "options_shown": payload.get("options_shown"),
            # §6.3 — gated routes are never hidden, so they are never omitted
            # from the log either. A run with 0 shown options and 10 gated ones
            # is a completely different result from a run that found nothing.
            "gated_summary": payload.get("gated_summary"),
            "gated_count": len(payload.get("gated_options") or []),
            "carriers_serving": payload.get("carriers_serving"),
            "live_award_space": payload.get("live_award_space"),
            # Carried on every row (not just the run banner) so a reader
            # filtering/greping individual route lines still sees whether
            # the cache was actually cold for this run.
            "redis_wiped_before_run": redis_status["redis_wiped"],
            "redis_wipe_warning": redis_status["redis_wipe_warning"],
        }
        rows.append(row)

        with jsonl_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

        _append_markdown_summary(md_path, row, header=False)

        print(
            f"[{i:02d}/{len(cases)}] {route.key()} {case.currency} "
            f"→ {row.get('verdict') or row.get('error')} ({elapsed}s)",
            flush=True,
        )

    meta["finished_at"] = datetime.now(timezone.utc).isoformat()
    meta["log_jsonl"] = str(jsonl_path.relative_to(_REPO))
    meta["log_md"] = str(md_path.relative_to(_REPO))
    meta["future_routes"] = list(FUTURE_ROUTE_IDEAS)

    stores.close()
    repo.close()
    return {"meta": meta, "rows": rows}


def _append_markdown_summary(path: Path, row: dict[str, Any], *, header: bool) -> None:
    lines: list[str] = []
    if header and not path.exists():
        lines.append(f"# Beta daily sweep — {date.today().isoformat()}\n")
        lines.append(f"Balance: **{BALANCE:,}** pts per route\n")
    lines.append(f"## {row['index']}. {row['route']} ({row['currency']} / {row['card']})\n")
    if row.get("note"):
        lines.append(f"_{row['note']}_\n")
    if row.get("error"):
        lines.append(f"- **Error:** {row.get('message') or row['error']}\n")
    else:
        fare = row.get("fare_cents")
        fare_bit = f"fare ${fare / 100:.0f}" if fare else "no market fare (degraded)"
        space = (
            "space checked" if row.get("space_checked") else "space NOT checked"
        )
        shown, considered = row.get("options_shown"), row.get("options_considered")
        cap = (
            f" · showing {shown} of {considered}"
            if considered and shown and considered > shown
            else ""
        )
        lines.append(f"- **Verdict:** {row.get('verdict')} · {fare_bit} · {space}{cap}\n")
        for j, opt in enumerate(row.get("top3") or [], start=1):
            flags = ", ".join(opt.get("flags") or []) or "—"
            cpp = f"{opt['cpp']}¢/pt" if opt.get("cpp") is not None else "cpp n/a"
            paid = opt.get("price_paid_usd")
            paid_bit = f"${paid:,.0f}" if paid is not None else "$?"
            lines.append(
                f"- **#{j}** {opt.get('label')} — {opt.get('source_points'):,} pts · "
                f"{paid_bit} · {cpp} · {opt.get('space') or '?'} [{flags}]\n"
            )
        for entry in row.get("gated_summary") or []:
            lines.append(
                f"- **gated:** {entry['routes']} route(s) {entry['requirement']}\n"
            )
        if not (row.get("top3") or row.get("gated_summary")):
            # An empty list is a real answer here — "nothing you hold can book
            # this" — but only if it says so. A bare blank block is the same
            # ambiguity as `no_live_space`: unbookable and unqueried look
            # identical until one of them explains itself.
            lines.append(
                f"- _no bookable option: {row.get('rationale') or 'see `coverage` in the JSONL'}_\n"
            )
            lines.append(f"- _carriers serving this pair: "
                         f"{', '.join(row.get('carriers_serving') or []) or 'none in the service map'}_\n")
    cache = f"{row.get('cache_hits', 0)}h/{row.get('cache_misses', 0)}m"
    lines.append(f"- _{row['elapsed_s']}s · cache {cache} · snap {row.get('snapshot', '?')}_\n\n")
    with path.open("a", encoding="utf-8") as fh:
        fh.writelines(lines)


# --------------------------------------------------------------------------- #
# Pytest entry (opt-in — not part of default hermetic suite)
# --------------------------------------------------------------------------- #
@pytest.mark.beta_daily
def test_beta_daily_routes_log() -> None:
    """Live beta sweep; skipped in CI unless ``-m beta_daily``."""
    if os.environ.get("CI") and os.environ.get("BETA_DAILY_FORCE") != "1":
        pytest.skip("beta daily sweep is manual/local only (set BETA_DAILY_FORCE=1 on CI)")

    report = run_beta_daily_sweep()
    assert report["rows"], "no routes ran"

    if report["meta"].get("redis_wipe_warning"):
        # Logged, not failed — the month demo needs the paper trail even
        # when the cache wasn't confirmed clear. The log itself (jsonl
        # run_meta line + md banner) also carries this, so it's auditable
        # after the fact, not just visible in this console output.
        print(
            f"\n⚠️  REDIS NOT WIPED before this run: {report['meta'].get('redis_wipe_note')}",
            file=sys.stderr,
        )

    errors = [r for r in report["rows"] if r.get("error")]
    # Log failures but do not fail the sweep — month demo needs the paper trail.
    if errors:
        print(f"\n{len(errors)} route(s) returned errors (logged):", file=sys.stderr)
        for r in errors:
            print(f"  {r['route']}: {r.get('message') or r['error']}", file=sys.stderr)


if __name__ == "__main__":
    report = run_beta_daily_sweep()
    print(json.dumps(report["meta"], indent=2))