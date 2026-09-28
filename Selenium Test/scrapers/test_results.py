"""Write per-test / per-site run output under ``test-results/``."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scrapers.contracts import PROJECT_ROOT

DEFAULT_RESULTS_DIR = "test-results"


def results_root() -> Path:
    raw = os.environ.get("TEST_RESULTS_DIR", DEFAULT_RESULTS_DIR)
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def timestamp_slug(when: datetime | None = None) -> str:
    when = when or datetime.now(timezone.utc)
    return when.strftime("%Y%m%dT%H%M%SZ")


def safe_name(name: str) -> str:
    cleaned = re.sub(r"[^\w.\-]+", "_", name.strip()).strip("._")
    return cleaned or "result"


def _atomic_write(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    data = json.dumps(payload, indent=2, default=str, ensure_ascii=False) + "\n"
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)
    return path


def start_run(kind: str = "run") -> Path:
    run_dir = results_root() / f"{timestamp_slug()}_{safe_name(kind)}"
    run_dir.mkdir(parents=True, exist_ok=True)
    write_result(
        run_dir,
        "run",
        {
            "kind": kind,
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "results_dir": str(run_dir),
        },
    )
    return run_dir


def write_result(run_dir: Path, name: str, payload: Any) -> Path:
    path = run_dir / f"{safe_name(name)}.json"
    return _atomic_write(path, payload)


def write_summary(run_dir: Path, payload: Any) -> Path:
    if isinstance(payload, dict):
        payload = {
            **payload,
            "status": payload.get("status", "complete"),
            "finished_at": payload.get("finished_at") or datetime.now(timezone.utc).isoformat(),
            "results_dir": payload.get("results_dir") or str(run_dir),
        }
    return write_result(run_dir, "summary", payload)


def live_scrape_record(
    *,
    site: str,
    browser: str,
    validation: Any,
    artifact: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """JSON payload for one live scrape attempt (httpx / chrome / tor / login)."""
    artifact = artifact or {}
    validation_dict = validation.to_dict() if hasattr(validation, "to_dict") else dict(validation)
    payload: dict[str, Any] = {
        "kind": "live_scrape",
        "site": site,
        "browser": browser,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "validation": validation_dict,
        "deliverable_met": validation_dict.get("deliverable_met"),
        "outcome": validation_dict.get("outcome"),
        "chart_confidence": validation_dict.get("chart_confidence"),
        "messages": validation_dict.get("messages", []),
        "html_path": artifact.get("html_path", ""),
        "screenshot_path": artifact.get("screenshot_path", ""),
        "artifact": artifact,
    }
    if extra:
        payload.update(extra)
    return payload
