"""Map one ArcGIS feature into a public ShotSpotter CAD record."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from shotspotter import DATA_TYPE, LAYER_URL, PORTAL_URL, PROVIDER_ID, SOURCE_NAME, VERIFICATION
from shotspotter.freshness import (
    DELAYED,
    HISTORICAL,
    UNVERIFIED,
    delay_seconds,
    parse_stamp,
    publication_delay,
    retrieval_delay,
)

FIELD_NAMES = (
    "incident_entry_id",
    "incident_id",
    "incident_location",
    "call_source",
    "call_description",
    "category",
    "priority",
    "call_group",
    "call_code",
    "agency",
    "called_at",
    "intake_time",
    "dispatch_time",
    "travel_time",
    "on_scene_time",
    "total_response_time",
    "total_time",
    "scout_car_area",
    "zip_code",
    "neighborhood",
    "council_district",
    "police_precinct",
    "longitude",
    "latitude",
)

CLASS_CODES = {
    "SST345": ("single", "Single gunshot"),
    "SST346": ("multiple", "Multiple gunshots"),
    "SST342": ("probable", "Probable gunfire"),
}

LON_MIN = -83.40
LON_MAX = -82.80
LAT_MIN = 42.20
LAT_MAX = 42.50

_HOUSE_NUMBER = re.compile(r"^\s*\d{1,6}\s+\S")
_RECORD_KEY = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_FIXTURE_PREFIX = "fixture-"


class RecordError(ValueError):
    """The feature cannot be stored as a public incident."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_called_at(value: Any) -> datetime:
    """ArcGIS date fields on this layer are UTC epoch milliseconds."""
    if isinstance(value, bool) or value is None:
        raise RecordError("Event time is missing.")
    if isinstance(value, str):
        text = value.strip()
        if not text or not re.fullmatch(r"-?\d+(\.\d+)?", text):
            raise RecordError("Event time is not a timestamp.")
        value = float(text)
    if not isinstance(value, (int, float)):
        raise RecordError("Event time is not a timestamp.")
    number = float(value)
    if abs(number) < 10_000_000_000:
        number *= 1000.0
    try:
        return datetime.fromtimestamp(number / 1000.0, timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise RecordError("Event time is outside the supported range.") from exc


def _number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _text(value: Any, limit: int = 200) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    return text[:limit]


def classify_code(value: Any) -> tuple[str, str]:
    text = (_text(value, 40) or "").upper()
    if not text:
        return "unclassified", "Unclassified"
    found = CLASS_CODES.get(text)
    if found:
        return found
    return "other", "Other published code"


def classify_location(text: Any, latitude: Any, longitude: Any) -> dict[str, Any]:
    """Keep a published nearest-intersection point inside the Detroit box.

    Coordinates are never invented. A house-number location is withheld.
    """
    raw = _text(text, 180)
    lat = _number(latitude)
    lon = _number(longitude)
    coords_ok = (
        lat is not None
        and lon is not None
        and LAT_MIN <= lat <= LAT_MAX
        and LON_MIN <= lon <= LON_MAX
    )
    if raw and _HOUSE_NUMBER.search(raw) and "&" not in raw:
        return {
            "intersection": None,
            "latitude": None,
            "longitude": None,
            "plottable": False,
            "location_precision": "withheld",
            "location_note": (
                "The published location was withheld because it was not a nearest-intersection description."
            ),
        }
    if not coords_ok:
        return {
            "intersection": raw,
            "latitude": None,
            "longitude": None,
            "plottable": False,
            "location_precision": "missing_coordinates" if raw else "missing",
            "location_note": (
                "No reliable coordinates were published inside the Detroit map box."
                if raw
                else "The published record has no intersection name or reliable coordinates."
            ),
        }
    return {
        "intersection": raw,
        "latitude": lat,
        "longitude": lon,
        "plottable": True,
        "location_precision": "nearest_intersection" if raw else "coordinates_only",
        "location_note": (
            "Coordinates are the published nearest-intersection point, not a sensor origin."
            if raw
            else "The intersection name was blank. The point is the published coordinate."
        ),
    }


def record_key(incident_id: str | None, entry_id: int | None) -> str:
    if incident_id and _RECORD_KEY.fullmatch(incident_id):
        return incident_id
    if entry_id is not None:
        key = "entry-%s" % entry_id
        if _RECORD_KEY.fullmatch(key):
            return key
    raise RecordError("The record has no usable source id.")


def is_fixture_id(source_record_id: str) -> bool:
    return source_record_id.startswith(_FIXTURE_PREFIX)


def _whole(value: Any) -> int | None:
    number = _number(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def parse_feature(feature: Any, retrieved_at: datetime, dataset_edited_at: str | None) -> dict[str, Any]:
    if not isinstance(feature, dict):
        raise RecordError("A feature was not an object.")
    attributes = feature.get("attributes")
    if not isinstance(attributes, dict):
        raise RecordError("A feature has no attributes.")
    incident_id = _text(attributes.get("incident_id"), 80)
    entry_id = _whole(attributes.get("incident_entry_id"))
    if attributes.get("incident_entry_id") not in (None, "") and entry_id is None:
        raise RecordError("incident_entry_id is not a whole number.")
    called_at = parse_called_at(attributes.get("called_at"))
    location = classify_location(
        attributes.get("incident_location"),
        attributes.get("latitude"),
        attributes.get("longitude"),
    )
    classification, classification_label = classify_code(attributes.get("call_code"))
    key = record_key(incident_id, entry_id)
    published_delay, published_reason = publication_delay(called_at, None)
    retrieved_delay, retrieved_reason = retrieval_delay(called_at, retrieved_at)
    durations = {
        name: _number(attributes.get(name))
        for name in (
            "intake_time",
            "dispatch_time",
            "travel_time",
            "on_scene_time",
            "total_response_time",
            "total_time",
        )
    }
    try:
        raw = json.dumps(attributes, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise RecordError("The source attributes could not be stored.") from exc
    if len(raw) > 20000:
        raise RecordError("The source attributes are too large to store.")
    return {
        "source_record_id": key,
        "incident_id": incident_id,
        "incident_entry_id": entry_id,
        "called_at": isoformat(called_at),
        "classification": classification,
        "classification_label": classification_label,
        "call_code": _text(attributes.get("call_code"), 40),
        "call_description": _text(attributes.get("call_description"), 160),
        "category": _text(attributes.get("category"), 80),
        "priority": _text(attributes.get("priority"), 8),
        "call_source": _text(attributes.get("call_source"), 40),
        "call_group": _text(attributes.get("call_group"), 40),
        "agency": _text(attributes.get("agency"), 40),
        "intersection": location["intersection"],
        "latitude": location["latitude"],
        "longitude": location["longitude"],
        "plottable": location["plottable"],
        "location_precision": location["location_precision"],
        "location_note": location["location_note"],
        "neighborhood": _text(attributes.get("neighborhood"), 80),
        "zip_code": _text(attributes.get("zip_code"), 10),
        "council_district": _text(attributes.get("council_district"), 8),
        "police_precinct": _whole(attributes.get("police_precinct")),
        "scout_car_area": _text(attributes.get("scout_car_area"), 40),
        "intake_time": durations["intake_time"],
        "dispatch_time": durations["dispatch_time"],
        "travel_time": durations["travel_time"],
        "on_scene_time": durations["on_scene_time"],
        "total_response_time": durations["total_response_time"],
        "total_time": durations["total_time"],
        "provider_id": PROVIDER_ID,
        "source_name": SOURCE_NAME,
        "data_type": DATA_TYPE,
        "verification": VERIFICATION,
        "source_url": PORTAL_URL,
        "layer_url": LAYER_URL,
        "dataset_edited_at": dataset_edited_at,
        "retrieved_at": isoformat(retrieved_at),
        "first_retrieved_at": isoformat(retrieved_at),
        "publication_delay_seconds": published_delay,
        "publication_delay_reason": published_reason,
        "retrieval_delay_seconds": retrieved_delay,
        "retrieval_delay_reason": retrieved_reason,
        "raw_attributes": raw,
        "is_fixture": is_fixture_id(key),
    }


def record_freshness(called_at: str, now: datetime) -> str:
    event = parse_stamp(called_at)
    if event is None or now < event:
        return UNVERIFIED
    if now - event > timedelta(hours=48):
        return HISTORICAL
    return DELAYED


def public_record(row: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    moment = now or utc_now()
    event = parse_stamp(row.get("called_at"))
    first = parse_stamp(row.get("first_retrieved_at")) or parse_stamp(row.get("retrieved_at"))
    state = record_freshness(row.get("called_at") or "", moment)
    historical_when_stored = bool(event and first and first - event > timedelta(hours=6))
    newly_stored = bool(first and moment - first <= timedelta(hours=6))
    payload = {key: value for key, value in row.items() if key not in {"raw_attributes", "is_fixture"}}
    payload["plottable"] = bool(row.get("plottable"))
    payload["freshness"] = state
    payload["happening_now"] = False
    payload["live_alert"] = False
    payload["event_age_seconds"] = delay_seconds(event, moment)
    payload["historical_when_stored"] = historical_when_stored
    payload["newly_retrieved_historical"] = historical_when_stored and newly_stored
    payload["source_publication_at"] = None
    payload["audio_published"] = False
    if payload["newly_retrieved_historical"]:
        payload["verification_note"] = (
            "This row was first stored by this dashboard recently. "
            "The event time is older, so it is not a new alert."
        )
    elif historical_when_stored:
        payload["verification_note"] = "Historical published CAD record. It is not a new alert."
    else:
        payload["verification_note"] = "Published CAD record from the public extract. It is not a live alert."
    return payload


def map_points(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points = []
    for record in records:
        if not record.get("plottable"):
            continue
        latitude = record.get("latitude")
        longitude = record.get("longitude")
        if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
            continue
        if isinstance(latitude, bool) or isinstance(longitude, bool):
            continue
        if not (LAT_MIN <= float(latitude) <= LAT_MAX and LON_MIN <= float(longitude) <= LON_MAX):
            continue
        points.append(
            {
                "source_record_id": record.get("source_record_id"),
                "latitude": latitude,
                "longitude": longitude,
                "classification": record.get("classification"),
            }
        )
    return points
