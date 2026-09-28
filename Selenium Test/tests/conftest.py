"""Pytest fixtures. Browser tests require REMOTE_SCRAPE=1 (Ubuntu VPS)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scrapers.test_results import start_run, write_result, write_summary

_RUN_DIR: Path | None = None
_TEST_RECORDS: list[dict[str, Any]] = []


def remote_scrape_enabled() -> bool:
    return os.environ.get("REMOTE_SCRAPE") == "1"


@pytest.fixture
def require_vm_scrape() -> None:
    if not remote_scrape_enabled():
        pytest.skip("REMOTE_SCRAPE=1 is required — run browser tests on the Ubuntu VPS")


@pytest.fixture
def record_scrape(pytestconfig):
    """Write a live scrape payload into this pytest run's test results folder."""
    run_dir = getattr(pytestconfig, "_test_results_dir", _RUN_DIR)

    def _record(name: str, payload: dict[str, Any]) -> None:
        if run_dir is None:
            return
        write_result(run_dir, name, payload)

    return _record


def pytest_configure(config: pytest.Config) -> None:
    global _RUN_DIR
    _RUN_DIR = start_run("pytest")
    config._test_results_dir = _RUN_DIR  # type: ignore[attr-defined]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]):
    outcome = yield
    report = outcome.get_result()
    if report.when != "call" or _RUN_DIR is None:
        return
    payload = {
        "nodeid": item.nodeid,
        "name": item.name,
        "outcome": report.outcome,
        "duration_seconds": round(report.duration, 4),
        "passed": report.passed,
        "failed": report.failed,
        "skipped": report.skipped,
        "longrepr": str(report.longrepr) if report.longrepr else "",
    }
    _TEST_RECORDS.append(payload)
    write_result(_RUN_DIR, item.nodeid.replace("::", "__"), payload)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if _RUN_DIR is None:
        return
    passed = sum(1 for row in _TEST_RECORDS if row["outcome"] == "passed")
    failed = sum(1 for row in _TEST_RECORDS if row["outcome"] == "failed")
    skipped = sum(1 for row in _TEST_RECORDS if row["outcome"] == "skipped")
    write_summary(
        _RUN_DIR,
        {
            "kind": "pytest",
            "exitstatus": exitstatus,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "total": len(_TEST_RECORDS),
            "results_dir": str(_RUN_DIR),
            "tests": _TEST_RECORDS,
        },
    )
    terminal = session.config.pluginmanager.get_plugin("terminalreporter")
    if terminal is not None:
        terminal.write_sep("=", f"Saved {len(_TEST_RECORDS)} test result(s) to {_RUN_DIR}")
