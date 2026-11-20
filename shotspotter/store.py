"""SQLite store for ShotSpotter CAD records. Separate from the Detroit 911 database."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from shotspotter.freshness import dataset_lag_seconds, parse_stamp
from shotspotter.providers import PROVIDERS
from shotspotter.records import isoformat, map_points, public_record, utc_now

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "shotspotter.sqlite"
DETROIT_TZ = ZoneInfo("America/Detroit")

COLUMNS = (
    "source_record_id",
    "incident_id",
    "incident_entry_id",
    "called_at",
    "classification",
    "classification_label",
    "call_code",
    "call_description",
    "category",
    "priority",
    "call_source",
    "call_group",
    "agency",
    "intersection",
    "latitude",
    "longitude",
    "plottable",
    "location_precision",
    "location_note",
    "neighborhood",
    "zip_code",
    "council_district",
    "police_precinct",
    "scout_car_area",
    "intake_time",
    "dispatch_time",
    "travel_time",
    "on_scene_time",
    "total_response_time",
    "total_time",
    "provider_id",
    "source_name",
    "data_type",
    "verification",
    "source_url",
    "layer_url",
    "dataset_edited_at",
    "retrieved_at",
    "first_retrieved_at",
    "publication_delay_seconds",
    "publication_delay_reason",
    "retrieval_delay_seconds",
    "retrieval_delay_reason",
    "raw_attributes",
)


class StoreError(RuntimeError):
    """A write was refused."""


def database_path() -> Path:
    override = os.environ.get("SHOTSPOTTER_DB", "").strip()
    if override:
        return Path(override)
    return DEFAULT_DB


def _connection_path(connection: sqlite3.Connection) -> Path | None:
    row = connection.execute("PRAGMA database_list").fetchone()
    if row is None or not row[2]:
        return None
    return Path(row[2]).resolve()


def fixtures_allowed(connection: sqlite3.Connection) -> bool:
    """Test rows may enter only an explicit non-production database."""
    if os.environ.get("SHOTSPOTTER_ALLOW_FIXTURES") != "1":
        return False
    path = _connection_path(connection)
    if path is None:
        return False
    return path != DEFAULT_DB.resolve()


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or database_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS shotspotter_incidents (
            source_record_id TEXT PRIMARY KEY,
            incident_id TEXT,
            incident_entry_id INTEGER,
            called_at TEXT NOT NULL,
            classification TEXT NOT NULL,
            classification_label TEXT NOT NULL,
            call_code TEXT,
            call_description TEXT,
            category TEXT,
            priority TEXT,
            call_source TEXT,
            call_group TEXT,
            agency TEXT,
            intersection TEXT,
            latitude REAL,
            longitude REAL,
            plottable INTEGER NOT NULL,
            location_precision TEXT NOT NULL,
            location_note TEXT NOT NULL,
            neighborhood TEXT,
            zip_code TEXT,
            council_district TEXT,
            police_precinct INTEGER,
            scout_car_area TEXT,
            intake_time REAL,
            dispatch_time REAL,
            travel_time REAL,
            on_scene_time REAL,
            total_response_time REAL,
            total_time REAL,
            provider_id TEXT NOT NULL,
            source_name TEXT NOT NULL,
            data_type TEXT NOT NULL,
            verification TEXT NOT NULL,
            source_url TEXT NOT NULL,
            layer_url TEXT NOT NULL,
            dataset_edited_at TEXT,
            retrieved_at TEXT NOT NULL,
            first_retrieved_at TEXT NOT NULL,
            publication_delay_seconds INTEGER,
            publication_delay_reason TEXT,
            retrieval_delay_seconds INTEGER,
            retrieval_delay_reason TEXT,
            raw_attributes TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS shotspotter_sync_history (
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
            window_count INTEGER,
            unchanged INTEGER
        );
        CREATE TABLE IF NOT EXISTS shotspotter_source_status (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            provider_id TEXT NOT NULL,
            feed_status TEXT NOT NULL,
            feed_detail TEXT NOT NULL,
            source_edited_at TEXT,
            newest_event_at TEXT,
            oldest_event_at TEXT,
            service_count INTEGER,
            stored_count INTEGER,
            last_success_at TEXT,
            last_attempt_at TEXT,
            last_error TEXT,
            realtime_verified INTEGER NOT NULL DEFAULT 0,
            measured_latency_seconds REAL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS shotspotter_provider_metadata (
            provider_id TEXT PRIMARY KEY,
            city TEXT NOT NULL,
            title TEXT NOT NULL,
            enabled INTEGER NOT NULL,
            status_ceiling TEXT NOT NULL,
            kind TEXT NOT NULL,
            source_url TEXT NOT NULL,
            api_url TEXT,
            access TEXT NOT NULL,
            credentials_required INTEGER NOT NULL,
            record_kind TEXT NOT NULL,
            publication TEXT NOT NULL,
            realtime_verified INTEGER NOT NULL,
            operational TEXT NOT NULL,
            free_api INTEGER NOT NULL,
            individual_timestamps INTEGER NOT NULL,
            notes TEXT NOT NULL,
            checked_on TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS shotspotter_called_at ON shotspotter_incidents(called_at);
        CREATE INDEX IF NOT EXISTS shotspotter_classification ON shotspotter_incidents(classification);
        CREATE INDEX IF NOT EXISTS shotspotter_neighborhood ON shotspotter_incidents(neighborhood);
        """
    )
    _seed_providers(connection)
    return connection


def _seed_providers(connection: sqlite3.Connection, replace: bool = False) -> None:
    conflict = (
        """
        ON CONFLICT(provider_id) DO UPDATE SET
            city = excluded.city,
            title = excluded.title,
            enabled = excluded.enabled,
            status_ceiling = excluded.status_ceiling,
            kind = excluded.kind,
            source_url = excluded.source_url,
            api_url = excluded.api_url,
            access = excluded.access,
            credentials_required = excluded.credentials_required,
            record_kind = excluded.record_kind,
            publication = excluded.publication,
            realtime_verified = excluded.realtime_verified,
            operational = excluded.operational,
            free_api = excluded.free_api,
            individual_timestamps = excluded.individual_timestamps,
            notes = excluded.notes,
            checked_on = excluded.checked_on
        """
        if replace
        else "ON CONFLICT(provider_id) DO NOTHING"
    )
    sql = """
        INSERT INTO shotspotter_provider_metadata (
            provider_id, city, title, enabled, status_ceiling, kind, source_url, api_url,
            access, credentials_required, record_kind, publication, realtime_verified,
            operational, free_api, individual_timestamps, notes, checked_on
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """ + conflict
    rows = [
        (
            item["provider_id"],
            item["city"],
            item["title"],
            int(item["enabled"]),
            item["status_ceiling"],
            item["kind"],
            item["source_url"],
            item["api_url"],
            item["access"],
            int(item["credentials_required"]),
            item["record_kind"],
            item["publication"],
            int(item["realtime_verified"]),
            item["operational"],
            int(item["free_api"]),
            int(item["individual_timestamps"]),
            item["notes"],
            item["checked_on"],
        )
        for item in PROVIDERS
    ]
    connection.executemany(sql, rows)


def refresh_provider_catalog(connection: sqlite3.Connection) -> None:
    """Rewrite catalog notes during a sync. Ordinary reads do not write them."""
    _seed_providers(connection, replace=True)


def upsert_many(connection: sqlite3.Connection, records: list[dict[str, Any]]) -> int:
    if not records:
        return 0
    if any(record.get("is_fixture") or str(record.get("source_record_id", "")).startswith("fixture-") for record in records):
        if not fixtures_allowed(connection):
            raise StoreError("Refusing to store a test fixture outside an isolated test database.")
    ids = [record["source_record_id"] for record in records]
    existing: dict[str, sqlite3.Row] = {}
    for start in range(0, len(ids), 400):
        chunk = ids[start:start + 400]
        placeholders = ", ".join("?" for _ in chunk)
        found = connection.execute(
            "SELECT source_record_id, first_retrieved_at, retrieved_at, raw_attributes "
            "FROM shotspotter_incidents WHERE source_record_id IN (" + placeholders + ")",
            chunk,
        ).fetchall()
        for row in found:
            existing[row["source_record_id"]] = row
    prepared = []
    for record in records:
        item = dict(record)
        previous = existing.get(item["source_record_id"])
        if previous is not None:
            item["first_retrieved_at"] = previous["first_retrieved_at"]
            if previous["raw_attributes"] == item["raw_attributes"]:
                item["retrieved_at"] = previous["retrieved_at"]
        prepared.append(item)
    assignments = ", ".join(name + " = excluded." + name for name in COLUMNS if name != "source_record_id")
    sql = (
        "INSERT INTO shotspotter_incidents ("
        + ", ".join(COLUMNS)
        + ") VALUES ("
        + ", ".join("?" for _ in COLUMNS)
        + ") ON CONFLICT(source_record_id) DO UPDATE SET "
        + assignments
    )
    rows = []
    for record in prepared:
        rows.append(tuple(int(record["plottable"]) if name == "plottable" else record.get(name) for name in COLUMNS))
    connection.executemany(sql, rows)
    return len(rows)


def start_run(connection: sqlite3.Connection, started_at: datetime, window_start: datetime) -> int:
    cursor = connection.execute(
        """
        INSERT INTO shotspotter_sync_history (started_at, ok, window_start, window_end)
        VALUES (?, 0, ?, ?)
        """,
        (isoformat(started_at), isoformat(window_start), isoformat(started_at)),
    )
    return int(cursor.lastrowid)


def finish_run(connection: sqlite3.Connection, run_id: int, **values: Any) -> None:
    sets = ", ".join(name + " = ?" for name in values)
    connection.execute(
        "UPDATE shotspotter_sync_history SET " + sets + " WHERE id = ?",
        tuple(values.values()) + (run_id,),
    )


def latest_successful_run(connection: sqlite3.Connection) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM shotspotter_sync_history WHERE ok = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def list_runs(connection: sqlite3.Connection, limit: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, started_at, finished_at, ok, error, pages, upserted,
               window_start, window_end, source_edited_at, newest_event_at,
               service_count, window_count, unchanged
        FROM shotspotter_sync_history
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def write_source_status(connection: sqlite3.Connection, values: dict[str, Any]) -> None:
    columns = (
        "provider_id",
        "feed_status",
        "feed_detail",
        "source_edited_at",
        "newest_event_at",
        "oldest_event_at",
        "service_count",
        "stored_count",
        "last_success_at",
        "last_attempt_at",
        "last_error",
        "realtime_verified",
        "measured_latency_seconds",
        "updated_at",
    )
    sql = (
        "INSERT INTO shotspotter_source_status (id, "
        + ", ".join(columns)
        + ") VALUES (1, "
        + ", ".join("?" for _ in columns)
        + ") ON CONFLICT(id) DO UPDATE SET "
        + ", ".join(name + " = excluded." + name for name in columns)
    )
    connection.execute(sql, tuple(values[name] for name in columns))


def source_status_row(connection: sqlite3.Connection) -> dict[str, Any] | None:
    row = connection.execute("SELECT * FROM shotspotter_source_status WHERE id = 1").fetchone()
    if row is None:
        return None
    return dict(row)


def incident_bounds(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT COUNT(*) AS stored_count,
               MIN(called_at) AS oldest_event_at,
               MAX(called_at) AS newest_event_at
        FROM shotspotter_incidents
        """
    ).fetchone()
    return {
        "stored_count": int(row["stored_count"]),
        "oldest_event_at": row["oldest_event_at"],
        "newest_event_at": row["newest_event_at"],
    }


def _filters(arguments: dict[str, Any], now: datetime) -> tuple[str, list[Any]]:
    clauses = ["1 = 1"]
    params: list[Any] = []
    if arguments.get("start"):
        clauses.append("called_at >= ?")
        params.append(arguments["start"])
    if arguments.get("end"):
        clauses.append("called_at <= ?")
        params.append(arguments["end"])
    if arguments.get("classification"):
        clauses.append("classification = ?")
        params.append(arguments["classification"])
    if arguments.get("neighborhood"):
        clauses.append("neighborhood = ?")
        params.append(arguments["neighborhood"])
    if arguments.get("zip_code"):
        clauses.append("zip_code = ?")
        params.append(arguments["zip_code"])
    if arguments.get("precinct") is not None:
        clauses.append("police_precinct = ?")
        params.append(arguments["precinct"])
    if arguments.get("q"):
        escaped = arguments["q"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = "%" + escaped + "%"
        clauses.append(
            "(incident_id LIKE ? ESCAPE '\\' OR neighborhood LIKE ? ESCAPE '\\' "
            "OR zip_code LIKE ? ESCAPE '\\' OR call_code LIKE ? ESCAPE '\\' "
            "OR classification_label LIKE ? ESCAPE '\\')"
        )
        params.extend([like, like, like, like, like])
    freshness = arguments.get("freshness")
    historic_before = isoformat(now - timedelta(hours=48))
    if freshness == "HISTORICAL":
        clauses.append("called_at < ?")
        params.append(historic_before)
    elif freshness == "DELAYED":
        clauses.append("called_at >= ?")
        params.append(historic_before)
    elif freshness == "UNVERIFIED":
        clauses.append("1 = 0")
    box = arguments.get("bbox")
    if box:
        clauses.append("plottable = 1 AND latitude >= ? AND latitude <= ? AND longitude >= ? AND longitude <= ?")
        params.extend([box["south"], box["north"], box["west"], box["east"]])
    return " AND ".join(clauses), params


def _rows(connection: sqlite3.Connection, where: str, params: list[Any], limit: int, offset: int, now: datetime) -> list[dict[str, Any]]:
    found = connection.execute(
        "SELECT * FROM shotspotter_incidents WHERE "
        + where
        + " ORDER BY called_at DESC, source_record_id DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    records = []
    for row in found:
        item = {key: row[key] for key in row.keys()}
        records.append(public_record(item, now))
    return records


def query_incidents(connection: sqlite3.Connection, arguments: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    moment = now or utc_now()
    where, params = _filters(arguments, moment)
    total = connection.execute("SELECT COUNT(*) FROM shotspotter_incidents WHERE " + where, params).fetchone()[0]
    limit = int(arguments.get("limit") or 50)
    offset = int(arguments.get("offset") or 0)
    records = _rows(connection, where, params, limit, offset, moment)
    return {"total": total, "limit": limit, "offset": offset, "records": records}


def query_map(connection: sqlite3.Connection, arguments: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    moment = now or utc_now()
    where, params = _filters(arguments, moment)
    matched = connection.execute("SELECT COUNT(*) FROM shotspotter_incidents WHERE " + where, params).fetchone()[0]
    unplotted = connection.execute(
        "SELECT COUNT(*) FROM shotspotter_incidents WHERE " + where + " AND plottable = 0",
        params,
    ).fetchone()[0]
    records = _rows(connection, where + " AND plottable = 1", params, 2000, 0, moment)
    points = map_points(records)
    return {
        "matched": matched,
        "unplotted": unplotted,
        "plotted": len(points),
        "truncated": matched - unplotted > 2000,
        "points": points,
        "records": records,
    }


def get_incident(connection: sqlite3.Connection, source_record_id: str, now: datetime | None = None) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM shotspotter_incidents WHERE source_record_id = ?",
        (source_record_id,),
    ).fetchone()
    if row is None:
        return None
    return public_record({key: row[key] for key in row.keys()}, now or utc_now())


def facets(connection: sqlite3.Connection) -> dict[str, list[Any]]:
    def distinct(column: str) -> list[Any]:
        found = connection.execute(
            "SELECT DISTINCT " + column + " FROM shotspotter_incidents WHERE "
            + column + " IS NOT NULL ORDER BY " + column + " LIMIT 300"
        ).fetchall()
        return [item[0] for item in found]

    return {
        "classification": ["single", "multiple", "probable", "unclassified", "other"],
        "neighborhood": distinct("neighborhood"),
        "zip_code": distinct("zip_code"),
        "precinct": distinct("police_precinct"),
    }


def statistics(connection: sqlite3.Connection, now: datetime | None = None) -> dict[str, Any]:
    moment = now or utc_now()
    local = moment.astimezone(DETROIT_TZ)
    today_start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone_utc())
    tomorrow = today_start + timedelta(days=1)
    day_ago = moment - timedelta(hours=24)
    week_ago = moment - timedelta(days=7)
    counts = connection.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN called_at >= ? AND called_at < ? THEN 1 ELSE 0 END) AS today_count,
            SUM(CASE WHEN called_at >= ? THEN 1 ELSE 0 END) AS last_24_hours,
            SUM(CASE WHEN called_at >= ? THEN 1 ELSE 0 END) AS last_7_days,
            SUM(CASE WHEN classification = 'single' THEN 1 ELSE 0 END) AS single_count,
            SUM(CASE WHEN classification = 'multiple' THEN 1 ELSE 0 END) AS multiple_count,
            SUM(CASE WHEN classification = 'probable' THEN 1 ELSE 0 END) AS probable_count,
            SUM(CASE WHEN classification = 'unclassified' THEN 1 ELSE 0 END) AS unclassified_count,
            SUM(CASE WHEN classification = 'other' THEN 1 ELSE 0 END) AS other_count,
            AVG(publication_delay_seconds) AS average_publication_delay_seconds,
            SUM(CASE WHEN publication_delay_seconds IS NOT NULL THEN 1 ELSE 0 END) AS publication_delay_samples
        FROM shotspotter_incidents
        """,
        (
            isoformat(today_start),
            isoformat(tomorrow),
            isoformat(day_ago),
            isoformat(week_ago),
        ),
    ).fetchone()
    samples = int(counts["publication_delay_samples"] or 0)
    average = counts["average_publication_delay_seconds"]
    return {
        "count_basis": (
            "Each number counts published CAD incident records stored from the public extract. "
            "It is not a count of dispatched units, gunshots, or people."
        ),
        "stored_incidents": int(counts["total"] or 0),
        "incidents_today": int(counts["today_count"] or 0),
        "incidents_last_24_hours": int(counts["last_24_hours"] or 0),
        "incidents_last_7_days": int(counts["last_7_days"] or 0),
        "single_shot": int(counts["single_count"] or 0),
        "multiple_shot": int(counts["multiple_count"] or 0),
        "probable_gunfire": int(counts["probable_count"] or 0),
        "unclassified": int(counts["unclassified_count"] or 0),
        "other_classification": int(counts["other_count"] or 0),
        "average_publication_delay_seconds": None if samples == 0 or average is None else round(float(average), 1),
        "publication_delay_samples": samples,
        "publication_delay_note": (
            "No per-incident publication timestamp is available, so the average was not calculated."
            if samples == 0
            else "Average of stored per-incident publication delays, in seconds."
        ),
        "today_timezone": "America/Detroit",
        "generated_at": isoformat(moment),
    }


def timezone_utc():
    from datetime import timezone
    return timezone.utc


def provider_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM shotspotter_provider_metadata ORDER BY enabled DESC, provider_id"
    ).fetchall()
    return [dict(row) for row in rows]


def dataset_lag(connection: sqlite3.Connection) -> int | None:
    status = source_status_row(connection)
    if not status:
        return None
    return dataset_lag_seconds(status.get("newest_event_at"), status.get("source_edited_at"))


def stamp_age(value: str | None, now: datetime) -> int | None:
    parsed = parse_stamp(value)
    if parsed is None or now < parsed:
        return None
    return int((now - parsed).total_seconds())
