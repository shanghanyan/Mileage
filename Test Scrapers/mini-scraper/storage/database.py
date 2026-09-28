import json
import os
from datetime import datetime

import aiosqlite

from optimizer.models import (
    TransferEdge,
    Currency,
    ScraperResult,
    OptimizationResult,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transfer_edges (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    from_currency TEXT NOT NULL,
    to_currency   TEXT NOT NULL,
    ratio         REAL NOT NULL,
    edge_cpp      REAL NOT NULL,
    label         TEXT NOT NULL,
    source_name   TEXT NOT NULL,
    source_url    TEXT NOT NULL,
    scraped_at    TEXT NOT NULL,
    is_one_way    INTEGER DEFAULT 0,
    stale         INTEGER DEFAULT 0,
    suspicious    INTEGER DEFAULT 0,
    notes         TEXT,
    UNIQUE(from_currency, to_currency, label)
);

CREATE TABLE IF NOT EXISTS scraper_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at         TEXT NOT NULL,
    duration_ms    INTEGER NOT NULL,
    scrapers_run   INTEGER NOT NULL,
    scrapers_ok    INTEGER NOT NULL,
    scrapers_cached INTEGER NOT NULL,
    scrapers_fail  INTEGER NOT NULL,
    devaluation_alerts INTEGER DEFAULT 0,
    summary_json   TEXT
);

CREATE TABLE IF NOT EXISTS devaluation_alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TEXT NOT NULL,
    program     TEXT NOT NULL,
    headline    TEXT,
    source_url  TEXT,
    resolved    INTEGER DEFAULT 0
);
"""


class Database:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.getenv("SQLITE_PATH", "./storage/optimizer.db")

    async def init(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    async def upsert_edges(self, edges: list[TransferEdge]) -> None:
        async with aiosqlite.connect(self.path) as db:
            for e in edges:
                await db.execute(
                    """
                    INSERT OR REPLACE INTO transfer_edges (
                        from_currency, to_currency, ratio, edge_cpp, label,
                        source_name, source_url, scraped_at,
                        is_one_way, stale, suspicious, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        e.from_currency.value,
                        e.to_currency.value,
                        e.ratio,
                        e.edge_cpp,
                        e.label,
                        e.source_name,
                        e.source_url,
                        e.scraped_at.isoformat(),
                        int(e.is_one_way),
                        int(e.stale),
                        int(e.suspicious),
                        e.notes,
                    ),
                )
            await db.commit()

    async def flag_stale(self, programs: list[str]) -> None:
        if not programs:
            return
        async with aiosqlite.connect(self.path) as db:
            for program in programs:
                pattern = f"%{program}%"
                await db.execute(
                    "UPDATE transfer_edges SET stale = 1 WHERE source_name LIKE ?",
                    (pattern,),
                )
                await db.execute(
                    "UPDATE transfer_edges SET stale = 1 WHERE label LIKE ?",
                    (pattern,),
                )
            await db.commit()

    async def load_edges(self) -> list[TransferEdge]:
        """Load edges from DB for optimizer (includes stale-flagged edges)."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM transfer_edges ORDER BY edge_cpp DESC"
            ) as cursor:
                rows = await cursor.fetchall()

        edges: list[TransferEdge] = []
        for row in rows:
            edges.append(
                TransferEdge(
                    from_currency=Currency(row["from_currency"]),
                    to_currency=Currency(row["to_currency"]),
                    ratio=row["ratio"],
                    edge_cpp=row["edge_cpp"],
                    label=row["label"],
                    source_name=row["source_name"],
                    source_url=row["source_url"],
                    scraped_at=datetime.fromisoformat(row["scraped_at"]),
                    is_one_way=bool(row["is_one_way"]),
                    stale=bool(row["stale"]),
                    suspicious=bool(row["suspicious"]),
                    notes=row["notes"],
                )
            )
        return edges

    async def save_run(
        self,
        results: list[ScraperResult],
        edges: list[TransferEdge],
        opt_result: OptimizationResult,
        duration_ms: int = 0,
    ) -> None:
        ok = sum(1 for r in results if r.status.value == "SUCCESS")
        cached = sum(1 for r in results if r.status.value == "CACHED")
        failed = sum(1 for r in results if r.status.value == "FAILED")
        alerts = sum(
            1
            for r in results
            if r.scraper_name == "awardwallet_scraper"
            for a in r.data.get("articles", [])
            if a.get("is_alert")
        )
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO scraper_runs (
                    run_at, duration_ms, scrapers_run, scrapers_ok,
                    scrapers_cached, scrapers_fail, devaluation_alerts, summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.utcnow().isoformat(),
                    duration_ms,
                    len(results),
                    ok,
                    cached,
                    failed,
                    alerts,
                    json.dumps(opt_result.model_dump(mode="json"), default=str),
                ),
            )
            await db.commit()

    async def save_devaluation_alert(
        self, program: str, headline: str, url: str
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO devaluation_alerts (detected_at, program, headline, source_url)
                VALUES (?, ?, ?, ?)
                """,
                (datetime.utcnow().isoformat(), program, headline, url),
            )
            await db.commit()
