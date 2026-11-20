"""Incremental sync of the recent Detroit calls-for-service window."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from detroit import LAYER_URL, PORTAL_URL
from detroit.client import DetroitClient, DetroitSourceError, validate_metadata, window_where
from detroit.records import RecordError, isoformat, parse_feature, utc_now
from detroit.store import connect, finish_run, latest_successful_run, start_run, upsert_many

DEFAULT_LOOKBACK_HOURS = 48
MAX_LOOKBACK_HOURS = 24 * 14


def main() -> None:
    import argparse
    import json
    import os
    import sys

    parser = argparse.ArgumentParser(
        description="Store a recent window of Detroit police-serviced 911 calls."
    )
    parser.add_argument("--lookback-hours", type=float, default=None)
    parser.add_argument("--db", default=None)
    args = parser.parse_args()
    from detroit.scheduler import lookback_hours, reconcile_hours, reconcile_interval_seconds

    hours = (
        args.lookback_hours
        if args.lookback_hours is not None
        else lookback_hours(os.environ.get("DETROIT_LOOKBACK_HOURS"))
    )
    try:
        result = sync_recent(
            db_path=Path(args.db) if args.db else None,
            lookback_hours=hours,
            reconcile_hours=reconcile_hours(os.environ.get("DETROIT_RECONCILE_HOURS")),
            reconcile_interval_seconds=reconcile_interval_seconds(
                os.environ.get("DETROIT_RECONCILE_INTERVAL_SECONDS")
            ),
        )
    except DetroitSourceError as exc:
        print(exc.message, file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(result, indent=2))


def _edited_at(layer: dict[str, Any]) -> str | None:
    editing = layer.get("editingInfo") or {}
    millis = editing.get("dataLastEditDate")
    if not isinstance(millis, (int, float)):
        return None
    try:
        moment = datetime.fromtimestamp(float(millis) / 1000.0, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return isoformat(moment)


def _parse_run_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _span_hours(run: Any) -> float | None:
    start = _parse_run_time(run["window_start"] if hasattr(run, "keys") else None)
    end = _parse_run_time(run["window_end"] if hasattr(run, "keys") else None)
    if start is None or end is None or end < start:
        return None
    return (end - start).total_seconds() / 3600.0


def reconciliation_due(
    connection: Any,
    moment: datetime,
    wide_hours: float,
    interval_seconds: int,
) -> bool:
    """A wider pass is due when no successful run has covered that width recently.

    An unchanged city timestamp never reaches this function. The scan stops at
    the newest wide success, so a long history is not loaded into memory.
    """
    cursor = connection.execute(
        """
        SELECT window_start, window_end, finished_at
        FROM sync_runs
        WHERE ok = 1 AND window_start IS NOT NULL AND window_end IS NOT NULL
        ORDER BY id DESC
        """
    )
    for row in cursor:
        span = _span_hours(row)
        if span is None or span + 0.05 < wide_hours:
            continue
        finished = _parse_run_time(row["finished_at"])
        if finished is None or interval_seconds <= 0:
            return True
        return moment - finished >= timedelta(seconds=interval_seconds)
    return True


def sync_recent(
    db_path: Path | None = None,
    lookback_hours: float = DEFAULT_LOOKBACK_HOURS,
    client: DetroitClient | None = None,
    now: datetime | None = None,
    reconcile_hours: float | None = None,
    reconcile_interval_seconds: int | None = None,
) -> dict[str, Any]:
    """Upsert calls received inside the lookback window. Does not page the full archive.

    reconcile_hours widens that window only after the city publication timestamp
    changes and the last wide success is older than reconcile_interval_seconds.
    Rows outside the window are left in place. Nothing is deleted.
    """
    if lookback_hours <= 0 or lookback_hours > MAX_LOOKBACK_HOURS:
        raise DetroitSourceError(
            "bad_window",
            "The lookback window must be greater than 0 and no more than 14 days.",
        )
    wide = None
    if reconcile_hours is not None and reconcile_hours > 0:
        wide = min(float(reconcile_hours), float(MAX_LOOKBACK_HOURS))
        if wide <= lookback_hours:
            wide = None
    moment = now or utc_now()
    window_start = moment - timedelta(hours=lookback_hours)
    source = client or DetroitClient()
    connection = connect(db_path)
    try:
        run_id = start_run(connection, moment, window_start)
        connection.commit()
        try:
            layer = source.fetch_metadata()
            page_size = validate_metadata(layer)
            edited = _edited_at(layer)
            previous = latest_successful_run(connection)
            if edited and previous and previous.get("source_edited_at") == edited:
                finish_run(
                    connection,
                    run_id,
                    finished_at=isoformat(utc_now()),
                    ok=1,
                    error=None,
                    pages=0,
                    upserted=0,
                    source_edited_at=edited,
                    newest_event_at=previous.get("newest_event_at"),
                    service_count=previous.get("service_count"),
                    window_count=previous.get("window_count"),
                )
                connection.commit()
                return {
                    "ok": True,
                    "unchanged": True,
                    "source_url": PORTAL_URL,
                    "layer_url": LAYER_URL,
                    "layer_name": layer.get("name"),
                    "layer_id": layer.get("id"),
                    "spatial_wkid": (layer.get("spatialReference") or {}).get("wkid"),
                    "max_record_count": layer.get("maxRecordCount"),
                    "page_size": page_size,
                    "pages": 0,
                    "upserted": 0,
                    "rejected": 0,
                    "window_start": isoformat(window_start),
                    "window_end": isoformat(moment),
                    "window_count": previous.get("window_count"),
                    "service_count": previous.get("service_count"),
                    "source_edited_at": edited,
                    "newest_event_at": previous.get("newest_event_at"),
                    "has_static_data": layer.get("hasStaticData"),
                    "cache_max_age": layer.get("cacheMaxAge"),
                    "reconciled": False,
                    "lookback_hours": lookback_hours,
                }
            effective = lookback_hours
            reconciled = False
            if wide is not None:
                interval = 6 * 60 * 60 if reconcile_interval_seconds is None else int(reconcile_interval_seconds)
                if reconciliation_due(connection, moment, wide, interval):
                    effective = wide
                    reconciled = True
                    window_start = moment - timedelta(hours=effective)
                    connection.execute(
                        "UPDATE sync_runs SET window_start = ? WHERE id = ?",
                        (isoformat(window_start), run_id),
                    )
            where = window_where(window_start)
            window_count = source.count(where)
            records: list[dict[str, Any]] = []
            rejected = 0
            pages = 0
            seen: set[str] = set()
            for page, feature in source.iter_features(where, page_size):
                pages = page
                try:
                    record = parse_feature(feature, moment)
                except RecordError:
                    rejected += 1
                    continue
                if record["source_record_id"] in seen:
                    continue
                seen.add(record["source_record_id"])
                records.append(record)
            upserted = upsert_many(connection, records)
            newest = max((item["called_at"] for item in records), default=None)
            service_count = None
            try:
                service_count = source.count("1=1")
            except DetroitSourceError:
                service_count = None
            finish_run(
                connection,
                run_id,
                finished_at=isoformat(utc_now()),
                ok=1,
                error=None,
                pages=pages,
                upserted=upserted,
                source_edited_at=_edited_at(layer),
                newest_event_at=newest,
                service_count=service_count,
                window_count=window_count,
            )
            connection.commit()
            return {
                "ok": True,
                "unchanged": False,
                "source_url": PORTAL_URL,
                "layer_url": LAYER_URL,
                "layer_name": layer.get("name"),
                "layer_id": layer.get("id"),
                "spatial_wkid": (layer.get("spatialReference") or {}).get("wkid"),
                "max_record_count": layer.get("maxRecordCount"),
                "page_size": page_size,
                "pages": pages,
                "upserted": upserted,
                "rejected": rejected,
                "window_start": isoformat(window_start),
                "window_end": isoformat(moment),
                "window_count": window_count,
                "service_count": service_count,
                "source_edited_at": _edited_at(layer),
                "newest_event_at": newest,
                "has_static_data": layer.get("hasStaticData"),
                "cache_max_age": layer.get("cacheMaxAge"),
                "reconciled": reconciled,
                "lookback_hours": effective,
            }
        except DetroitSourceError as exc:
            finish_run(
                connection,
                run_id,
                finished_at=isoformat(utc_now()),
                ok=0,
                error=exc.message,
            )
            connection.commit()
            raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
