"""ShotSpotter Detroit tests. Fixtures stay in a temporary database."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(ROOT))

from shotspotter.client import (  # noqa: E402
    MAX_PAGES,
    ShotspotterClient,
    ShotspotterSourceError,
    retry_after_seconds,
    validate_metadata,
)
from shotspotter.freshness import LIVE, classify_feed, publication_delay, retrieval_delay  # noqa: E402
from shotspotter import records  # noqa: E402
from shotspotter.records import (  # noqa: E402
    FIELD_NAMES,
    classify_code,
    classify_location,
    parse_called_at,
    parse_feature,
    public_record,
)
from shotspotter.scheduler import interval_seconds, lookback_hours  # noqa: E402
from shotspotter import store  # noqa: E402
from shotspotter.store import connect, statistics, upsert_many  # noqa: E402
from shotspotter.sync import sync_recent  # noqa: E402
from shotspotter.api import ApiError, clear_rate_limit, enforce_rate_limit, export_csv, handle  # noqa: E402

NOW = datetime(2026, 11, 17, 18, 0, tzinfo=timezone.utc)
EDITED = "2026-11-16T11:38:00Z"
EDITED_MS = int(datetime(2026, 11, 16, 11, 38, tzinfo=timezone.utc).timestamp() * 1000)
RECENT_MS = int(datetime(2026, 11, 16, 9, 52, 50, tzinfo=timezone.utc).timestamp() * 1000)
OLD_MS = int(datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)


def layer(edited_ms=EDITED_MS, fields=FIELD_NAMES, object_id="incident_entry_id"):
    return {
        "name": "shotspotter_911_calls",
        "id": 0,
        "spatialReference": {"wkid": 4326},
        "fields": [{"name": name} for name in fields],
        "geometryType": "esriGeometryPoint",
        "objectIdField": object_id,
        "advancedQueryCapabilities": {"supportsPagination": True},
        "maxRecordCount": 2000,
        "editingInfo": {"dataLastEditDate": edited_ms},
        "hasAttachments": False,
    }


def feature(entry, incident, called_ms=RECENT_MS, code="SST345", lat=42.35, lon=-83.10, location="WOODWARD & WARREN"):
    attributes = {name: None for name in FIELD_NAMES}
    attributes.update({
        "incident_entry_id": entry,
        "incident_id": incident,
        "incident_location": location,
        "call_code": code,
        "call_description": "SHOTSPOTTER",
        "called_at": called_ms,
        "latitude": lat,
        "longitude": lon,
        "neighborhood": "Fixture Neighborhood",
        "zip_code": "48202",
        "police_precinct": 7,
    })
    return {"attributes": attributes}


class ScriptClient:
    """Stand-in for the ArcGIS client. It never contacts the network."""

    def __init__(self, pages, edited_ms=EDITED_MS, window_count=1, service_count=10, fail=None):
        self.pages = pages
        self.edited_ms = edited_ms
        self.window_count = window_count
        self.service_count = service_count
        self.fail = fail
        self.metadata_calls = 0
        self.feature_calls = 0

    def fetch_metadata(self):
        self.metadata_calls += 1
        if self.fail == "metadata":
            raise ShotspotterSourceError("unavailable", "down")
        return layer(self.edited_ms)

    def count(self, where):
        if where == "1=1":
            return self.service_count
        return self.window_count

    def iter_features(self, where, page_size):
        self.feature_calls += 1
        for index, page in enumerate(self.pages, start=1):
            for item in page:
                yield index, item


class ShotspotterRecordTests(unittest.TestCase):
    def test_timestamp_accepts_epoch_milliseconds_and_seconds(self):
        millis = parse_called_at(RECENT_MS)
        seconds = parse_called_at(RECENT_MS / 1000.0)
        self.assertEqual(millis, seconds)
        self.assertEqual(millis.tzinfo, timezone.utc)

    def test_missing_timestamp_is_rejected(self):
        with self.assertRaises(records.RecordError):
            parse_called_at(None)
        with self.assertRaises(records.RecordError):
            parse_feature({"attributes": {"incident_id": "fixture-missing"}}, NOW, EDITED)

    def test_codes_and_blank_classification(self):
        self.assertEqual(classify_code("SST345")[0], "single")
        self.assertEqual(classify_code("SST346")[0], "multiple")
        self.assertEqual(classify_code("SST342")[0], "probable")
        self.assertEqual(classify_code("  ")[0], "unclassified")
        self.assertEqual(classify_code("843020")[0], "other")

    def test_coordinates_are_not_invented(self):
        outside = classify_location("WOODWARD & WARREN", 10, 10)
        self.assertFalse(outside["plottable"])
        self.assertIsNone(outside["latitude"])
        self.assertIsNone(outside["longitude"])
        house = classify_location("123 MAIN ST", 42.35, -83.1)
        self.assertEqual(house["location_precision"], "withheld")
        self.assertIsNone(house["latitude"])
        missing = classify_location(None, None, None)
        self.assertFalse(missing["plottable"])
        self.assertIsNone(missing["latitude"])

    def test_publication_delay_stays_empty_without_a_per_incident_time(self):
        event = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)
        delay, reason = publication_delay(event, None)
        self.assertIsNone(delay)
        self.assertIn("no per-incident", reason)
        parsed = parse_feature(feature(1, "fixture-delay"), NOW, EDITED)
        self.assertIsNone(parsed["publication_delay_seconds"])
        self.assertGreater(parsed["retrieval_delay_seconds"], 0)
        backwards, why = retrieval_delay(NOW, event)
        self.assertIsNone(backwards)
        self.assertIn("earlier", why)

    def test_public_record_hides_raw_source_and_is_not_a_live_alert(self):
        parsed = parse_feature(feature(1, "fixture-public", called_ms=OLD_MS), NOW, EDITED)
        shown = public_record(parsed, NOW)
        self.assertNotIn("raw_attributes", shown)
        self.assertFalse(shown["live_alert"])
        self.assertFalse(shown["happening_now"])
        self.assertEqual(shown["freshness"], "HISTORICAL")
        self.assertTrue(shown["newly_retrieved_historical"])
        self.assertIn("not a new alert", shown["verification_note"])


class ShotspotterClientTests(unittest.TestCase):
    def test_schema_validation_rejects_a_changed_layer(self):
        self.assertEqual(validate_metadata(layer()), 2000)
        broken = layer(fields=tuple(name for name in FIELD_NAMES if name != "called_at"))
        with self.assertRaises(ShotspotterSourceError) as caught:
            validate_metadata(broken)
        self.assertEqual(caught.exception.code, "unexpected_fields")
        wrong = layer(object_id="ESRI_OID")
        with self.assertRaises(ShotspotterSourceError) as caught:
            validate_metadata(wrong)
        self.assertEqual(caught.exception.code, "unexpected_object_id")

    def test_retry_after_is_capped(self):
        self.assertEqual(retry_after_seconds("999999"), 3600)
        self.assertEqual(retry_after_seconds("15"), 15)

    def _client(self, handler):
        def opener(request, timeout=None):
            return handler(request)
        return ShotspotterClient(opener=opener, sleeper=lambda _seconds: None)

    def test_timeout_is_retried_once(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("slow")
            return JsonResponse(layer())

        found = self._client(handler).fetch_metadata()
        self.assertEqual(found["name"], "shotspotter_911_calls")
        self.assertEqual(calls["n"], 2)

    def test_rate_limit_is_not_retried(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many", {"Retry-After": "12"}, io.BytesIO(b""))

        with self.assertRaises(ShotspotterSourceError) as caught:
            self._client(handler).fetch_metadata()
        self.assertEqual(caught.exception.code, "rate_limited")
        self.assertEqual(caught.exception.retry_after, 12)
        self.assertEqual(calls["n"], 1)

    def test_http_error_and_malformed_json(self):
        def http_error(request):
            raise urllib.error.HTTPError(request.full_url, 500, "Error", {}, io.BytesIO(b""))

        with self.assertRaises(ShotspotterSourceError) as caught:
            self._client(http_error).fetch_metadata()
        self.assertEqual(caught.exception.code, "http_500")

        def malformed(request):
            return JsonResponse(b"not-json", raw=True)

        with self.assertRaises(ShotspotterSourceError) as caught:
            self._client(malformed).fetch_metadata()
        self.assertEqual(caught.exception.code, "malformed")

        def query_error(request):
            return JsonResponse({"error": {"message": "bad where"}})

        with self.assertRaises(ShotspotterSourceError) as caught:
            self._client(query_error).fetch_metadata()
        self.assertEqual(caught.exception.code, "query_error")

    def test_pagination_stops_and_rejects_a_stuck_page(self):
        pages = {
            0: [feature(1, "fixture-p1")],
            1: [feature(2, "fixture-p2")],
        }

        def handler(request):
            query = request.full_url
            if "returnCountOnly" in query:
                return JsonResponse({"count": 2})
            if "resultOffset=0" in query:
                return JsonResponse({"features": pages[0], "exceededTransferLimit": True})
            if "resultOffset=1" in query:
                return JsonResponse({"features": pages[1], "exceededTransferLimit": False})
            return JsonResponse(layer())

        client = self._client(handler)
        client.query_url = "https://example.test/query"
        client.layer_url = "https://example.test/layer"
        found = list(client.iter_features("called_at >= timestamp '2026-11-01 00:00:00'", 1))
        self.assertEqual(len(found), 2)
        self.assertEqual(found[1][0], 2)

        def stuck(request):
            return JsonResponse({"features": [feature(1, "fixture-stuck")], "exceededTransferLimit": True})

        client = self._client(stuck)
        import shotspotter.client as client_module
        original = client_module.MAX_PAGES
        client_module.MAX_PAGES = 2
        try:
            with self.assertRaises(ShotspotterSourceError) as caught:
                list(client.iter_features("1=1", 1))
            self.assertEqual(caught.exception.code, "page_stuck")
        finally:
            client_module.MAX_PAGES = original
        self.assertEqual(MAX_PAGES, 25)


class JsonResponse:
    def __init__(self, payload, status=200, raw=False):
        self.status = status
        self.headers = {}
        if raw:
            self._body = payload
        else:
            self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class ShotspotterStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "isolated-shotspotter.sqlite"
        self._saved = {}
        for name in (
            "SHOTSPOTTER_DB",
            "SHOTSPOTTER_ALLOW_FIXTURES",
            "SHOTSPOTTER_API_RATE_PER_MINUTE",
            "SHOTSPOTTER_SYNC_INTERVAL_SECONDS",
            "SHOTSPOTTER_LOOKBACK_HOURS",
        ):
            self._saved[name] = os.environ.get(name)
        os.environ["SHOTSPOTTER_DB"] = str(self.db)
        os.environ["SHOTSPOTTER_ALLOW_FIXTURES"] = "1"
        os.environ["SHOTSPOTTER_API_RATE_PER_MINUTE"] = "0"
        clear_rate_limit()

    def tearDown(self):
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        clear_rate_limit()
        self.tmp.cleanup()

    def test_duplicate_upsert_keeps_one_row_and_the_first_retrieval(self):
        connection = connect(self.db)
        try:
            first = parse_feature(feature(1, "fixture-dup"), NOW, EDITED)
            second = parse_feature(feature(1, "fixture-dup"), NOW + timedelta(hours=2), EDITED)
            self.assertEqual(upsert_many(connection, [first, first]), 2)
            connection.commit()
            # The sync loop drops duplicates before upsert. The store still collapses one id.
            connection.execute("DELETE FROM shotspotter_incidents")
            upsert_many(connection, [first])
            connection.commit()
            upsert_many(connection, [second])
            connection.commit()
            count = connection.execute("SELECT COUNT(*) FROM shotspotter_incidents").fetchone()[0]
            row = connection.execute("SELECT first_retrieved_at, retrieved_at FROM shotspotter_incidents").fetchone()
            self.assertEqual(count, 1)
            self.assertEqual(row["first_retrieved_at"], first["first_retrieved_at"])
            self.assertEqual(row["retrieved_at"], first["retrieved_at"])
        finally:
            connection.close()

    def test_fixtures_cannot_enter_the_production_database(self):
        os.environ["SHOTSPOTTER_ALLOW_FIXTURES"] = "1"
        connection = connect(store.DEFAULT_DB)
        try:
            record = parse_feature(feature(1, "fixture-production"), NOW, EDITED)
            with self.assertRaises(store.StoreError):
                upsert_many(connection, [record])
            connection.rollback()
            found = connection.execute(
                "SELECT COUNT(*) FROM shotspotter_incidents WHERE source_record_id LIKE 'fixture-%'"
            ).fetchone()[0]
            self.assertEqual(found, 0)
        finally:
            connection.close()

    def test_fixtures_require_the_isolated_flag(self):
        os.environ["SHOTSPOTTER_ALLOW_FIXTURES"] = "0"
        connection = connect(self.db)
        try:
            record = parse_feature(feature(1, "fixture-blocked"), NOW, EDITED)
            with self.assertRaises(store.StoreError):
                upsert_many(connection, [record])
        finally:
            connection.close()

    def test_sync_is_incremental_and_survives_a_restart(self):
        client = ScriptClient([[feature(1, "fixture-sync", called_ms=RECENT_MS), feature(1, "fixture-sync")]])
        first = sync_recent(db_path=self.db, lookback_hours=48, client=client, now=NOW)
        self.assertTrue(first["ok"])
        self.assertEqual(first["upserted"], 1)
        self.assertEqual(first["feed_status"], "DELAYED")
        self.assertFalse(first["realtime_access"])
        self.assertEqual(client.feature_calls, 1)
        again = ScriptClient([], edited_ms=EDITED_MS)
        second = sync_recent(db_path=self.db, lookback_hours=48, client=again, now=NOW + timedelta(minutes=5))
        self.assertTrue(second["unchanged"])
        self.assertEqual(second["pages"], 0)
        self.assertEqual(again.feature_calls, 0)
        connection = connect(self.db)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM shotspotter_incidents").fetchone()[0], 1)
        finally:
            connection.close()

    def test_failed_sync_keeps_stored_rows(self):
        sync_recent(
            db_path=self.db,
            lookback_hours=48,
            client=ScriptClient([[feature(1, "fixture-keep")]]),
            now=NOW,
        )
        with self.assertRaises(ShotspotterSourceError):
            sync_recent(
                db_path=self.db,
                lookback_hours=48,
                client=ScriptClient([], fail="metadata"),
                now=NOW + timedelta(minutes=1),
            )
        connection = connect(self.db)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM shotspotter_incidents").fetchone()[0], 1)
            status = connection.execute("SELECT feed_status FROM shotspotter_source_status").fetchone()[0]
            self.assertNotEqual(status, "LIVE")
        finally:
            connection.close()

    def test_statistics_filters_and_map_use_stored_rows_only(self):
        rows = [
            parse_feature(feature(1, "fixture-single", code="SST345"), NOW, EDITED),
            parse_feature(feature(2, "fixture-multi", code="SST346"), NOW, EDITED),
            parse_feature(feature(3, "fixture-old", code="SST342", called_ms=OLD_MS), NOW, EDITED),
            parse_feature(feature(4, "fixture-blank", code=None, lat=None, lon=None, location=None), NOW, EDITED),
            parse_feature(feature(5, "fixture-far", lat=10, lon=10), NOW, EDITED),
        ]
        connection = connect(self.db)
        try:
            upsert_many(connection, rows)
            connection.execute(
                "UPDATE shotspotter_incidents SET publication_delay_seconds = 90 WHERE source_record_id = ?",
                ("fixture-single",),
            )
            connection.commit()
            counts = statistics(connection, NOW)
            self.assertEqual(counts["stored_incidents"], 5)
            self.assertEqual(counts["single_shot"], 2)
            self.assertEqual(counts["multiple_shot"], 1)
            self.assertEqual(counts["probable_gunfire"], 1)
            self.assertEqual(counts["unclassified"], 1)
            self.assertEqual(counts["publication_delay_samples"], 1)
            self.assertEqual(counts["average_publication_delay_seconds"], 90)
        finally:
            connection.close()
        status, payload = handle("/api/shotspotter-detroit/incidents", {"classification": "multiple"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["records"][0]["source_record_id"], "fixture-multi")
        self.assertNotIn("raw_attributes", payload["records"][0])
        status, mapped = handle("/api/shotspotter-detroit/map", {})
        plotted = {point["source_record_id"] for point in mapped["points"]}
        self.assertIn("fixture-single", plotted)
        self.assertNotIn("fixture-blank", plotted)
        self.assertNotIn("fixture-far", plotted)
        self.assertGreaterEqual(mapped["unplotted"], 2)
        status, stats = handle("/api/shotspotter-detroit/statistics", {})
        self.assertEqual(stats["stored_incidents"], 5)
        self.assertIn("not a count of dispatched units", stats["count_basis"])
        status, one = handle("/api/shotspotter-detroit/incidents/fixture-old", {})
        self.assertEqual(one["record"]["freshness"], "HISTORICAL")
        status, text = export_csv({})
        self.assertEqual(status, 200)
        self.assertIn("incident_id", text)
        self.assertNotIn("raw_attributes", text.splitlines()[0])
        self.assertIn("fixture-single", text)

    def test_feed_status_cannot_be_live_for_the_public_extract(self):
        os.environ["SHOTSPOTTER_ALLOW_FIXTURES"] = "1"
        sync_recent(
            db_path=self.db,
            lookback_hours=48,
            client=ScriptClient([[feature(1, "fixture-delayed")]]),
            now=NOW,
        )
        status, health = handle("/api/shotspotter-detroit/health", {})
        self.assertFalse(health["live"])
        self.assertFalse(health["realtime_access"])
        self.assertNotEqual(health["feed_status"], "LIVE")
        status, sources = handle("/api/shotspotter-detroit/sources", {})
        enabled = [item for item in sources["providers"] if item["enabled"]]
        self.assertEqual([item["provider_id"] for item in enabled], ["detroit"])
        self.assertTrue(all(item["feed_status"] != "LIVE" and item["live"] is False for item in sources["providers"]))
        cities = {item["provider_id"] for item in sources["providers"]}
        self.assertTrue({"washington-dc", "milwaukee", "chicago"} <= cities)

    def test_api_validation_and_rate_limit(self):
        with self.assertRaises(ApiError):
            handle("/api/shotspotter-detroit/incidents", {"from": "yesterday"})
        with self.assertRaises(ApiError):
            handle("/api/shotspotter-detroit/nope", {})
        clear_rate_limit()
        os.environ["SHOTSPOTTER_API_RATE_PER_MINUTE"] = "2"
        enforce_rate_limit(1000)
        enforce_rate_limit(1001)
        with self.assertRaises(ApiError) as caught:
            enforce_rate_limit(1002)
        self.assertEqual(caught.exception.status, 429)
        enforce_rate_limit(1063)
        os.environ["SHOTSPOTTER_API_RATE_PER_MINUTE"] = "0"

    def test_missing_database_does_not_invent_counts(self):
        missing = Path(self.tmp.name) / "absent.sqlite"
        os.environ["SHOTSPOTTER_DB"] = str(missing)
        status, payload = handle("/api/shotspotter-detroit/statistics", {})
        self.assertEqual(payload["stored_incidents"], 0)
        self.assertIsNone(payload["average_publication_delay_seconds"])
        self.assertFalse(missing.exists())
        status, health = handle("/api/shotspotter-detroit/health", {})
        self.assertEqual(health["feed_status"], "UNVERIFIED")
        self.assertFalse(health["live"])

    def test_scheduler_bounds(self):
        self.assertEqual(interval_seconds(None), 60)
        self.assertEqual(interval_seconds("0"), 0)
        self.assertEqual(interval_seconds("15"), 60)
        self.assertEqual(interval_seconds("120"), 120)
        self.assertEqual(lookback_hours(None), 336)
        self.assertEqual(lookback_hours("400"), 336)
        self.assertEqual(lookback_hours("48"), 48)


class ShotspotterFreshnessTests(unittest.TestCase):
    def test_live_requires_an_authorized_measured_stream(self):
        now = NOW
        recent = records.isoformat(now - timedelta(minutes=1))
        edited = records.isoformat(now)
        status, _detail = classify_feed(
            realtime_verified=True,
            measured_latency_seconds=30,
            provider_kind="authorized_stream",
            newest_event_at=recent,
            source_edited_at=edited,
            sync_ok=True,
            now=now,
        )
        self.assertEqual(status, LIVE)
        status, _detail = classify_feed(
            realtime_verified=True,
            measured_latency_seconds=30,
            provider_kind="public_arcgis_poll",
            newest_event_at=recent,
            source_edited_at=edited,
            sync_ok=True,
            now=now,
        )
        self.assertEqual(status, "DELAYED")
        status, _detail = classify_feed(
            realtime_verified=False,
            measured_latency_seconds=None,
            provider_kind="public_arcgis_poll",
            newest_event_at=records.isoformat(now - timedelta(days=3)),
            source_edited_at=edited,
            sync_ok=True,
            now=now,
        )
        self.assertEqual(status, "HISTORICAL")
        status, _detail = classify_feed(
            realtime_verified=False,
            measured_latency_seconds=None,
            provider_kind="public_arcgis_poll",
            newest_event_at=None,
            source_edited_at=None,
            sync_ok=True,
            now=now,
        )
        self.assertEqual(status, "UNVERIFIED")
        status, _detail = classify_feed(
            realtime_verified=False,
            measured_latency_seconds=None,
            provider_kind="public_arcgis_poll",
            newest_event_at=None,
            source_edited_at=None,
            sync_ok=False,
            now=now,
        )
        self.assertEqual(status, "UNAVAILABLE")


class ShotspotterPageTests(unittest.TestCase):
    def test_navigation_and_script_do_not_use_inner_html(self):
        page = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "shotspotter.js").read_text(encoding="utf-8")
        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('data-nav="shotspotter-detroit"', page)
        self.assertIn("SHOTSPOTTER DETROIT", page)
        self.assertIn('href="#/detroit"', page)
        self.assertNotIn("innerHTML", script)
        self.assertNotIn(".style.", script)
        self.assertIn("renderShotspotter", app)
        self.assertIn("disposeShotspotterMap", app)
        self.assertIn("#/shotspotter-detroit", script)


if __name__ == "__main__":
    unittest.main()
