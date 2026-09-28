"""SQLite intermediate storage for scraped records."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class SQLiteStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS page_visits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                visited_at TEXT NOT NULL,
                dom_success INTEGER NOT NULL,
                vision_success INTEGER NOT NULL,
                dom_fail_reason TEXT,
                vision_fail_reason TEXT,
                raw_data TEXT
            );
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                record_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                source_url TEXT,
                extracted_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_records_type ON records(record_type);
            """
        )
        self._conn.commit()

    def save_page_visit(self, page_record: dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT INTO page_visits
            (url, visited_at, dom_success, vision_success, dom_fail_reason, vision_fail_reason, raw_data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                page_record["url"],
                page_record["visited_at"],
                int(page_record["dom_success"]),
                int(page_record["vision_success"]),
                page_record.get("dom_fail_reason"),
                page_record.get("vision_fail_reason"),
                json.dumps(page_record.get("data", {}), default=str),
            ),
        )
        self._conn.commit()

    def save_records(self, records: list[dict[str, Any]]) -> None:
        for rec in records:
            rec_copy = dict(rec)
            rtype = rec_copy.pop("record_type", "unknown")
            source_url = rec_copy.get("source_url")
            extracted_at = rec_copy.get("extracted_at")
            payload = rec_copy
            self._conn.execute(
                """
                INSERT INTO records (record_type, payload, source_url, extracted_at)
                VALUES (?, ?, ?, ?)
                """,
                (rtype, json.dumps(payload), source_url, extracted_at),
            )
        self._conn.commit()

    def fetch_all_records(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT record_type, payload, source_url, extracted_at FROM records"
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["payload"])
            rec = {"record_type": row["record_type"], **payload}
            out.append(rec)
        return out

    def count_by_type(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT record_type, COUNT(*) as c FROM records GROUP BY record_type"
        ).fetchall()
        return {row["record_type"]: row["c"] for row in rows}

    def close(self) -> None:
        self._conn.close()
