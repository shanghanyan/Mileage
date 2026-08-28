"""Test-suite invariants: every test runs OFFLINE and against a throwaway DB.

Two properties the plan promised but the suite did not enforce (§10/§12 Phase 5):

  1. **Hermetic / deterministic.** `MILEAGE_OFFLINE=1` pins the aggregator to its
     `file://` fixtures — no live HTTP, no Wayback — so tests never touch the
     network and can't hang on a blocked egress (the old failure mode: live URLs
     in `sources.yaml` made the fetcher block ~10s/target under the politeness
     backoff, timing the suite out).
  2. **No collateral writes.** Point `MILEAGE_DB` at a temp file so a test run
     never mutates the developer's real `mileage.db` (and SQLite never tries to
     lock a file on a synced/mounted folder, which raised disk-I/O errors).

This file is auto-loaded by pytest AND imported by the standalone
`python tests/test_phaseN.py` entrypoints (see each test's header), so the
guarantees hold no matter how the tests are launched.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("MILEAGE_OFFLINE", "1")
if not os.environ.get("MILEAGE_DB") or os.environ["MILEAGE_DB"] == "mileage.db":
    os.environ["MILEAGE_DB"] = os.path.join(
        tempfile.gettempdir(), "mileage_test_suite.db"
    )

# Trigger dotenv load (mileage.config), then scrub live creds so the suite stays
# hermetic even when the developer has a populated .env (Arize/Redis/Amadeus).
try:
    import mileage.config  # noqa: F401
except Exception:
    pass

for _key in (
    "MILEAGE_REDIS_URL",
    "REDIS_URL",
    "ARIZE_SPACE_ID",
    "ARIZE_SPACE",
    "ARIZE_API_KEY",
    "MILEAGE_TRACE_CONSOLE",
    "AMADEUS_CLIENT_ID",
    "AMADEUS_CLIENT_SECRET",
    "SEATS_AERO_API_KEY",
    "GMAIL_ADDRESS",
    "GMAIL_APP_PASSWORD",
    "BING_SEARCH_API_KEY",
    "SERPAPI_API_KEY",
):
    os.environ.pop(_key, None)

# Reset tracing globals if a prior import enabled them with real creds.
try:
    from mileage import obs as _obs

    _obs.shutdown_tracing()
    _obs._provider = None  # noqa: SLF001 — test isolation
    _obs._enabled = False  # noqa: SLF001
except Exception:
    pass


# --------------------------------------------------------------------------- #
# Award-space claims require a real award-space provider
# --------------------------------------------------------------------------- #
# The ONLY live L3 source wired into this repo is seats.aero
# (providers/seats_aero.py), and its `health()` reports DOWN without
# SEATS_AERO_API_KEY. What remains in offline mode is
# knowledge/fixtures/milefeed.rss + starnet_award_space.json — between them two
# hardcoded rows (Turkish LAX-IST business, LifeMiles LAX-JFK economy).
#
# A twelve-day sweep reported `no_space` on ~88 of 92 displayed options and it
# read as "award space is scarce". It was not: nothing was configured to look.
# Any test asserting that space WAS found is therefore asserting on a two-row
# demo fixture, and passes for reasons unrelated to whether the engine can
# actually see award inventory.
#
# So: positive space claims are skipped unless a real provider is configured.
# The inverse checks — that the engine never reports `no_space` for something
# it did not query — are NOT gated. Those are safety properties that must hold
# precisely when no provider is available.
import pytest as _pytest

AWARD_API_CONFIGURED = bool(os.environ.get("SEATS_AERO_API_KEY"))

requires_award_api = _pytest.mark.skipif(
    not AWARD_API_CONFIGURED,
    reason=(
        "no live award-space provider: SEATS_AERO_API_KEY is unset, so the only "
        "L3 data available is a 2-row offline fixture. A pass here would say "
        "nothing about real award availability."
    ),
)
