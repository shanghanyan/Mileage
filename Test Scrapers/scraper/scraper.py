#!/usr/bin/env python3
"""
Local AI Travel Intelligence Scraper
United + Delta + American + partners + aggregators — Playwright + Ollama LLaVA
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from collections import deque
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

import yaml

# Ensure scraper package root is on path when run as script
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from crawl.browser import BrowserSession
from crawl.scope import ScopeManager, discover_sitemap_urls, extract_links, normalize_url
from extraction.dom import extract_dom
from extraction.merge import merge_extractions, records_from_merged
from extraction.vision import VisionExtractor
from storage.csv_export import export_csv
from storage.sqlite_store import SQLiteStore

# ============================================================
# CONFIGURE THESE PATHS BEFORE RUNNING
# Use absolute paths or paths relative to this script file.
# Leave blank to use defaults next to this script.
# ============================================================

# Defaults: folders next to this script (see resolve_dirs).
# Override with absolute paths if you prefer, e.g.:
# SCREENSHOTS_DIR = "/Users/you/Code Projects/Pointy/scraper/screenshots"
SCREENSHOTS_DIR = ""
DATA_DIR = ""

# ============================================================

DEFAULT_SCREENSHOTS = _SCRIPT_DIR / "screenshots"
DEFAULT_DATA = _SCRIPT_DIR / "data"

CHECKPOINT_FILE = "checkpoint.json"
VISIT_LOG_FILE = "visit_log.jsonl"
FAILED_URLS_FILE = "failed_urls.log"
SKIPPED_URLS_FILE = "skipped_urls.log"
SQLITE_FILE = "united_scraper.sqlite"
OUTPUT_CSV = "output.csv"

CONSECUTIVE_FAIL_THRESHOLD = 10


def resolve_dirs() -> tuple[Path, Path]:
    shots = Path(SCREENSHOTS_DIR) if SCREENSHOTS_DIR.strip() else DEFAULT_SCREENSHOTS
    data = Path(DATA_DIR) if DATA_DIR.strip() else DEFAULT_DATA
    if not shots.is_absolute():
        shots = (_SCRIPT_DIR / shots).resolve()
    if not data.is_absolute():
        data = (_SCRIPT_DIR / data).resolve()
    return shots, data


def setup_logging(data_dir: Path) -> logging.Logger:
    data_dir.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("scraper")
    log.setLevel(logging.INFO)
    log.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    log.addHandler(console)

    today = datetime.now().strftime("%Y-%m-%d")
    file_handler = TimedRotatingFileHandler(
        data_dir / f"scraper_{today}.log",
        when="midnight",
        backupCount=7,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    log.addHandler(file_handler)
    return log


def load_config() -> dict[str, Any]:
    config_path = _SCRIPT_DIR / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def append_line(path: Path, line: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(line.rstrip() + "\n")


def load_checkpoint(data_dir: Path) -> dict[str, Any] | None:
    cp = data_dir / CHECKPOINT_FILE
    if cp.exists():
        with open(cp, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_checkpoint(
    data_dir: Path,
    queue: deque,
    visited: list[str],
    depth_map: dict[str, int],
) -> None:
    cp = data_dir / CHECKPOINT_FILE
    with open(cp, "w", encoding="utf-8") as f:
        json.dump(
            {
                "queue": list(queue),
                "visited": visited,
                "depth_map": depth_map,
                "saved_at": datetime.now(timezone.utc).isoformat(),
            },
            f,
        )


def seed_queue(config: dict[str, Any], scope: ScopeManager) -> deque:
    q: deque = deque()
    for domain_cfg in config.get("seeds", {}).values():
        for url in domain_cfg.get("urls", []):
            norm = normalize_url(url)
            scope.seed_depth(norm)
            q.append(norm)
    return q


def add_sitemap_seeds(
    config: dict[str, Any],
    queue: deque,
    scope: ScopeManager,
    log: logging.Logger,
) -> None:
    if not config.get("crawl", {}).get("sitemap_discovery", True):
        return
    for domain in config.get("scope", {}):
        try:
            urls = discover_sitemap_urls(domain)
            added = 0
            for url in urls:
                ok, _ = scope.can_enqueue(url)
                if ok:
                    scope.seed_depth(url)
                    queue.append(url)
                    added += 1
            if added:
                log.info("Sitemap %s: added %d URLs", domain, added)
        except Exception as e:
            log.warning("Sitemap fetch failed for %s: %s", domain, e)


class ScraperOrchestrator:
    def __init__(
        self,
        config: dict[str, Any],
        screenshots_dir: Path,
        data_dir: Path,
        log: logging.Logger,
        headless: bool = True,
    ):
        self.config = config
        self.screenshots_dir = screenshots_dir
        self.data_dir = data_dir
        self.log = log
        self.headless = headless

        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.scope = ScopeManager(
            config.get("scope", {}),
            config.get("url_blocklist", []),
            config.get("aggregator_domains", []),
        )
        crawl_cfg = config.get("crawl", {})
        slow_mo = int(crawl_cfg.get("browser_slow_mo_ms", 80))
        self.browser = BrowserSession(headless=headless, slow_mo_ms=slow_mo)
        self.db = SQLiteStore(self.data_dir / SQLITE_FILE)

        ollama_ok, llava_ok = VisionExtractor.check_ollama(
            config.get("ollama_host", "http://localhost:11434")
        )
        vision_enabled = llava_ok
        if not ollama_ok:
            log.warning("Ollama is not running — vision mode disabled. DOM-only crawl continues.")
        elif not llava_ok:
            log.warning("LLaVA model not found — run: ollama pull llava. Vision disabled.")

        self.vision = VisionExtractor(
            enabled=vision_enabled,
            model=config.get("vision_model", "llava"),
            host=config.get("ollama_host", "http://localhost:11434"),
            timeout=config.get("vision_timeout_seconds", 30),
        )

        self.queue: deque = deque()
        self.visited: set[str] = set()
        self.depth_map: dict[str, int] = {}
        self.page_counter = 0
        self.failed_count = 0
        self.visited_count = 0
        self._domain_fail_streak: dict[str, int] = {}
        self.stop_reason: str | None = None  # complete | timeout | interrupt
        self.max_runtime_seconds = int(
            config.get("crawl", {}).get("max_runtime_seconds", 3600)
        )

        self.visit_log_path = self.data_dir / VISIT_LOG_FILE
        self.failed_path = self.data_dir / FAILED_URLS_FILE
        self.skipped_path = self.data_dir / SKIPPED_URLS_FILE

    def _rate_limits(self, url: str) -> tuple[float, float]:
        rl = self.config.get("rate_limit", {})
        if self.scope.is_aggregator(url):
            return (
                rl.get("aggregator_min_seconds", 8),
                rl.get("aggregator_max_seconds", 15),
            )
        return (
            rl.get("default_min_seconds", 3),
            rl.get("default_max_seconds", 8),
        )

    async def scrape_page(self, url: str) -> dict[str, Any]:
        self.page_counter += 1
        page_id = self.page_counter
        dom_data: dict[str, Any] = {}
        vision_data: dict[str, Any] = {}
        dom_success = False
        vision_success = False
        dom_fail: str | None = None
        vision_fail: str | None = None
        html = ""
        page = None

        try:
            page, html, _status = await self.browser.load_page(url)
            dom_data, dom_success, dom_fail = await extract_dom(page, html, url)
            vision_data, vision_success, vision_fail, _shot = await self.vision.extract(
                page, url, self.screenshots_dir, page_id
            )
        except Exception as e:
            dom_fail = dom_fail or str(e)
            vision_fail = vision_fail or str(e)
            self.log.warning("[PAGE_ERROR] %s — %s", url, e)
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass

        merged = merge_extractions(vision_data, dom_data)
        return {
            "url": url,
            "visited_at": datetime.now(timezone.utc).isoformat(),
            "dom_success": dom_success,
            "vision_success": vision_success,
            "dom_fail_reason": dom_fail,
            "vision_fail_reason": vision_fail,
            "data": merged,
            "html": html,
        }

    def _track_domain_failure(self, url: str, both_failed: bool) -> None:
        key = self.scope._domain_key(url)
        if both_failed:
            self._domain_fail_streak[key] = self._domain_fail_streak.get(key, 0) + 1
            if self._domain_fail_streak[key] == CONSECUTIVE_FAIL_THRESHOLD:
                self.log.error(
                    "[DOMAIN_WARN] %s — %d consecutive failures; consider skipping",
                    key,
                    CONSECUTIVE_FAIL_THRESHOLD,
                )
        else:
            self._domain_fail_streak[key] = 0

    def _enqueue_links(self, page_record: dict[str, Any], parent_url: str) -> None:
        html = page_record.get("html") or ""
        if not html:
            return
        links = extract_links(html, parent_url)
        parent_depth = self.depth_map.get(parent_url, 0)
        for link in links:
            if link in self.visited:
                continue
            if not self.scope.same_scope_domain(link, parent_url):
                continue
            ok, reason = self.scope.can_enqueue(link, parent_url)
            if not ok:
                append_line(self.skipped_path, f"{link}\t{reason}")
                continue
            child_depth = parent_depth + 1
            self.depth_map[link] = child_depth
            self.scope._depth_by_url[link] = child_depth
            self.queue.append(link)

    async def run(self) -> None:
        await self.browser.start()

        checkpoint = load_checkpoint(self.data_dir)
        if checkpoint:
            self.log.info("Resuming from checkpoint (%d queued)", len(checkpoint.get("queue", [])))
            self.queue = deque(checkpoint.get("queue", []))
            self.visited = set(checkpoint.get("visited", []))
            self.depth_map = checkpoint.get("depth_map", {})
            for u, d in self.depth_map.items():
                self.scope._depth_by_url[u] = d
        else:
            self.queue = seed_queue(self.config, self.scope)
            add_sitemap_seeds(self.config, self.queue, self.scope, self.log)
            for u in list(self.queue):
                self.depth_map[u] = 0

        crawl_start = time.monotonic()
        self.log.info(
            "Max runtime: %d seconds (%.1f hours)",
            self.max_runtime_seconds,
            self.max_runtime_seconds / 3600,
        )

        try:
            while self.queue:
                elapsed = time.monotonic() - crawl_start
                if elapsed >= self.max_runtime_seconds:
                    self.stop_reason = "timeout"
                    self.log.warning(
                        "Max runtime reached (%.0fs / %ds) — stopping crawl",
                        elapsed,
                        self.max_runtime_seconds,
                    )
                    break

                url = normalize_url(self.queue.popleft())
                if url in self.visited:
                    continue

                can, reason = self.scope.can_visit(url)
                if not can:
                    append_line(self.skipped_path, f"{url}\t{reason}")
                    self.log.info("Skipped: %s (%s)", url, reason)
                    continue

                self.visited.add(url)
                self.scope.register_visit(url)
                self.visited_count += 1

                min_d, max_d = self._rate_limits(url)
                self.log.info("Visiting: %s", url)
                await BrowserSession.random_delay(min_d, max_d)

                try:
                    page_record = await self.scrape_page(url)
                except Exception as e:
                    self.log.error("[UNHANDLED] %s — %s", url, e)
                    page_record = {
                        "url": url,
                        "visited_at": datetime.now(timezone.utc).isoformat(),
                        "dom_success": False,
                        "vision_success": False,
                        "dom_fail_reason": str(e),
                        "vision_fail_reason": str(e),
                        "data": {},
                        "html": "",
                    }

                both_failed = not page_record["dom_success"] and not page_record["vision_success"]
                if both_failed:
                    append_line(self.failed_path, url)
                    self.failed_count += 1
                    self.log.error("[BOTH_FAIL] %s — added to failed_urls.log", url)
                else:
                    if page_record["dom_success"]:
                        self.log.info("DOM success for %s", url)
                    if page_record["vision_success"]:
                        self.log.info("Vision success for %s", url)

                self._track_domain_failure(url, both_failed)

                visit_entry = {
                    k: v for k, v in page_record.items() if k != "html"
                }
                self.db.save_page_visit(visit_entry)
                append_jsonl(self.visit_log_path, visit_entry)

                records = records_from_merged(
                    page_record["data"],
                    url,
                    page_record["dom_success"],
                    page_record["vision_success"],
                )
                if records:
                    by_type: dict[str, int] = {}
                    for r in records:
                        by_type[r["record_type"]] = by_type.get(r["record_type"], 0) + 1
                    self.log.info(
                        "Extracted records: %s",
                        ", ".join(f"{k} ({v})" for k, v in by_type.items()),
                    )
                    self.db.save_records(records)

                self._enqueue_links(page_record, url)

                save_checkpoint(
                    self.data_dir,
                    self.queue,
                    list(self.visited),
                    self.depth_map,
                )
            else:
                if not self.queue and self.stop_reason is None:
                    self.stop_reason = "complete"
        finally:
            await self.browser.stop()

    def shutdown(self) -> None:
        records = self.db.fetch_all_records()
        csv_path = self.data_dir / OUTPUT_CSV
        export_csv(records, csv_path)
        self.log.info("Exported %d records to %s", len(records), csv_path)

        cp = self.data_dir / CHECKPOINT_FILE
        if self.stop_reason == "complete" and cp.exists():
            cp.unlink()
            self.log.info("Checkpoint removed (clean completion)")
        elif cp.exists():
            self.log.info(
                "Checkpoint kept at %s (%s) — run again to resume",
                cp,
                self.stop_reason or "stopped early",
            )

        counts = self.db.count_by_type()
        self.log.info("=" * 60)
        self.log.info("CRAWL SUMMARY (%s)", self.stop_reason or "stopped")
        self.log.info("  Pages visited: %d", self.visited_count)
        self.log.info("  Pages failed (both modes): %d", self.failed_count)
        if self.queue:
            self.log.info("  URLs still in queue: %d", len(self.queue))
        for rtype, count in sorted(counts.items()):
            self.log.info("  Records [%s]: %d", rtype, count)
        self.log.info("=" * 60)
        self.db.close()


async def main() -> None:
    shots, data = resolve_dirs()
    if not str(SCREENSHOTS_DIR).strip() and not str(DATA_DIR).strip():
        pass  # defaults OK
    log = setup_logging(data)
    config = load_config()

    shots.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)

    log.info("Screenshots: %s", shots)
    log.info("Data: %s", data)
    log.info(
        "Scope: %s",
        ", ".join(config.get("scope", {}).keys()),
    )

    headless = config.get("crawl", {}).get("headless", True)
    if not headless:
        log.info("Browser: visible Chromium (headless=false) — watch scrolling on screen")
    orchestrator = ScraperOrchestrator(config, shots, data, log, headless=headless)
    try:
        await orchestrator.run()
    except KeyboardInterrupt:
        orchestrator.stop_reason = "interrupt"
        log.warning("Interrupted — saving checkpoint and partial CSV")
    finally:
        orchestrator.shutdown()


# ============================================================
# SCHEDULED RE-CRAWL — uncomment to enable
# Runs the full crawl on a schedule using APScheduler
# Requires: pip install apscheduler
# ============================================================

# from apscheduler.schedulers.blocking import BlockingScheduler
#
# def run_scheduled_crawl():
#     print(f"[SCHEDULER] Starting scheduled crawl at {datetime.now(timezone.utc)}")
#     asyncio.run(main())
#
# scheduler = BlockingScheduler()
# scheduler.add_job(run_scheduled_crawl, "cron", hour=3, minute=0)
#
# if __name__ == "__main__":
#     print("[SCHEDULER] Scheduled crawl active. Press Ctrl+C to stop.")
#     scheduler.start()
# ============================================================

if __name__ == "__main__":
    asyncio.run(main())
