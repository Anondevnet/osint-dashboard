"""Feed status from source timestamps. A successful HTTP request is not freshness."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

LIVE = "LIVE"
DELAYED = "DELAYED"
HISTORICAL = "HISTORICAL"
UNAVAILABLE = "UNAVAILABLE"
UNVERIFIED = "UNVERIFIED"

STATUSES = (LIVE, DELAYED, HISTORICAL, UNAVAILABLE, UNVERIFIED)
HISTORICAL_AFTER = timedelta(hours=48)
LIVE_EVENT_WITHIN = timedelta(minutes=5)
LIVE_LATENCY_MAX_SECONDS = 120

NO_PER_INCIDENT_PUBLICATION = (
    "This layer has no per-incident publication timestamp. "
    "The dataset dataLastEditDate is not this incident's publication time."
)


def parse_stamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _latency_ok(measured_latency_seconds: Any) -> bool:
    if isinstance(measured_latency_seconds, bool) or not isinstance(measured_latency_seconds, (int, float)):
        return False
    return 0 <= float(measured_latency_seconds) <= LIVE_LATENCY_MAX_SECONDS


def classify_feed(
    *,
    realtime_verified: bool,
    measured_latency_seconds: Any,
    provider_kind: str,
    newest_event_at: str | None,
    source_edited_at: str | None,
    sync_ok: bool,
    now: datetime,
) -> tuple[str, str]:
    """Return a status that stays off LIVE unless an authorized latency sample exists.

    The public Detroit provider sets realtime_verified false and kind
    public_arcgis_poll, so this function cannot return LIVE for it.
    """
    newest = parse_stamp(newest_event_at)
    edited = parse_stamp(source_edited_at)
    authorized = (
        realtime_verified is True
        and provider_kind == "authorized_stream"
        and _latency_ok(measured_latency_seconds)
        and newest is not None
        and edited is not None
        and now >= newest
        and (now - newest) <= LIVE_EVENT_WITHIN
    )
    if authorized:
        return (
            LIVE,
            "An authorized upstream stream reported a measured event latency. "
            "The public Detroit extract does not use this status.",
        )
    if newest is None or edited is None:
        if not sync_ok:
            return (
                UNAVAILABLE,
                "The last check failed and a source timestamp is missing. Stored rows are not live alerts.",
            )
        return (
            UNVERIFIED,
            "A source timestamp is missing, so freshness is unverified. This is not a live feed.",
        )
    if now < newest:
        return (
            UNVERIFIED,
            "The newest event time is later than the dashboard clock, so freshness is unverified.",
        )
    if now - newest > HISTORICAL_AFTER:
        return (
            HISTORICAL,
            "The newest published event is more than 48 hours old. This public extract is not a live alert feed.",
        )
    return (
        DELAYED,
        "The public CAD extract has a recent event, and no authorized real-time stream is connected. "
        "This feed is delayed. It is not live.",
    )


def delay_seconds(start: datetime | None, end: datetime | None) -> int | None:
    """Seconds from start to end. Missing or reversed clocks return None."""
    if start is None or end is None:
        return None
    if end < start:
        return None
    seconds = int((end - start).total_seconds())
    if seconds > 60 * 60 * 24 * 365 * 20:
        return None
    return seconds


def publication_delay(event: datetime | None, published: datetime | None) -> tuple[int | None, str | None]:
    if published is None or event is None:
        return None, NO_PER_INCIDENT_PUBLICATION
    if published < event:
        return None, "The publication time is earlier than the event time, so the delay was not calculated."
    return delay_seconds(event, published), None


def retrieval_delay(event: datetime | None, retrieved: datetime | None) -> tuple[int | None, str | None]:
    if event is None or retrieved is None:
        return None, "A timestamp is missing, so retrieval delay was not calculated."
    if retrieved < event:
        return None, "The retrieval time is earlier than the event time, so the delay was not calculated."
    return delay_seconds(event, retrieved), None


def dataset_lag_seconds(newest_event_at: str | None, source_edited_at: str | None) -> int | None:
    """Dataset edit minus newest event. This is not an average per-incident delay."""
    return delay_seconds(parse_stamp(newest_event_at), parse_stamp(source_edited_at))
