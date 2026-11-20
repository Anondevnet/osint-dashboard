"""Local JSON API for stored Detroit ShotSpotter CAD records."""

from __future__ import annotations

import csv
import io
import os
import re
import sqlite3
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from shotspotter import (
    DATA_TYPE,
    ITEM_ID,
    LAYER_ID,
    LAYER_NAME,
    LAYER_URL,
    PORTAL_URL,
    SOURCE_NAME,
    VERIFICATION,
)
from shotspotter.freshness import NO_PER_INCIDENT_PUBLICATION, dataset_lag_seconds
from shotspotter.providers import catalog, public_provider
from shotspotter.scheduler import interval_seconds, lookback_hours
from shotspotter.store import (
    connect,
    database_path,
    dataset_lag,
    facets,
    get_incident,
    list_runs,
    provider_rows,
    query_incidents,
    query_map,
    source_status_row,
    stamp_age,
    statistics,
)

NOTICE = (
    "These are published Detroit Police CAD records initiated by the gunshot detection system. "
    "The open-data extract is not a live ShotSpotter alert feed. "
    "A point is a nearest-intersection location from a published record, not a gunshot happening now."
)
COVERAGE = (
    "The official dataset is DPD ShotSpotter Incident Records. "
    "It contains CAD records from 15 March 2023, when ShotSpotter was integrated into CAD, onward. "
    "Earlier ShotSpotter-related CAD remains in Police Serviced 911 Calls. "
    "The city says coverage is about 39 square miles and locations are the nearest intersection. "
    "No acoustic audio is published with these records."
)
IMPORT_NOTE = (
    "Each check reads the dataset publication timestamp. "
    "Rows are downloaded only when that timestamp changes, and only for the recent window, at most 14 days. "
    "The full archive is not downloaded. A successful check is not evidence that the feed is live."
)
COUNT_NOTE = (
    "Counts on this page are published CAD incident records. "
    "They are not counts of dispatched units, shots, or people."
)

_KEY = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?Z$")
_QUERY_TEXT = re.compile(r"^[A-Za-z0-9 .,'&/#_-]{1,80}$")
_CLASSIFICATIONS = {"single", "multiple", "probable", "unclassified", "other"}
_FRESH = {"DELAYED", "HISTORICAL", "UNVERIFIED"}
_HITS: deque[float] = deque()

CSV_FIELDS = (
    "incident_id",
    "called_at",
    "classification",
    "intersection",
    "classification_label",
    "call_code",
    "neighborhood",
    "zip_code",
    "police_precinct",
    "council_district",
    "latitude",
    "longitude",
    "location_precision",
    "location_note",
    "source_name",
    "source_url",
    "dataset_edited_at",
    "retrieved_at",
    "freshness",
    "publication_delay_seconds",
    "publication_delay_reason",
    "retrieval_delay_seconds",
    "verification_note",
)


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def clear_rate_limit() -> None:
    _HITS.clear()


def enforce_rate_limit(now: float | None = None) -> None:
    raw = os.environ.get("SHOTSPOTTER_API_RATE_PER_MINUTE", "240").strip()
    if raw == "0":
        return
    if not raw.isdigit():
        limit = 240
    else:
        limit = int(raw)
    if limit <= 0:
        return
    moment = time.monotonic() if now is None else now
    while _HITS and moment - _HITS[0] > 60:
        _HITS.popleft()
    if len(_HITS) >= limit:
        raise ApiError(429, "Too many ShotSpotter requests. Wait a minute and try again.")
    _HITS.append(moment)


def _stamp(value: str, label: str) -> str:
    text = value.strip()
    if not _STAMP.fullmatch(text):
        raise ApiError(400, label + " must be a UTC time such as 2026-11-14T00:00:00Z.")
    if len(text) == 17:
        text = text[:-1] + ":00Z"
    datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return text


def _float_box(query: dict[str, str]) -> dict[str, float] | None:
    keys = ("west", "south", "east", "north")
    present = [key for key in keys if query.get(key, "").strip()]
    if not present:
        return None
    if len(present) != 4:
        raise ApiError(400, "A map box needs west, south, east, and north.")
    values = {}
    for key in keys:
        text = query[key].strip()
        try:
            number = float(text)
        except ValueError as exc:
            raise ApiError(400, "Map box values must be numbers.") from exc
        if number != number or number in (float("inf"), float("-inf")):
            raise ApiError(400, "Map box values must be numbers.")
        values[key] = number
    if not -180 <= values["west"] < values["east"] <= 180:
        raise ApiError(400, "That map box is not valid.")
    if not -90 <= values["south"] < values["north"] <= 90:
        raise ApiError(400, "That map box is not valid.")
    return values


def _arguments(query: dict[str, str], *, csv_export: bool) -> dict[str, Any]:
    arguments: dict[str, Any] = {}
    if query.get("from"):
        arguments["start"] = _stamp(query["from"], "From")
    if query.get("to"):
        arguments["end"] = _stamp(query["to"], "To")
    classification = query.get("classification", "").strip()
    if classification:
        if classification not in _CLASSIFICATIONS:
            raise ApiError(400, "That classification filter is not supported.")
        arguments["classification"] = classification
    neighborhood = query.get("neighborhood", "").strip()
    if neighborhood:
        if not _QUERY_TEXT.fullmatch(neighborhood):
            raise ApiError(400, "That neighborhood filter is not valid.")
        arguments["neighborhood"] = neighborhood
    zip_code = query.get("zip_code", "").strip()
    if zip_code:
        if not re.fullmatch(r"\d{5}", zip_code):
            raise ApiError(400, "ZIP code must be five digits.")
        arguments["zip_code"] = zip_code
    precinct = query.get("precinct", "").strip()
    if precinct:
        if not re.fullmatch(r"\d{1,2}", precinct):
            raise ApiError(400, "Precinct must be a whole number.")
        arguments["precinct"] = int(precinct)
    text = query.get("q", "").strip()
    if text:
        if not _QUERY_TEXT.fullmatch(text):
            raise ApiError(400, "That search is not valid.")
        arguments["q"] = text
    freshness = query.get("freshness", "").strip()
    if freshness:
        if freshness not in _FRESH:
            raise ApiError(400, "That freshness filter is not supported.")
        arguments["freshness"] = freshness
    arguments["bbox"] = _float_box(query)
    default_limit = "2000" if csv_export else "50"
    max_limit = 2000 if csv_export else 200
    limit = query.get("limit", "").strip() or default_limit
    offset = query.get("offset", "").strip() or "0"
    if not limit.isdigit() or not offset.isdigit():
        raise ApiError(400, "Limit and offset must be whole numbers.")
    limit_number = int(limit)
    offset_number = int(offset)
    if not 1 <= limit_number <= max_limit:
        raise ApiError(400, "Limit must be between 1 and %s." % max_limit)
    if offset_number > 100000:
        raise ApiError(400, "That page offset is too large.")
    arguments["limit"] = limit_number
    arguments["offset"] = offset_number
    return arguments


def _envelope() -> dict[str, Any]:
    return {
        "source_name": SOURCE_NAME,
        "data_type": DATA_TYPE,
        "verification": VERIFICATION,
        "source_url": PORTAL_URL,
        "service_url": LAYER_URL,
        "dataset_item_id": ITEM_ID,
        "layer_name": LAYER_NAME,
        "layer_id": LAYER_ID,
        "notice": NOTICE,
        "coverage": COVERAGE,
        "import_note": IMPORT_NOTE,
        "count_note": COUNT_NOTE,
        "publication_delay_note": NO_PER_INCIDENT_PUBLICATION,
        "live_dispatch": False,
        "realtime_access": False,
        "audio_published": False,
        "credentials_required": False,
        "update_schedule_guaranteed": False,
    }


def _sync_block(status_row: dict[str, Any] | None, latest: dict | None, success: dict | None) -> dict[str, Any]:
    interval = interval_seconds(os.environ.get("SHOTSPOTTER_SYNC_INTERVAL_SECONDS"))
    feed_status = (status_row or {}).get("feed_status") or "UNVERIFIED"
    if feed_status == "LIVE":
        feed_status = "UNVERIFIED"
    return {
        "sync_interval_seconds": interval,
        "lookback_hours": lookback_hours(os.environ.get("SHOTSPOTTER_LOOKBACK_HOURS")),
        "feed_status": feed_status,
        "feed_detail": (status_row or {}).get("feed_detail")
        or "No ShotSpotter records have been stored yet. Freshness is unverified.",
        "live": False,
        "realtime_verified": False,
        "measured_latency_seconds": None,
        "last_success_at": (status_row or {}).get("last_success_at") or (success or {}).get("finished_at"),
        "last_attempt_at": (status_row or {}).get("last_attempt_at") or (latest or {}).get("finished_at"),
        "source_edited_at": (status_row or {}).get("source_edited_at") or (success or {}).get("source_edited_at"),
        "newest_event_at": (status_row or {}).get("newest_event_at"),
        "oldest_event_at": (status_row or {}).get("oldest_event_at"),
        "service_count": (status_row or {}).get("service_count"),
        "stored_count": (status_row or {}).get("stored_count") or 0,
        "last_error": (status_row or {}).get("last_error"),
        "monitoring": (
            "Automatic checks are off."
            if interval == 0
            else "This dashboard checks the public layer every %s seconds. That is not a live alert stream."
            % interval
        ),
        "dataset_lag_seconds": dataset_lag_seconds(
            (status_row or {}).get("newest_event_at"),
            (status_row or {}).get("source_edited_at"),
        ),
        "dataset_lag_note": (
            "Seconds from the newest stored event to the dataset publication timestamp. "
            "This is not an average per-incident publication delay."
        ),
    }


def _empty_status() -> dict[str, Any]:
    body = _envelope()
    body.update(_sync_block(None, None, None))
    body.update({
        "database_present": False,
        "local_records": 0,
        "latest_run": None,
        "last_success": None,
        "facets": {"classification": [], "neighborhood": [], "zip_code": [], "precinct": []},
        "message": "No ShotSpotter records have been stored yet. Run python3 -m shotspotter.sync.",
    })
    return body


def _load(path: str):
    target = database_path()
    if not target.is_file():
        return None
    try:
        return connect(target)
    except sqlite3.Error as exc:
        raise ApiError(500, "The local ShotSpotter database could not be opened.") from exc


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if text[:1] in "=+-@":
        return "'" + text
    return text


def export_csv(query: dict[str, str]) -> tuple[int, str]:
    enforce_rate_limit()
    arguments = _arguments(query, csv_export=True)
    connection = _load("/api/shotspotter-detroit/incidents")
    if connection is None:
        arguments_records = []
    else:
        try:
            arguments_records = query_incidents(connection, arguments)["records"]
        finally:
            connection.close()
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for record in arguments_records:
        writer.writerow({name: _csv_cell(record.get(name)) for name in CSV_FIELDS})
    return 200, buffer.getvalue()


def handle(path: str, query: dict[str, str]) -> tuple[int, dict[str, Any]]:
    enforce_rate_limit()
    if path == "/api/shotspotter-detroit/health":
        return 200, _health()
    if path == "/api/shotspotter-detroit/sources":
        return 200, _sources()
    if path == "/api/shotspotter-detroit/incidents":
        return 200, _incidents(query)
    prefix = "/api/shotspotter-detroit/incidents/"
    if path.startswith(prefix):
        return 200, _incident(path[len(prefix):])
    if path == "/api/shotspotter-detroit/statistics":
        return 200, _statistics()
    if path == "/api/shotspotter-detroit/map":
        return 200, _map(query)
    if path == "/api/shotspotter-detroit/sync-history":
        return 200, _history(query)
    if path == "/api/shotspotter-detroit/freshness":
        return 200, _freshness()
    if path == "/api/shotspotter-detroit/status":
        return 200, _status()
    raise ApiError(404, "That ShotSpotter route is not available.")


def _health() -> dict[str, Any]:
    body = _envelope()
    target = database_path()
    body["database_present"] = target.is_file()
    body["feed_status"] = "UNVERIFIED"
    body["live"] = False
    if not target.is_file():
        body["status"] = "ok"
        body["module"] = "shotspotter-detroit"
        return body
    connection = _load("")
    try:
        row = source_status_row(connection) if connection else None
    finally:
        if connection:
            connection.close()
    block = _sync_block(row, None, None)
    body.update(block)
    body["status"] = "ok" if block["feed_status"] != "UNAVAILABLE" else "degraded"
    body["module"] = "shotspotter-detroit"
    body["event_age_seconds"] = stamp_age(block.get("newest_event_at"), datetime.now(timezone.utc))
    return body


def _sources() -> dict[str, Any]:
    body = _envelope()
    connection = _load("")
    rows = catalog()
    feed_status = "UNVERIFIED"
    if connection is not None:
        try:
            rows = provider_rows(connection)
            status = source_status_row(connection)
            if status and status.get("feed_status") and status["feed_status"] != "LIVE":
                feed_status = status["feed_status"]
        finally:
            connection.close()
    published = []
    for item in rows:
        if item["provider_id"] == "detroit":
            published.append(public_provider(item, feed_status))
        else:
            published.append(public_provider(item))
    body["providers"] = published
    body["enabled_provider_id"] = "detroit"
    body["live_provider"] = None
    body["message"] = "Secondary cities are documented and disabled. None is labeled live."
    return body


def _with_connection(builder):
    connection = _load("")
    if connection is None:
        return None
    try:
        return builder(connection)
    finally:
        connection.close()


def _status() -> dict[str, Any]:
    body = _empty_status()
    if not database_path().is_file():
        return body

    def build(connection):
        latest = connection.execute(
            "SELECT * FROM shotspotter_sync_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
        success = connection.execute(
            "SELECT * FROM shotspotter_sync_history WHERE ok = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        status_row = source_status_row(connection)
        payload = _envelope()
        payload.update(_sync_block(status_row, dict(latest) if latest else None, dict(success) if success else None))
        payload["database_present"] = True
        payload["local_records"] = payload["stored_count"]
        payload["latest_run"] = dict(latest) if latest else None
        payload["last_success"] = dict(success) if success else None
        payload["facets"] = facets(connection)
        payload["dataset_lag_seconds"] = dataset_lag(connection)
        return payload

    return _with_connection(build) or body


def _incidents(query: dict[str, str]) -> dict[str, Any]:
    arguments = _arguments(query, csv_export=False)
    body = _envelope()
    if not database_path().is_file():
        body.update({"records": [], "total": 0, "limit": arguments["limit"], "offset": arguments["offset"], "empty": True})
        return body
    found = _with_connection(lambda connection: query_incidents(connection, arguments))
    body.update(found)
    return body


def _incident(key: str) -> dict[str, Any]:
    if not _KEY.fullmatch(key):
        raise ApiError(404, "That incident is not available.")
    if not database_path().is_file():
        raise ApiError(404, "That incident is not in the stored window.")
    record = _with_connection(lambda connection: get_incident(connection, key))
    if record is None:
        raise ApiError(404, "That incident is not in the stored window.")
    body = _envelope()
    body["record"] = record
    return body


def _statistics() -> dict[str, Any]:
    body = _envelope()
    if not database_path().is_file():
        body.update({
            "stored_incidents": 0,
            "incidents_today": 0,
            "incidents_last_24_hours": 0,
            "incidents_last_7_days": 0,
            "single_shot": 0,
            "multiple_shot": 0,
            "probable_gunfire": 0,
            "unclassified": 0,
            "other_classification": 0,
            "average_publication_delay_seconds": None,
            "publication_delay_samples": 0,
            "service_count": None,
            "empty": True,
        })
        return body

    def build(connection):
        payload = statistics(connection)
        status = source_status_row(connection)
        payload["service_count"] = None if status is None else status.get("service_count")
        payload["service_count_note"] = (
            "service_count is the city layer's published total from the last successful check. "
            "stored_incidents is the recent window kept locally. They are different numbers."
        )
        return payload

    body.update(_with_connection(build))
    return body


def _map(query: dict[str, str]) -> dict[str, Any]:
    arguments = _arguments(query, csv_export=False)
    body = _envelope()
    body["map_note"] = (
        "Only records with reliable coordinates inside the Detroit box are plotted. "
        "Other stored records stay in the incident list."
    )
    if not database_path().is_file():
        body.update({"points": [], "records": [], "matched": 0, "unplotted": 0, "plotted": 0, "truncated": False})
        return body
    body.update(_with_connection(lambda connection: query_map(connection, arguments)))
    return body


def _history(query: dict[str, str]) -> dict[str, Any]:
    raw = query.get("limit", "").strip() or "40"
    if not raw.isdigit():
        raise ApiError(400, "Limit must be a whole number.")
    limit = int(raw)
    if not 1 <= limit <= 200:
        raise ApiError(400, "Limit must be between 1 and 200.")
    body = _envelope()
    if not database_path().is_file():
        body["runs"] = []
        body["empty"] = True
        return body
    body["runs"] = _with_connection(lambda connection: list_runs(connection, limit))
    return body


def _freshness() -> dict[str, Any]:
    body = _status()
    body["publication_delay_available"] = False
    body["retrieval_delay_basis"] = "retrieval_timestamp minus event_timestamp, per stored row, when both are comparable."
    body["event_age_seconds"] = stamp_age(body.get("newest_event_at"), datetime.now(timezone.utc))
    body["source_update_frequency"] = (
        "Not guaranteed. The dashboard polls dataLastEditDate. "
        "The city has not published a fixed schedule for this extract."
    )
    return body
