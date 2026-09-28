"""Shared plumbing for the LifeMiles scraping lab.

This module is intentionally self-contained so the lab can be run on its own
(`python run_lab.py`) without importing the parent mini-scraper package. It
provides:

  * A normalized data model (``AwardRow`` / ``StrategyResult``).
  * Robust mileage / region parsing helpers.
  * Anti-detection Playwright helpers (LifeMiles sits behind Akamai).
  * Per-strategy logging that writes both to the console and a shared run log.
  * A transparent scoring function used to build the leaderboard.

Why the lab exists: the production ``lifemiles_scraper`` vision-scrapes
``lifemiles.com/use/redeem-miles/flights``, but that URL is an Akamai-protected
JS booking widget with *no* award chart on the page, so the screenshot fed to
the vision model never contains the data. This lab pits several extraction
strategies against each other to find the one that actually returns the chart.
"""

from __future__ import annotations

import io
import os
import re
import json
import time
import random
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from rich.console import Console

LAB_DIR = Path(__file__).resolve().parent
LOG_DIR = LAB_DIR / "logs"
OUTPUT_DIR = LAB_DIR / "output"
LOG_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

console = Console()

# Single timestamped run id so every artifact from one lab run groups together.
RUN_ID = datetime.now().strftime("%Y%m%d_%H%M%S")
RUN_LOG_PATH = LOG_DIR / f"lab_{RUN_ID}.log"

LIFEMILES_URL = os.getenv(
    "LIFEMILES_URL", "https://www.lifemiles.com/use/redeem-miles/flights"
)


# ── Data model ─────────────────────────────────────────────────────────────


@dataclass
class AwardRow:
    """One origin→destination award price, normalized across all strategies.

    Ranges from the source (e.g. "35,000-45,000") are preserved in ``raw`` while
    the canonical ``*_miles`` ints hold the *low* end of the range (the cheapest,
    most relevant figure for "what's the best redemption").
    """

    origin: str
    destination: str
    economy_miles: Optional[int] = None
    business_miles: Optional[int] = None
    first_miles: Optional[int] = None
    one_way: bool = True
    source: str = ""
    raw: dict = field(default_factory=dict)

    def has_any_price(self) -> bool:
        return any(
            v is not None
            for v in (self.economy_miles, self.business_miles, self.first_miles)
        )


@dataclass
class StrategyResult:
    """The uniform return type every strategy produces."""

    name: str
    label: str
    ok: bool = False
    rows: list[AwardRow] = field(default_factory=list)
    duration_ms: int = 0
    confidence: float = 0.0  # strategy's self-reported trust in its output, 0..1
    cost: str = "low"        # low | medium | high — runtime / dependency weight
    notes: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    error: Optional[str] = None
    score: float = 0.0       # filled in by score_result()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rows"] = [asdict(r) for r in self.rows]
        return d


# ── Parsing helpers ────────────────────────────────────────────────────────

_NULL_TOKENS = {"", "-", "—", "–", "n/a", "na", "none", "null"}


def parse_miles(value) -> tuple[Optional[int], Optional[int]]:
    """Parse a mileage cell into ``(low, high)`` integers.

    Handles plain ints, comma-grouped strings ("12,100"), ranges
    ("35,000-45,000" / "35,000 – 45,000"), "from 6,500", and null tokens
    ("—", "-", "", "N/A"). Returns ``(None, None)`` when no number is present.
    """
    if value is None:
        return None, None
    if isinstance(value, (int, float)):
        n = int(value)
        return n, n
    text = str(value).strip().lower()
    if text in _NULL_TOKENS:
        return None, None
    nums = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", text)]
    if not nums:
        return None, None
    low, high = min(nums), max(nums)
    return low, high


def looks_like_miles(n: Optional[int], lo: int = 2000, hi: int = 400000) -> bool:
    """Sanity bound: a real award price is roughly 2k–400k miles."""
    return n is not None and lo <= n <= hi


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Logging ────────────────────────────────────────────────────────────────

_LOGGERS: dict[str, logging.Logger] = {}


def get_logger(name: str) -> logging.Logger:
    """Logger that writes to the console (rich) + a per-strategy file + the run log."""
    if name in _LOGGERS:
        return _LOGGERS[name]

    logger = logging.getLogger(f"lab.{name}")
    logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s | %(name)-22s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )

    from rich.logging import RichHandler

    rich_handler = RichHandler(console=console, show_path=False, markup=False)
    rich_handler.setFormatter(
        logging.Formatter("%(message)s", datefmt="%H:%M:%S")
    )
    logger.addHandler(rich_handler)

    per_file = logging.FileHandler(LOG_DIR / f"strategy_{name}.log", encoding="utf-8")
    per_file.setFormatter(fmt)
    logger.addHandler(per_file)

    run_file = logging.FileHandler(RUN_LOG_PATH, encoding="utf-8")
    run_file.setFormatter(fmt)
    logger.addHandler(run_file)

    _LOGGERS[name] = logger
    return logger


# ── Anti-detection Playwright helpers ──────────────────────────────────────
#
# LifeMiles is fronted by Akamai (plain requests get a 403 "Access Denied" from
# errors.edgesuite.net). A vanilla headless Playwright fingerprint is also
# blocked, so every browser strategy routes through these helpers.

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

# Kept minimal: aggressive flags (--no-sandbox / --disable-gpu) can SIGABRT
# Chromium inside restrictive sandboxes. Real hardening is in the init script.
STEALTH_ARGS = ["--disable-blink-features=AutomationControlled"]

STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || { runtime: {} };
"""

BROWSER_HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Upgrade-Insecure-Requests": "1",
}


def pick_user_agent() -> str:
    return random.choice(USER_AGENTS)


async def launch_stealth_browser(p, *, headless: bool = True, extra_args=None):
    args = list(STEALTH_ARGS)
    if extra_args:
        args.extend(extra_args)
    return await p.chromium.launch(headless=headless, args=args)


async def new_stealth_page(browser, *, user_agent: Optional[str] = None):
    context = await browser.new_context(
        user_agent=user_agent or pick_user_agent(),
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
        timezone_id="America/New_York",
        extra_http_headers=BROWSER_HEADERS,
    )
    await context.add_init_script(STEALTH_INIT_SCRIPT)
    page = await context.new_page()
    return context, page


async def detect_block(page) -> tuple[bool, str]:
    """Detect an Akamai / WAF 'Access Denied' interstitial.

    LifeMiles serves a bare "Access Denied" page (title + edgesuite reference)
    when it blocks a client. Strategies must treat a blocked page as having *no*
    content — critically, a vision model handed a screenshot of this page will
    happily *hallucinate* a plausible-looking award chart, so we detect the block
    and refuse to trust (or even bother computing) any extraction from it.
    """
    try:
        title = (await page.title()) or ""
    except Exception:  # noqa: BLE001
        title = ""
    body = ""
    try:
        if await page.query_selector("body"):
            body = (await page.inner_text("body"))[:1500]
    except Exception:  # noqa: BLE001
        pass
    haystack = f"{title}\n{body}".lower()
    for token in ("access denied", "edgesuite", "you don't have permission"):
        if token in haystack:
            return True, f"blocked: {title!r}"
    return False, title


async def goto_resilient(page, url: str, *, timeout_ms: int, logger=None) -> bool:
    """Navigate, loosening the wait condition on each retry. Returns success."""
    for wait_until in ("domcontentloaded", "load", "commit"):
        try:
            resp = await page.goto(url, timeout=timeout_ms, wait_until=wait_until)
            status = resp.status if resp else "n/a"
            if logger:
                logger.info(f"navigated wait_until={wait_until} http_status={status}")
            return True
        except Exception as exc:  # noqa: BLE001
            if logger:
                logger.warning(
                    f"goto wait_until={wait_until} failed "
                    f"({type(exc).__name__}): {str(exc)[:120]}"
                )
    return False


def save_artifact(name: str, data: bytes | str) -> str:
    """Persist an artifact (screenshot, payload, html) under output/ and return its path."""
    path = OUTPUT_DIR / f"{RUN_ID}_{name}"
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(path, mode, encoding=None if isinstance(data, bytes) else "utf-8") as f:
        f.write(data)
    return str(path)


# ── Scoring ────────────────────────────────────────────────────────────────

_COST_PENALTY = {"low": 1.0, "medium": 0.75, "high": 0.5}


def score_result(r: StrategyResult) -> float:
    """Transparent 0..100 score balancing data richness, trust, speed, and cost.

    Weights (sum 100):
      * 45 — data completeness (rows with a real price, saturating at 10 rows)
      * 30 — self-reported confidence
      * 15 — speed (full marks <=3s, decays to 0 by ~120s)
      * 10 — dependency / runtime cost (low > medium > high)
    A failed strategy (no priced rows) scores 0 regardless.
    """
    priced = [row for row in r.rows if row.has_any_price()]
    if not r.ok or not priced:
        return 0.0

    completeness = min(len(priced), 10) / 10.0
    speed = max(0.0, 1.0 - max(0, r.duration_ms - 3000) / 117000.0)
    cost = _COST_PENALTY.get(r.cost, 0.5)

    score = (
        45 * completeness
        + 30 * max(0.0, min(1.0, r.confidence))
        + 15 * speed
        + 10 * cost
    )
    return round(score, 1)
