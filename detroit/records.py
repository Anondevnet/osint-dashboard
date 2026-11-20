"""Map one ArcGIS feature into a privacy-limited incident record."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from detroit import DATA_TYPE, PORTAL_URL, SOURCE_NAME, VERIFICATION

# Names confirmed from FeatureServer layer 0 on 2026-11-14.
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
    "called_at",
    "intake_time",
    "dispatch_time",
    "travel_time",
    "on_scene_time",
    "total_response_time",
    "total_time",
    "zip_code",
    "precinct",
    "scout_car_area",
    "neighborhood_name",
    "council_district",
    "longitude",
    "latitude",
)

DURATION_FIELDS = (
    (
        "intake_time",
        "Intake",
        "Minutes for call intake, from the time a call is received until the call ends.",
    ),
    (
        "dispatch_time",
        "Dispatch",
        "Minutes elapsed from the time a call ends until the first unit is dispatched.",
    ),
    (
        "travel_time",
        "Travel",
        "Minutes elapsed from when the first unit is dispatched en route to when the first assigned unit arrived on scene.",
    ),
    (
        "on_scene_time",
        "On scene",
        "Minutes elapsed from when the first unit arrived on scene to call close time.",
    ),
    (
        "total_response_time",
        "Total response",
        "Total time elapsed from time call received until the first unit arrived on scene.",
    ),
    (
        "total_time",
        "Total time",
        "Total time elapsed from the time the call was received to call close time.",
    ),
)

# Published intersection coordinates outside this box are not plotted.
LON_MIN = -83.40
LON_MAX = -82.80
LAT_MIN = 42.20
LAT_MAX = 42.50

RECENT_WITHIN = timedelta(hours=6)
STALE_AFTER = timedelta(hours=24)

_HOUSE_NUMBER = re.compile(r"^\s*\d{1,6}\s+\S")
_RECORD_KEY = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


class RecordError(ValueError):
    """The feature cannot be stored as a public incident."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_called_at(value: Any) -> datetime:
    """ArcGIS date fields on this layer are UTC epoch milliseconds."""
    if isinstance(value, bool) or value is None:
        raise RecordError("Call time is missing.")
    if isinstance(value, str):
        text = value.strip()
        if not text or not re.fullmatch(r"-?\d+(\.\d+)?", text):
            raise RecordError("Call time is not a timestamp.")
        value = float(text)
    if not isinstance(value, (int, float)):
        raise RecordError("Call time is not a timestamp.")
    number = float(value)
    # Seconds would be about 1.7e9 in 2026. Milliseconds are about 1.7e12.
    if abs(number) < 10_000_000_000:
        number *= 1000.0
    try:
        return datetime.fromtimestamp(number / 1000.0, timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise RecordError("Call time is outside the supported range.") from exc


def isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
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


def classify_location(text: Any, latitude: Any, longitude: Any) -> dict[str, Any]:
    """Keep a nearest intersection. Drop a house-number location and its point."""
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
                "The published location was not stored because it was not a "
                "nearest-intersection description."
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
                "No published intersection coordinates were available."
                if raw
                else "The published record has no intersection name or coordinates."
            ),
        }
    return {
        "intersection": raw,
        "latitude": lat,
        "longitude": lon,
        "plottable": True,
        "location_precision": "nearest_intersection" if raw else "coordinates_only",
        "location_note": (
            "Coordinates are the published nearest-intersection point."
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


def parse_feature(feature: Any, retrieved_at: datetime) -> dict[str, Any]:
    if not isinstance(feature, dict):
        raise RecordError("A feature was not an object.")
    attributes = feature.get("attributes")
    if not isinstance(attributes, dict):
        raise RecordError("A feature has no attributes.")
    incident_id = _text(attributes.get("incident_id"), 80)
    entry_raw = attributes.get("incident_entry_id")
    entry_id = None
    if entry_raw is not None and entry_raw != "":
        entry_number = _number(entry_raw)
        if entry_number is None or not entry_number.is_integer():
            raise RecordError("incident_entry_id is not a whole number.")
        entry_id = int(entry_number)
    called_at = parse_called_at(attributes.get("called_at"))
    location = classify_location(
        attributes.get("incident_location"),
        attributes.get("latitude"),
        attributes.get("longitude"),
    )
    precinct = _number(attributes.get("precinct"))
    precinct_value = int(precinct) if precinct is not None and precinct.is_integer() else None
    durations = {}
    for name, _label, _note in DURATION_FIELDS:
        durations[name] = _number(attributes.get(name))
    key = record_key(incident_id, entry_id)
    return {
        "source_record_id": key,
        "incident_id": incident_id,
        "incident_entry_id": entry_id,
        "nature": _text(attributes.get("call_description"), 160),
        "category": _text(attributes.get("category"), 80),
        "nature_code": _text(attributes.get("call_code"), 40),
        "priority": _text(attributes.get("priority"), 8),
        "call_source": _text(attributes.get("call_source"), 40),
        "call_group": _text(attributes.get("call_group"), 40),
        "called_at": isoformat(called_at),
        "intersection": location["intersection"],
        "latitude": location["latitude"],
        "longitude": location["longitude"],
        "plottable": location["plottable"],
        "location_precision": location["location_precision"],
        "location_note": location["location_note"],
        "precinct": precinct_value,
        "scout_car_area": _text(attributes.get("scout_car_area"), 40),
        "neighborhood_name": _text(attributes.get("neighborhood_name"), 80),
        "zip_code": _text(attributes.get("zip_code"), 10),
        "council_district": _text(attributes.get("council_district"), 8),
        "intake_time": durations["intake_time"],
        "dispatch_time": durations["dispatch_time"],
        "travel_time": durations["travel_time"],
        "on_scene_time": durations["on_scene_time"],
        "total_response_time": durations["total_response_time"],
        "total_time": durations["total_time"],
        "source_name": SOURCE_NAME,
        "data_type": DATA_TYPE,
        "verification": VERIFICATION,
        "source_url": PORTAL_URL,
        "retrieved_at": isoformat(retrieved_at),
    }


def freshness(retrieved_at: str, now: datetime) -> str:
    retrieved = datetime.strptime(retrieved_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    age = now - retrieved
    if age >= STALE_AFTER:
        return "stale"
    if age <= RECENT_WITHIN:
        return "recently_retrieved"
    return "stored"


FRESHNESS_LABELS = {
    "recently_retrieved": "Stored copy retrieved within the last 6 hours. The call itself is not live.",
    "stored": "Stored copy retrieved within the last 24 hours. The call itself is not live.",
    "stale": "Stale stored copy. It was retrieved more than 24 hours ago. The call itself is not live.",
}


def freshness_facets() -> list[dict[str, str]]:
    return [
        {"value": "recently_retrieved", "label": FRESHNESS_LABELS["recently_retrieved"]},
        {"value": "stored", "label": FRESHNESS_LABELS["stored"]},
        {"value": "stale", "label": FRESHNESS_LABELS["stale"]},
    ]


def map_points(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Points the map may draw. House-number and missing coordinates stay off the map."""
    points = []
    for record in records:
        if not record.get("plottable"):
            continue
        latitude = record.get("latitude")
        longitude = record.get("longitude")
        if latitude is None or longitude is None:
            continue
        if not (LAT_MIN <= float(latitude) <= LAT_MAX and LON_MIN <= float(longitude) <= LON_MAX):
            continue
        points.append(
            {
                "source_record_id": record.get("source_record_id"),
                "latitude": latitude,
                "longitude": longitude,
                "category": record.get("category"),
                "priority": record.get("priority"),
            }
        )
    return points


def public_record(row: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    moment = now or utc_now()
    state = freshness(row["retrieved_at"], moment)
    durations = []
    for name, label, note in DURATION_FIELDS:
        value = row.get(name)
        if value is None:
            continue
        durations.append(
            {
                "field": name,
                "label": label,
                "minutes": value,
                "note": note + " This is a published elapsed time, not a live response.",
            }
        )
    timeline = [
        {
            "label": "Call received",
            "at": row["called_at"],
            "kind": "call_received",
            "detail": "Timestamp generated in the CAD system when the call was received.",
        },
        {
            "label": "Record retrieved by this dashboard",
            "at": row["retrieved_at"],
            "kind": "retrieved",
            "detail": "When this dashboard read the published row. It is not the time of the call.",
        },
    ]
    payload = dict(row)
    payload["freshness"] = state
    payload["freshness_label"] = FRESHNESS_LABELS[state]
    payload["timeline"] = timeline
    payload["durations"] = durations
    payload["happening_now"] = False
    payload["outcome_known"] = False
    return payload
