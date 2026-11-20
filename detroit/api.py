"""Local JSON API for the stored Detroit records."""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from detroit import DATA_TYPE, LAYER_ID, LAYER_NAME, LAYER_URL, PORTAL_URL, SOURCE_NAME, VERIFICATION
from detroit.records import freshness_facets, map_points, utc_now
from detroit.scheduler import (
    interval_seconds,
    lookback_hours,
    reconcile_hours,
    reconcile_interval_seconds,
)
from detroit.store import (
    connect,
    database_path,
    facets,
    get_incident,
    list_runs,
    query_incidents,
    status,
)

IMPORT_NOTE = (
    "Each check reads a recent time window from the public service and upserts those rows. "
    "It does not download the full archive. "
    "The check interval is not the city's publication schedule, and a stored row is not a live incident."
)
PUBLICATION_NOTE = (
    "The city publication timestamp is the service dataLastEditDate. "
    "The dashboard check interval is separate. "
    "Polling does not make this a live dispatch feed."
)
WINDOW_NOTE = (
    "When the city timestamp is unchanged, no rows are downloaded. "
    "When it changes, the recent window is read again. "
    "A wider reconciliation runs only if that timestamp changed and the last wide pass is old enough. "
    "Stored rows outside the window stay. The full archive is not downloaded."
)

NOTICE = (
    "The City of Detroit does not guarantee an update schedule for this dataset. "
    "A point on the map is a published call-for-service record, not a call that is happening now, "
    "and not a unit that is currently responding."
)
COVERAGE = (
    "The published dataset contains police-serviced 911 calls, emergency responses from the "
    "non-emergency telephone reporting line, and ShotSpotter incidents since 20 September 2016. "
    "It does not include every emergency call, and it does not include officer-initiated activity."
)

_KEY = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?Z$")
_FRESH = {"recently_retrieved", "stored", "stale"}


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _stamp(value: str, label: str) -> str:
    text = value.strip()
    if not _STAMP.fullmatch(text):
        raise ApiError(400, label + " must be a UTC time such as 2026-11-14T00:00:00Z.")
    if len(text) == 17:
        text = text[:-1] + ":00Z"
    datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return text


def _arguments(query: dict[str, str]) -> dict[str, Any]:
    arguments: dict[str, Any] = {}
    if query.get("from"):
        arguments["start"] = _stamp(query["from"], "From")
    if query.get("to"):
        arguments["end"] = _stamp(query["to"], "To")
    for key, limit in (("category", 80), ("priority", 8), ("call_source", 40)):
        value = query.get(key, "").strip()
        if value:
            if len(value) > limit:
                raise ApiError(400, "That filter is too long.")
            arguments[key] = value
    precinct = query.get("precinct", "").strip()
    if precinct:
        if not re.fullmatch(r"\d{1,2}", precinct):
            raise ApiError(400, "Precinct must be a whole number.")
        arguments["precinct"] = int(precinct)
    source = query.get("source", "").strip()
    if source:
        if len(source) > 120:
            raise ApiError(400, "That filter is too long.")
        arguments["source"] = source
    freshness = query.get("freshness", "").strip()
    if freshness:
        if freshness not in _FRESH:
            raise ApiError(400, "That freshness filter is not supported.")
        arguments["freshness"] = freshness
    limit = query.get("limit", "").strip() or "500"
    offset = query.get("offset", "").strip() or "0"
    if not limit.isdigit() or not offset.isdigit():
        raise ApiError(400, "Limit and offset must be whole numbers.")
    limit_number = int(limit)
    offset_number = int(offset)
    if not 1 <= limit_number <= 2000:
        raise ApiError(400, "Limit must be between 1 and 2000.")
    if offset_number > 100000:
        raise ApiError(400, "That page offset is too large.")
    arguments["limit"] = limit_number
    arguments["offset"] = offset_number
    return arguments


def _parse_stamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def describe_sync(latest: dict | None, success: dict | None, now: datetime, interval: int) -> dict[str, Any]:
    """Separate a dashboard check from the city's publication timestamp."""
    last_checked = None
    if latest:
        last_checked = latest.get("finished_at") or latest.get("started_at")
    last_success_at = success.get("finished_at") if success else None
    source_edited = success.get("source_edited_at") if success else None
    in_progress = bool(latest and not latest.get("finished_at") and not latest.get("error"))
    failed = bool(latest and latest.get("finished_at") and not latest.get("ok"))
    if failed:
        state = "error"
        detail = (
            "The last synchronization check failed. "
            "Stored rows are published records, not live incidents."
        )
    elif not success and in_progress:
        state = "checking"
        detail = "A synchronization check is in progress. Nothing stored yet is a live incident."
    elif not success:
        state = "never"
        detail = "No successful synchronization has been stored yet. This page does not show live incidents."
    else:
        finished = _parse_stamp(last_success_at)
        age = now - finished if finished is not None else timedelta.max
        if interval == 0:
            window = timedelta(hours=6)
        else:
            window = timedelta(seconds=max(int(interval), 1) * 2)
        if age <= window and interval != 0:
            state = "current"
            detail = (
                "The dashboard's last check finished on schedule. "
                "That does not mean the city published new calls. "
                "These rows are not live incidents."
            )
        elif age <= timedelta(hours=6):
            state = "delayed" if interval else "current"
            if interval == 0:
                detail = (
                    "Automatic checks are off. "
                    "Stored rows are published records, not live incidents."
                )
            else:
                detail = (
                    "The last successful check is later than the configured interval. "
                    "Stored rows are published records, not live incidents."
                )
        else:
            state = "stale"
            detail = (
                "The last successful check is more than 6 hours old. "
                "Stored rows are published records, not live incidents."
            )
        if in_progress:
            detail = "A synchronization check is in progress. " + detail
    return {
        "sync_interval_seconds": interval,
        "last_checked_at": last_checked,
        "last_success_at": last_success_at,
        "source_edited_at": source_edited,
        "sync_state": state,
        "sync_state_detail": detail,
        "publication_note": PUBLICATION_NOTE,
    }


def _run_limit(query: dict[str, str]) -> int:
    raw = query.get("limit", "").strip() or "40"
    if not raw.isdigit():
        raise ApiError(400, "Limit must be a whole number.")
    value = int(raw)
    if not 1 <= value <= 200:
        raise ApiError(400, "Limit must be between 1 and 200.")
    return value


def _attach_sync(body: dict[str, Any]) -> dict[str, Any]:
    body["import_note"] = IMPORT_NOTE
    body["window_note"] = WINDOW_NOTE
    wide = reconcile_hours(os.environ.get("DETROIT_RECONCILE_HOURS"))
    body["lookback_hours"] = lookback_hours(os.environ.get("DETROIT_LOOKBACK_HOURS"))
    body["reconcile_hours"] = wide
    body["reconcile_interval_seconds"] = reconcile_interval_seconds(
        os.environ.get("DETROIT_RECONCILE_INTERVAL_SECONDS")
    )
    body.update(
        describe_sync(
            body.get("latest_run"),
            body.get("last_success"),
            utc_now(),
            interval_seconds(os.environ.get("DETROIT_SYNC_INTERVAL_SECONDS")),
        )
    )
    return body


def _envelope() -> dict[str, Any]:
    return {
        "source_name": SOURCE_NAME,
        "data_type": DATA_TYPE,
        "verification": VERIFICATION,
        "source_url": PORTAL_URL,
        "service_url": LAYER_URL,
        "layer_name": LAYER_NAME,
        "layer_id": LAYER_ID,
        "notice": NOTICE,
        "coverage": COVERAGE,
        "live_dispatch": False,
        "update_schedule_guaranteed": False,
    }


def _empty_status() -> dict[str, Any]:
    body = _envelope()
    body.update({
        "database_present": False,
        "local_records": 0,
        "oldest_event_at": None,
        "newest_event_at": None,
        "latest_run": None,
        "last_success": None,
        "message": "No Detroit records have been stored yet. Run python3 -m detroit.sync.",
    })
    return _attach_sync(body)


def handle(path: str, query: dict[str, str]) -> tuple[int, dict[str, Any]]:
    target = database_path()
    if not target.is_file():
        if path == "/api/detroit/status":
            return 200, _empty_status()
        if path == "/api/detroit/facets":
            body = _envelope()
            body.update({
                "category": [],
                "priority": [],
                "precinct": [],
                "call_source": [],
                "source": [],
                "freshness": freshness_facets(),
                "empty": True,
                "message": "No Detroit records have been stored yet. Run python3 -m detroit.sync.",
            })
            return 200, body
        if path == "/api/detroit/incidents":
            arguments = _arguments(query)
            body = _envelope()
            body.update({
                "records": [],
                "points": [],
                "total": 0,
                "limit": arguments["limit"],
                "offset": arguments["offset"],
                "empty": True,
                "message": "No Detroit records have been stored yet. Run python3 -m detroit.sync.",
            })
            return 200, body
        if path == "/api/detroit/runs":
            body = _envelope()
            body["runs"] = []
            body["empty"] = True
            return 200, body
        if path.startswith("/api/detroit/incidents/"):
            raise ApiError(404, "That incident is not in the stored window.")
        raise ApiError(404, "That Detroit route is not available.")
    try:
        connection = connect(target)
    except sqlite3.Error as exc:
        raise ApiError(500, "The local Detroit database could not be opened.") from exc
    try:
        if path == "/api/detroit/status":
            body = _envelope()
            body.update(status(connection))
            body["database_present"] = True
            return 200, _attach_sync(body)
        if path == "/api/detroit/facets":
            body = _envelope()
            body.update(facets(connection))
            return 200, body
        if path == "/api/detroit/runs":
            body = _envelope()
            body["runs"] = list_runs(connection, _run_limit(query))
            return 200, body
        if path == "/api/detroit/incidents":
            found = query_incidents(connection, _arguments(query))
            found["points"] = map_points(found["records"])
            body = _envelope()
            body.update(found)
            return 200, body
        prefix = "/api/detroit/incidents/"
        if path.startswith(prefix):
            key = path[len(prefix):]
            if not _KEY.fullmatch(key):
                raise ApiError(404, "That incident is not available.")
            record = get_incident(connection, key)
            if record is None:
                raise ApiError(404, "That incident is not in the stored window.")
            body = _envelope()
            body["record"] = record
            return 200, body
        raise ApiError(404, "That Detroit route is not available.")
    finally:
        connection.close()
