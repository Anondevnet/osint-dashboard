"""SQLite store for Detroit records. This file is not the command-center database."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from detroit.records import freshness, freshness_facets, isoformat, public_record, utc_now

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "detroit_911.sqlite"

COLUMNS = (
    "source_record_id",
    "incident_id",
    "incident_entry_id",
    "nature",
    "category",
    "nature_code",
    "priority",
    "call_source",
    "call_group",
    "called_at",
    "intersection",
    "latitude",
    "longitude",
    "plottable",
    "location_precision",
    "location_note",
    "precinct",
    "scout_car_area",
    "neighborhood_name",
    "zip_code",
    "council_district",
    "intake_time",
    "dispatch_time",
    "travel_time",
    "on_scene_time",
    "total_response_time",
    "total_time",
    "source_name",
    "data_type",
    "verification",
    "source_url",
    "retrieved_at",
)


def database_path() -> Path:
    override = os.environ.get("DETROIT_DB", "").strip()
    if override:
        return Path(override)
    return DEFAULT_DB


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or database_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS incidents (
            source_record_id TEXT PRIMARY KEY,
            incident_id TEXT,
            incident_entry_id INTEGER,
            nature TEXT,
            category TEXT,
            nature_code TEXT,
            priority TEXT,
            call_source TEXT,
            call_group TEXT,
            called_at TEXT NOT NULL,
            intersection TEXT,
            latitude REAL,
            longitude REAL,
            plottable INTEGER NOT NULL,
            location_precision TEXT NOT NULL,
            location_note TEXT NOT NULL,
            precinct INTEGER,
            scout_car_area TEXT,
            neighborhood_name TEXT,
            zip_code TEXT,
            council_district TEXT,
            intake_time REAL,
            dispatch_time REAL,
            travel_time REAL,
            on_scene_time REAL,
            total_response_time REAL,
            total_time REAL,
            source_name TEXT NOT NULL,
            data_type TEXT NOT NULL,
            verification TEXT NOT NULL,
            source_url TEXT NOT NULL,
            retrieved_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            ok INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            pages INTEGER,
            upserted INTEGER,
            window_start TEXT,
            window_end TEXT,
            source_edited_at TEXT,
            newest_event_at TEXT,
            service_count INTEGER,
            window_count INTEGER
        );
        CREATE INDEX IF NOT EXISTS incidents_called_at ON incidents(called_at);
        CREATE INDEX IF NOT EXISTS incidents_category ON incidents(category);
        CREATE INDEX IF NOT EXISTS incidents_priority ON incidents(priority);
        CREATE INDEX IF NOT EXISTS incidents_precinct ON incidents(precinct);
        CREATE INDEX IF NOT EXISTS incidents_call_source ON incidents(call_source);
        CREATE INDEX IF NOT EXISTS incidents_retrieved ON incidents(retrieved_at);
        """
    )
    return connection


def upsert_many(connection: sqlite3.Connection, records: list[dict[str, Any]]) -> int:
    if not records:
        return 0
    assignments = ", ".join(name + " = excluded." + name for name in COLUMNS if name != "source_record_id")
    sql = (
        "INSERT INTO incidents ("
        + ", ".join(COLUMNS)
        + ") VALUES ("
        + ", ".join("?" for _ in COLUMNS)
        + ") ON CONFLICT(source_record_id) DO UPDATE SET "
        + assignments
    )
    rows = []
    for record in records:
        rows.append(tuple(int(record["plottable"]) if name == "plottable" else record[name] for name in COLUMNS))
    connection.executemany(sql, rows)
    return len(rows)


def start_run(connection: sqlite3.Connection, started_at: datetime, window_start: datetime) -> int:
    cursor = connection.execute(
        "INSERT INTO sync_runs (started_at, ok, window_start, window_end) VALUES (?, 0, ?, ?)",
        (isoformat(started_at), isoformat(window_start), isoformat(started_at)),
    )
    return int(cursor.lastrowid)


def finish_run(connection: sqlite3.Connection, run_id: int, **values: Any) -> None:
    sets = ", ".join(name + " = ?" for name in values)
    connection.execute(
        "UPDATE sync_runs SET " + sets + " WHERE id = ?",
        tuple(values.values()) + (run_id,),
    )


def _filters(arguments: dict[str, Any], now: datetime) -> tuple[str, list[Any]]:
    clauses = ["1 = 1"]
    params: list[Any] = []
    if arguments.get("start"):
        clauses.append("called_at >= ?")
        params.append(arguments["start"])
    if arguments.get("end"):
        clauses.append("called_at <= ?")
        params.append(arguments["end"])
    for column in ("category", "priority", "call_source"):
        if arguments.get(column):
            clauses.append(column + " = ?")
            params.append(arguments[column])
    if arguments.get("precinct") is not None:
        clauses.append("precinct = ?")
        params.append(arguments["precinct"])
    if arguments.get("source"):
        clauses.append("source_name = ?")
        params.append(arguments["source"])
    state = arguments.get("freshness")
    if state == "recently_retrieved":
        clauses.append("retrieved_at >= ?")
        params.append(isoformat(now - timedelta(hours=6)))
    elif state == "stored":
        clauses.append("retrieved_at > ? AND retrieved_at < ?")
        params.append(isoformat(now - timedelta(hours=24)))
        params.append(isoformat(now - timedelta(hours=6)))
    elif state == "stale":
        clauses.append("retrieved_at <= ?")
        params.append(isoformat(now - timedelta(hours=24)))
    return " AND ".join(clauses), params


def query_incidents(
    connection: sqlite3.Connection,
    arguments: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or utc_now()
    where, params = _filters(arguments, moment)
    total = connection.execute("SELECT COUNT(*) FROM incidents WHERE " + where, params).fetchone()[0]
    limit = int(arguments.get("limit") or 500)
    offset = int(arguments.get("offset") or 0)
    rows = connection.execute(
        "SELECT * FROM incidents WHERE " + where + " ORDER BY called_at DESC, source_record_id DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    records = []
    for row in rows:
        item = {key: row[key] for key in row.keys()}
        item["plottable"] = bool(item["plottable"])
        records.append(public_record(item, moment))
    return {"total": total, "limit": limit, "offset": offset, "records": records}


def get_incident(connection: sqlite3.Connection, source_record_id: str, now: datetime | None = None) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM incidents WHERE source_record_id = ?",
        (source_record_id,),
    ).fetchone()
    if row is None:
        return None
    item = {key: row[key] for key in row.keys()}
    item["plottable"] = bool(item["plottable"])
    return public_record(item, now or utc_now())


def facets(connection: sqlite3.Connection) -> dict[str, list[Any]]:
    def distinct(column: str) -> list[Any]:
        found = connection.execute(
            "SELECT DISTINCT "
            + column
            + " FROM incidents WHERE "
            + column
            + " IS NOT NULL ORDER BY "
            + column
            + " LIMIT 300"
        ).fetchall()
        return [item[0] for item in found]

    return {
        "category": distinct("category"),
        "priority": distinct("priority"),
        "precinct": distinct("precinct"),
        "call_source": distinct("call_source"),
        "source": distinct("source_name"),
        "freshness": freshness_facets(),
    }


def list_runs(connection: sqlite3.Connection, limit: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, started_at, finished_at, ok, error, pages, upserted,
               window_start, window_end, source_edited_at, newest_event_at,
               service_count, window_count
        FROM sync_runs
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def latest_successful_run(connection: sqlite3.Connection) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM sync_runs WHERE ok = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def status(connection: sqlite3.Connection) -> dict[str, Any]:
    count = connection.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    newest = connection.execute("SELECT MAX(called_at) FROM incidents").fetchone()[0]
    oldest = connection.execute("SELECT MIN(called_at) FROM incidents").fetchone()[0]
    latest = connection.execute(
        "SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    success = connection.execute(
        "SELECT * FROM sync_runs WHERE ok = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return {
        "local_records": count,
        "oldest_event_at": oldest,
        "newest_event_at": newest,
        "latest_run": dict(latest) if latest else None,
        "last_success": dict(success) if success else None,
    }


def freshness_of(retrieved_at: str, now: datetime | None = None) -> str:
    return freshness(retrieved_at, now or utc_now())
