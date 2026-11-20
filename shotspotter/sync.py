"""Incremental sync of the recent Detroit ShotSpotter CAD window.

An unchanged dataLastEditDate does not download rows. The full archive is not paged.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from shotspotter import LAYER_URL, PORTAL_URL, PROVIDER_ID
from shotspotter.client import ShotspotterClient, ShotspotterSourceError, validate_metadata, window_where
from shotspotter.freshness import classify_feed
from shotspotter.providers import enabled_provider
from shotspotter.records import RecordError, isoformat, parse_feature, utc_now
from shotspotter.store import (
    connect,
    finish_run,
    incident_bounds,
    latest_successful_run,
    refresh_provider_catalog,
    start_run,
    upsert_many,
    write_source_status,
)

DEFAULT_LOOKBACK_HOURS = 24 * 14
MAX_LOOKBACK_HOURS = 24 * 14


def main() -> None:
    import argparse
    import json
    import os
    import sys

    parser = argparse.ArgumentParser(
        description="Store a recent window of Detroit ShotSpotter CAD records."
    )
    parser.add_argument("--lookback-hours", type=float, default=None)
    parser.add_argument("--db", default=None)
    args = parser.parse_args()
    from shotspotter.scheduler import lookback_hours

    hours = (
        args.lookback_hours
        if args.lookback_hours is not None
        else lookback_hours(os.environ.get("SHOTSPOTTER_LOOKBACK_HOURS"))
    )
    try:
        result = sync_recent(db_path=Path(args.db) if args.db else None, lookback_hours=hours)
    except ShotspotterSourceError as exc:
        print(exc.message, file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(result, indent=2))


def edited_at(layer: dict[str, Any]) -> str | None:
    editing = layer.get("editingInfo") or {}
    millis = editing.get("dataLastEditDate")
    if isinstance(millis, bool) or not isinstance(millis, (int, float)):
        return None
    try:
        moment = datetime.fromtimestamp(float(millis) / 1000.0, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return isoformat(moment)


def _remember_status(
    connection: Any,
    *,
    provider: dict[str, Any],
    source_edited_at: str | None,
    service_count: int | None,
    last_success_at: str | None,
    last_attempt_at: str,
    last_error: str | None,
    sync_ok: bool,
    now: datetime,
) -> tuple[str, str]:
    bounds = incident_bounds(connection)
    status, detail = classify_feed(
        realtime_verified=bool(provider.get("realtime_verified")),
        measured_latency_seconds=None,
        provider_kind=str(provider.get("kind") or ""),
        newest_event_at=bounds["newest_event_at"],
        source_edited_at=source_edited_at,
        sync_ok=sync_ok,
        now=now,
    )
    write_source_status(
        connection,
        {
            "provider_id": provider["provider_id"],
            "feed_status": status,
            "feed_detail": detail,
            "source_edited_at": source_edited_at,
            "newest_event_at": bounds["newest_event_at"],
            "oldest_event_at": bounds["oldest_event_at"],
            "service_count": service_count,
            "stored_count": bounds["stored_count"],
            "last_success_at": last_success_at,
            "last_attempt_at": last_attempt_at,
            "last_error": last_error,
            "realtime_verified": 0,
            "measured_latency_seconds": None,
            "updated_at": isoformat(now),
        },
    )
    return status, detail


def sync_recent(
    db_path: Path | None = None,
    lookback_hours: float = DEFAULT_LOOKBACK_HOURS,
    client: ShotspotterClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Upsert CAD rows inside the lookback window. Does not page the full archive."""
    if lookback_hours <= 0 or lookback_hours > MAX_LOOKBACK_HOURS:
        raise ShotspotterSourceError(
            "bad_window",
            "The lookback window must be greater than 0 and no more than 14 days.",
        )
    provider = enabled_provider()
    if provider["provider_id"] != PROVIDER_ID or provider["kind"] != "public_arcgis_poll":
        raise ShotspotterSourceError("provider", "The enabled provider is not the public Detroit extract.")
    moment = now or utc_now()
    window_start = moment - timedelta(hours=lookback_hours)
    source = client or ShotspotterClient(query_url=provider["query_url"], layer_url=provider["api_url"])
    connection = connect(db_path)
    try:
        refresh_provider_catalog(connection)
        run_id = start_run(connection, moment, window_start)
        connection.commit()
        try:
            layer = source.fetch_metadata()
            page_size = validate_metadata(layer)
            edited = edited_at(layer)
            previous = latest_successful_run(connection)
            if edited and previous and previous.get("source_edited_at") == edited:
                finished = isoformat(utc_now())
                finish_run(
                    connection,
                    run_id,
                    finished_at=finished,
                    ok=1,
                    error=None,
                    pages=0,
                    upserted=0,
                    source_edited_at=edited,
                    newest_event_at=previous.get("newest_event_at"),
                    service_count=previous.get("service_count"),
                    window_count=previous.get("window_count"),
                    unchanged=1,
                )
                status, detail = _remember_status(
                    connection,
                    provider=provider,
                    source_edited_at=edited,
                    service_count=previous.get("service_count"),
                    last_success_at=finished,
                    last_attempt_at=finished,
                    last_error=None,
                    sync_ok=True,
                    now=utc_now(),
                )
                connection.commit()
                bounds = incident_bounds(connection)
                return {
                    "ok": True,
                    "unchanged": True,
                    "source_url": PORTAL_URL,
                    "layer_url": LAYER_URL,
                    "pages": 0,
                    "upserted": 0,
                    "rejected": 0,
                    "window_start": isoformat(window_start),
                    "window_end": isoformat(moment),
                    "window_count": previous.get("window_count"),
                    "service_count": previous.get("service_count"),
                    "stored_count": bounds["stored_count"],
                    "source_edited_at": edited,
                    "newest_event_at": bounds["newest_event_at"],
                    "feed_status": status,
                    "feed_detail": detail,
                    "realtime_access": False,
                    "lookback_hours": lookback_hours,
                }
            where = window_where(window_start)
            window_count = source.count(where)
            records: list[dict[str, Any]] = []
            rejected = 0
            pages = 0
            seen: set[str] = set()
            for page, feature in source.iter_features(where, page_size):
                pages = page
                try:
                    record = parse_feature(feature, moment, edited)
                except RecordError:
                    rejected += 1
                    continue
                if record["source_record_id"] in seen:
                    continue
                seen.add(record["source_record_id"])
                records.append(record)
            upserted = upsert_many(connection, records)
            service_count = None
            try:
                service_count = source.count("1=1")
            except ShotspotterSourceError:
                service_count = None
            finished = isoformat(utc_now())
            bounds = incident_bounds(connection)
            finish_run(
                connection,
                run_id,
                finished_at=finished,
                ok=1,
                error=None,
                pages=pages,
                upserted=upserted,
                source_edited_at=edited,
                newest_event_at=bounds["newest_event_at"],
                service_count=service_count,
                window_count=window_count,
                unchanged=0,
            )
            status, detail = _remember_status(
                connection,
                provider=provider,
                source_edited_at=edited,
                service_count=service_count,
                last_success_at=finished,
                last_attempt_at=finished,
                last_error=None,
                sync_ok=True,
                now=utc_now(),
            )
            connection.commit()
            return {
                "ok": True,
                "unchanged": False,
                "source_url": PORTAL_URL,
                "layer_url": LAYER_URL,
                "pages": pages,
                "upserted": upserted,
                "rejected": rejected,
                "window_start": isoformat(window_start),
                "window_end": isoformat(moment),
                "window_count": window_count,
                "service_count": service_count,
                "stored_count": bounds["stored_count"],
                "source_edited_at": edited,
                "newest_event_at": bounds["newest_event_at"],
                "feed_status": status,
                "feed_detail": detail,
                "realtime_access": False,
                "lookback_hours": lookback_hours,
                "has_attachments": layer.get("hasAttachments"),
            }
        except ShotspotterSourceError as exc:
            finished = isoformat(utc_now())
            finish_run(
                connection,
                run_id,
                finished_at=finished,
                ok=0,
                error=exc.message,
                unchanged=0,
            )
            previous = latest_successful_run(connection)
            _remember_status(
                connection,
                provider=provider,
                source_edited_at=previous.get("source_edited_at") if previous else None,
                service_count=previous.get("service_count") if previous else None,
                last_success_at=previous.get("finished_at") if previous else None,
                last_attempt_at=finished,
                last_error=exc.message,
                sync_ok=False,
                now=utc_now(),
            )
            connection.commit()
            raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
