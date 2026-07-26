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
