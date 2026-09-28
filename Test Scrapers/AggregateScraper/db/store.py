"""SQLite persistence for edges, runs, and alerts."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


class Database:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self) -> None:
        schema = Path(__file__).parent / "schema.sql"
        with self.connect() as conn:
            conn.executescript(schema.read_text())

    def save_run(
        self,
        run_type: str,
        origin: str,
        dest: str,
        cabin: str,
        cash: float,
        fees: float,
        result: dict,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (run_type, origin, destination, cabin, cash_usd,
                                  fees_usd, started_at, finished_at, result_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_type, origin, dest, cabin, cash, fees, now, now, json.dumps(result)),
            )

    def save_alert(self, feed_name: str, title: str, url: str, programs: list[str]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO alerts (feed_name, title, url, programs, detected_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (feed_name, title, url, ",".join(programs), datetime.now(timezone.utc).isoformat()),
            )

    def get_wayback_cache(
        self, target_url: str, *, max_age_days: int = 7
    ) -> tuple[str | None, str | None, datetime] | None:
        """Return cached snapshot if fresh. snapshot_url=None means confirmed miss."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT snapshot_url, snapshot_ts, checked_at FROM wayback_cache WHERE target_url = ?",
                (target_url,),
            ).fetchone()
        if not row:
            return None
        try:
            checked_at = datetime.fromisoformat(row["checked_at"].replace("Z", "+00:00"))
        except ValueError:
            return None
        age = datetime.now(timezone.utc) - checked_at
        if age > timedelta(days=max_age_days):
            return None
        return row["snapshot_url"], row["snapshot_ts"], checked_at

    def save_wayback_cache(
        self,
        target_url: str,
        snapshot_url: str | None,
        snapshot_ts: str | None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO wayback_cache (target_url, snapshot_url, snapshot_ts, checked_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(target_url) DO UPDATE SET
                    snapshot_url = excluded.snapshot_url,
                    snapshot_ts = excluded.snapshot_ts,
                    checked_at = excluded.checked_at
                """,
                (target_url, snapshot_url, snapshot_ts, now),
            )

    def flag_stale_programs(self, programs: list[str]) -> None:
        if not programs:
            return
        with self.connect() as conn:
            for program in programs:
                conn.execute(
                    """
                    UPDATE edges SET flags = flags || ',stale'
                    WHERE from_node = ? OR to_node LIKE ?
                    """,
                    (program, f"%{program}%"),
                )
