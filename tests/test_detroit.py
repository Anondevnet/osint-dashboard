"""Fixture tests for the Detroit open-data connector.

Every incident in this file is synthetic. IDs start with FIXTURE and streets
are Example, Sample, or Secret Home. Nothing here is a live call.
"""

from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from detroit import DATA_TYPE, PORTAL_URL, SOURCE_NAME, VERIFICATION  # noqa: E402
from detroit.api import ApiError, describe_sync, handle  # noqa: E402
from detroit.client import (  # noqa: E402
    DetroitClient,
    DetroitSourceError,
    retry_after_seconds,
    validate_metadata,
    window_where,
)
from detroit.records import (  # noqa: E402
    FIELD_NAMES,
    LAT_MAX,
    LAT_MIN,
    LON_MAX,
    LON_MIN,
    RecordError,
    classify_location,
    freshness,
    isoformat,
    map_points,
    parse_called_at,
    parse_feature,
    public_record,
)
from detroit.store import COLUMNS, connect, query_incidents, upsert_many  # noqa: E402
from detroit.sync import sync_recent  # noqa: E402

FIXTURE_LAYER = ROOT / "tests" / "fixtures" / "detroit_layer_fields.json"
CALLED_MS = 1474364172000  # 2016-09-20T09:36:12Z, used only as a conversion sample.


def load_layer() -> dict:
    payload = json.loads(FIXTURE_LAYER.read_text())
    assert payload["fixture"] is True
    return payload


def epoch_ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def fixture_feature(**overrides) -> dict:
    """Synthetic ArcGIS feature. Not a published Detroit incident."""
    attributes = {
        "incident_entry_id": 1001,
        "incident_id": "FIXTURE100001",
        "incident_location": "Example St & Sample Ave",
        "call_source": "PHONE",
        "call_description": "FIXTURE nature",
        "category": "FIXTURE",
        "priority": "3",
        "call_group": "99PCT",
        "call_code": "FIX",
        "called_at": CALLED_MS,
        "intake_time": 1.5,
        "dispatch_time": -2.0,
        "travel_time": 4,
        "on_scene_time": None,
        "total_response_time": 5.5,
        "total_time": 9,
        "zip_code": "00000",
        "precinct": 8,
        "scout_car_area": "SCA-FIX",
        "neighborhood_name": "Fixture Neighborhood",
        "council_district": 1,
        "longitude": -83.1,
        "latitude": 42.35,
        "phone": "555-0100",
        "caller_name": "Should Not Persist",
        "ESRI_OID": 999999,
    }
    attributes.update(overrides)
    return {"attributes": attributes}


class Response:
    def __init__(self, payload, status=200):
        self.status = status
        self._payload = payload

    def read(self):
        if isinstance(self._payload, bytes):
            return self._payload
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def scripted(payloads):
    calls = []

    def opener(request, timeout=None):
        calls.append(request.full_url)
        if not payloads:
            raise AssertionError("unexpected request")
        item = payloads.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return opener, calls


class DetroitFixtureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "fixture.sqlite"
        self.previous = os.environ.get("DETROIT_DB")
        os.environ["DETROIT_DB"] = str(self.db)

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("DETROIT_DB", None)
        else:
            os.environ["DETROIT_DB"] = self.previous
        self.tmp.cleanup()

    def test_fixture_metadata_matches_documented_fields(self):
        layer = load_layer()
        self.assertEqual(validate_metadata(layer), 2000)
        names = {field["name"] for field in layer["fields"]}
        for name in FIELD_NAMES:
            self.assertIn(name, names)
        self.assertIn("ESRI_OID", names)
        self.assertNotIn("ESRI_OID", FIELD_NAMES)
        self.assertEqual(layer["spatialReference"]["wkid"], 4326)
        self.assertTrue(layer["advancedQueryCapabilities"]["supportsPagination"])
        wide = deepcopy(layer)
        wide["maxRecordCount"] = 16000
        self.assertEqual(validate_metadata(wide), 2000)
        for change, code in (
            ({"name": "other"}, "unexpected_layer"),
            ({"spatialReference": {"wkid": 3857}}, "unexpected_spatial_reference"),
            ({"geometryType": "esriGeometryPolygon"}, "unexpected_geometry"),
            ({"objectIdField": "incident_entry_id"}, "unexpected_object_id"),
            ({"advancedQueryCapabilities": {"supportsPagination": False}}, "unexpected_pagination"),
            ({"maxRecordCount": 0}, "unexpected_page_size"),
        ):
            broken = deepcopy(layer)
            broken.update(change)
            with self.assertRaises(DetroitSourceError) as caught:
                validate_metadata(broken)
            self.assertEqual(caught.exception.code, code)
        missing = deepcopy(layer)
        missing["fields"] = [field for field in missing["fields"] if field["name"] != "called_at"]
        with self.assertRaises(DetroitSourceError) as caught:
            validate_metadata(missing)
        self.assertEqual(caught.exception.code, "unexpected_fields")

    def test_query_page_uses_documented_fields_without_geometry(self):
        opener, calls = scripted([Response({"features": []})])
        client = DetroitClient(opener=opener, timeout=1)
        client.query_page("called_at >= timestamp '2026-11-14 00:00:00'", 0, 200)
        self.assertEqual(len(calls), 1)
        url = calls[0]
        self.assertIn("returnGeometry=false", url)
        self.assertIn("incident_location", url)
        self.assertIn("called_at", url)
        self.assertNotIn("ESRI_OID", url)
        self.assertNotIn("outSR", url)

    def test_timestamp_window_uses_utc_timestamp_literal(self):
        start = datetime(2026, 11, 14, 9, 30, tzinfo=timezone.utc)
        clause = window_where(start)
        self.assertEqual(clause, "called_at >= timestamp '2026-11-14 09:30:00'")
        self.assertNotIn("1794", clause)

    def test_pagination_stops_on_a_short_page_and_on_a_repeated_tail(self):
        first = fixture_feature(incident_id="FIXTUREPAGE0001", incident_entry_id=1)
        second = fixture_feature(incident_id="FIXTUREPAGE0002", incident_entry_id=2)
        third = fixture_feature(incident_id="FIXTUREPAGE0003", incident_entry_id=3)
        opener, calls = scripted([
            Response({"features": [first, second], "exceededTransferLimit": True}),
            Response({"features": [third], "exceededTransferLimit": False}),
        ])
        client = DetroitClient(opener=opener, timeout=1)
        pages = [page for page, _feature in client.iter_features("called_at >= timestamp '2026-11-14 00:00:00'", 2)]
        self.assertEqual(pages, [1, 1, 2])
        self.assertEqual(len(calls), 2)

        stuck = fixture_feature(incident_id="FIXTURESTUCK0001", incident_entry_id=50)
        other = fixture_feature(incident_id="FIXTUREOTHER0001", incident_entry_id=51)
        opener, _calls = scripted([
            Response({"features": [other, stuck], "exceededTransferLimit": True}),
            Response({"features": [other, stuck], "exceededTransferLimit": True}),
        ])
        client = DetroitClient(opener=opener, timeout=1)
        with self.assertRaises(DetroitSourceError) as caught:
            list(client.iter_features("called_at >= timestamp '2026-11-14 00:00:00'", 2))
        self.assertEqual(caught.exception.code, "page_stuck")

    def test_pagination_stops_when_the_transfer_limit_flag_is_false(self):
        features = [
            fixture_feature(incident_id="FIXTUREFULL%04d" % index, incident_entry_id=index)
            for index in (1, 2)
        ]
        opener, calls = scripted([
            Response({"features": features, "exceededTransferLimit": False}),
            Response({"features": features, "exceededTransferLimit": True}),
        ])
        client = DetroitClient(opener=opener, timeout=1)
        found = list(client.iter_features("called_at >= timestamp '2026-11-14 00:00:00'", 2))
        self.assertEqual(len(found), 2)
        self.assertEqual(len(calls), 1)

    def test_page_limit_stops_a_long_recent_window(self):
        payloads = []
        for index in range(25):
            payloads.append(Response({
                "features": [fixture_feature(
                    incident_id="FIXTURELIMIT%03d" % index,
                    incident_entry_id=index + 1,
                    called_at=CALLED_MS + index,
                )],
                "exceededTransferLimit": True,
            }))
        opener, calls = scripted(payloads)
        client = DetroitClient(opener=opener, timeout=1)
        with self.assertRaises(DetroitSourceError) as caught:
            list(client.iter_features("called_at >= timestamp '2026-11-14 00:00:00'", 1))
        self.assertEqual(caught.exception.code, "page_limit")
        self.assertEqual(len(calls), 25)

    def test_api_failures_timeouts_and_malformed_payloads(self):
        cases = [
            (TimeoutError(), "timeout"),
            (socket.timeout("timed out"), "timeout"),
            (urllib.error.URLError(socket.timeout("timed out")), "timeout"),
            (urllib.error.URLError(OSError("refused")), "unavailable"),
            (
                urllib.error.HTTPError(
                    "https://services2.arcgis.com/fixture",
                    503,
                    "unavailable",
                    Message(),
                    io.BytesIO(b""),
                ),
                "http_503",
            ),
            (
                urllib.error.HTTPError(
                    "https://services2.arcgis.com/fixture",
                    429,
                    "limited",
                    Message(),
                    io.BytesIO(b""),
                ),
                "rate_limited",
            ),
        ]
        for exc, code in cases:
            opener, _calls = scripted([exc])
            client = DetroitClient(opener=opener, timeout=1)
            with self.assertRaises(DetroitSourceError) as caught:
                client.fetch_metadata()
            self.assertEqual(caught.exception.code, code)

        opener, _calls = scripted([Response({"error": {"message": "Unable to complete operation."}})])
        client = DetroitClient(opener=opener, timeout=1)
        with self.assertRaises(DetroitSourceError) as caught:
            client.fetch_metadata()
        self.assertEqual(caught.exception.code, "query_error")
        self.assertIn("Unable to complete operation.", caught.exception.message)

        opener, _calls = scripted([Response(b"not-json")])
        client = DetroitClient(opener=opener, timeout=1)
        with self.assertRaises(DetroitSourceError) as caught:
            client.fetch_metadata()
        self.assertEqual(caught.exception.code, "malformed")

        opener, _calls = scripted([Response(["not", "an", "object"])])
        client = DetroitClient(opener=opener, timeout=1)
        with self.assertRaises(DetroitSourceError) as caught:
            client.count("called_at >= timestamp '2026-11-14 00:00:00'")
        self.assertEqual(caught.exception.code, "malformed")

        opener, _calls = scripted([Response({"features": "nope"})])
        client = DetroitClient(opener=opener, timeout=1)
        with self.assertRaises(DetroitSourceError) as caught:
            list(client.iter_features("called_at >= timestamp '2026-11-14 00:00:00'", 2))
        self.assertEqual(caught.exception.code, "malformed")

        opener, _calls = scripted([Response({"error": {"message": "bad"}}, status=400)])
        client = DetroitClient(opener=opener, timeout=1)
        with self.assertRaises(DetroitSourceError) as caught:
            client.fetch_metadata()
        self.assertEqual(caught.exception.code, "http_400")

    def test_timestamp_conversion_preserves_utc_epoch_milliseconds(self):
        self.assertEqual(isoformat(parse_called_at(CALLED_MS)), "2016-09-20T09:36:12Z")
        self.assertEqual(isoformat(parse_called_at(CALLED_MS / 1000)), "2016-09-20T09:36:12Z")
        self.assertEqual(isoformat(parse_called_at(str(CALLED_MS))), "2016-09-20T09:36:12Z")
        for value in (None, True, "not-a-time", "", 10 ** 30):
            with self.assertRaises(RecordError):
                parse_called_at(value)

    def test_invalid_records_are_rejected(self):
        retrieved = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
        for feature in (
            None,
            [],
            {"attributes": None},
            {"attributes": {"incident_id": "FIXTUREBAD0001", "called_at": "yesterday"}},
            {"attributes": {"incident_id": "FIXTUREBAD0002", "incident_entry_id": 1.5, "called_at": CALLED_MS}},
            {"attributes": {"called_at": CALLED_MS, "incident_location": "Example St & Sample Ave"}},
            {"attributes": {"incident_id": "has spaces", "called_at": CALLED_MS}},
        ):
            with self.assertRaises(RecordError):
                parse_feature(feature, retrieved)

    def test_privacy_safe_location_handling(self):
        retrieved = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
        house = parse_feature(
            fixture_feature(incident_location="123 Secret Home Rd", latitude=42.35, longitude=-83.1),
            retrieved,
        )
        self.assertIsNone(house["intersection"])
        self.assertIsNone(house["latitude"])
        self.assertIsNone(house["longitude"])
        self.assertFalse(house["plottable"])
        self.assertEqual(house["location_precision"], "withheld")
        self.assertEqual(map_points([public_record(house, retrieved)]), [])
        self.assertNotIn("555-0100", json.dumps(house))
        self.assertNotIn("Should Not Persist", json.dumps(house))
        self.assertNotIn("phone", house)
        self.assertNotIn("ESRI_OID", house)

        missing = classify_location("Example St & Sample Ave", None, None)
        self.assertEqual(missing["intersection"], "Example St & Sample Ave")
        self.assertFalse(missing["plottable"])
        self.assertIsNone(missing["latitude"])
        self.assertEqual(missing["location_precision"], "missing_coordinates")

        outside = classify_location("Example St & Sample Ave", 40.0, -75.0)
        self.assertEqual(outside["intersection"], "Example St & Sample Ave")
        self.assertFalse(outside["plottable"])
        self.assertIsNone(outside["latitude"])

        numbered_intersection = classify_location("12 Example & Sample Ave", 42.35, -83.1)
        self.assertEqual(numbered_intersection["location_precision"], "nearest_intersection")
        self.assertTrue(numbered_intersection["plottable"])

        source = (ROOT / "detroit" / "records.py").read_text().lower()
        self.assertNotIn("nominatim", source)
        self.assertNotIn("reverse", source)
        for name in COLUMNS:
            self.assertNotIn("phone", name)
            self.assertNotIn("caller", name)
            self.assertNotIn("audio", name)

    def test_negative_duration_is_preserved_and_not_treated_as_live(self):
        retrieved = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
        record = public_record(parse_feature(fixture_feature(), retrieved), retrieved)
        self.assertEqual(record["dispatch_time"], -2.0)
        dispatch = [item for item in record["durations"] if item["field"] == "dispatch_time"][0]
        self.assertEqual(dispatch["minutes"], -2.0)
        self.assertIn("not a live response", dispatch["note"])
        self.assertFalse(record["happening_now"])
        self.assertFalse(record["outcome_known"])
        self.assertEqual(record["priority"], "3")

    def test_stale_data_label_follows_retrieval_time(self):
        now = datetime(2026, 11, 14, 18, 0, tzinfo=timezone.utc)
        self.assertEqual(freshness(isoformat(now - timedelta(hours=6)), now), "recently_retrieved")
        self.assertEqual(freshness(isoformat(now - timedelta(hours=6, seconds=1)), now), "stored")
        self.assertEqual(freshness(isoformat(now - timedelta(hours=24)), now), "stale")
        self.assertEqual(freshness(isoformat(now - timedelta(hours=23, minutes=59)), now), "stored")
        stale = public_record(
            parse_feature(fixture_feature(), now - timedelta(hours=30)),
            now,
        )
        self.assertEqual(stale["freshness"], "stale")
        self.assertIn("Stale", stale["freshness_label"])
        self.assertIn("not live", stale["freshness_label"])
        self.assertEqual(stale["timeline"][0]["kind"], "call_received")
        self.assertEqual(stale["timeline"][1]["kind"], "retrieved")
        self.assertNotEqual(stale["timeline"][0]["at"], stale["timeline"][1]["at"])
        self.assertEqual(stale["called_at"], "2016-09-20T09:36:12Z")

    def test_map_filtering_and_duplicate_prevention(self):
        now = datetime.now(timezone.utc)
        recent_ms = epoch_ms(datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc))
        older_ms = epoch_ms(datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc))
        rows = [
            parse_feature(fixture_feature(
                incident_id="FIXTUREFRESH0001",
                incident_entry_id=1,
                category="FIXTURE-A",
                priority="1",
                precinct=8,
                call_source="W911",
                called_at=recent_ms,
            ), now - timedelta(hours=1)),
            parse_feature(fixture_feature(
                incident_id="FIXTUREHOUSE0001",
                incident_entry_id=2,
                category="FIXTURE-A",
                priority="1",
                precinct=8,
                call_source="W911",
                incident_location="123 Secret Home Rd",
                called_at=recent_ms,
            ), now - timedelta(hours=1)),
            parse_feature(fixture_feature(
                incident_id="FIXTURESTALE0001",
                incident_entry_id=3,
                category="FIXTURE-A",
                priority="1",
                precinct=8,
                called_at=recent_ms,
            ), now - timedelta(hours=30)),
            parse_feature(fixture_feature(
                incident_id="FIXTURENOISE0001",
                incident_entry_id=4,
                category="FIXTURE-B",
                priority="5",
                precinct=2,
                call_source="E911",
                called_at=older_ms,
            ), now - timedelta(hours=1)),
            parse_feature(fixture_feature(
                incident_id="FIXTUREBLANK0001",
                incident_entry_id=5,
                category="FIXTURE-A",
                priority="1",
                precinct=8,
                incident_location="Example St & Sample Ave",
                latitude=None,
                longitude=None,
                called_at=recent_ms,
            ), now - timedelta(hours=10)),
        ]
        connection = connect(self.db)
        try:
            self.assertEqual(upsert_many(connection, rows), 5)
            rows[0]["nature"] = "FIXTURE nature updated"
            self.assertEqual(upsert_many(connection, [rows[0]]), 1)
            connection.commit()
            stored = connection.execute("SELECT COUNT(*), nature FROM incidents WHERE source_record_id = ?", ("FIXTUREFRESH0001",)).fetchone()
            self.assertEqual(stored[0], 1)
            self.assertEqual(stored[1], "FIXTURE nature updated")
            arguments = {
                "start": "2026-11-14T00:00:00Z",
                "end": "2026-11-14T23:00:00Z",
                "category": "FIXTURE-A",
                "priority": "1",
                "precinct": 8,
                "source": SOURCE_NAME,
                "call_source": "W911",
                "freshness": "recently_retrieved",
                "limit": 50,
                "offset": 0,
            }
            found = query_incidents(connection, arguments, now)
            self.assertEqual(
                [item["source_record_id"] for item in found["records"]],
                ["FIXTUREHOUSE0001", "FIXTUREFRESH0001"],
            )
            points = map_points(found["records"])
            self.assertEqual([item["source_record_id"] for item in points], ["FIXTUREFRESH0001"])
            self.assertEqual(points[0]["latitude"], 42.35)
            self.assertEqual(points[0]["longitude"], -83.1)
            stale = query_incidents(connection, {"freshness": "stale", "limit": 20, "offset": 0}, now)
            self.assertEqual([item["source_record_id"] for item in stale["records"]], ["FIXTURESTALE0001"])
            self.assertEqual(stale["records"][0]["freshness"], "stale")
            stored_band = query_incidents(connection, {"freshness": "stored", "limit": 20, "offset": 0}, now)
            self.assertEqual([item["source_record_id"] for item in stored_band["records"]], ["FIXTUREBLANK0001"])
            self.assertEqual(map_points(stored_band["records"]), [])
            other_source = query_incidents(connection, {"source": "FIXTURE other source", "limit": 20, "offset": 0}, now)
            self.assertEqual(other_source["total"], 0)
        finally:
            connection.close()

        status, payload = handle("/api/detroit/incidents", {
            "from": "2026-11-14T00:00:00Z",
            "to": "2026-11-14T23:00:00Z",
            "category": "FIXTURE-A",
            "priority": "1",
            "precinct": "8",
            "source": SOURCE_NAME,
            "call_source": "W911",
            "freshness": "recently_retrieved",
        })
        self.assertEqual(status, 200)
        self.assertEqual(payload["source_name"], SOURCE_NAME)
        self.assertEqual(payload["data_type"], DATA_TYPE)
        self.assertEqual(payload["verification"], VERIFICATION)
        self.assertEqual(payload["source_url"], PORTAL_URL)
        self.assertFalse(payload["live_dispatch"])
        self.assertFalse(payload["update_schedule_guaranteed"])
        self.assertIn("does not guarantee", payload["notice"].lower())
        self.assertEqual([item["source_record_id"] for item in payload["points"]], ["FIXTUREFRESH0001"])
        self.assertTrue(all(item["source_name"] == SOURCE_NAME for item in payload["records"]))
        self.assertTrue(LAT_MIN <= payload["points"][0]["latitude"] <= LAT_MAX)
        self.assertTrue(LON_MIN <= payload["points"][0]["longitude"] <= LON_MAX)

    def test_empty_database_is_not_created_by_a_read(self):
        missing = Path(self.tmp.name) / "missing.sqlite"
        os.environ["DETROIT_DB"] = str(missing)
        status, payload = handle("/api/detroit/status", {})
        self.assertEqual(status, 200)
        self.assertFalse(payload["database_present"])
        self.assertFalse(missing.exists())
        status, payload = handle("/api/detroit/incidents", {})
        self.assertEqual(payload["points"], [])
        self.assertEqual(payload["total"], 0)
        with self.assertRaises(ApiError) as caught:
            handle("/api/detroit/incidents", {"limit": "0"})
        self.assertEqual(caught.exception.status, 400)
        with self.assertRaises(ApiError):
            handle("/api/detroit/incidents/FIXTURE404", {})
        self.assertFalse(missing.exists())

    def test_sync_is_idempotent_and_does_not_page_the_archive(self):
        layer = load_layer()
        feature = fixture_feature(incident_id="FIXTURESYNC0001", incident_entry_id=42)
        duplicate = fixture_feature(incident_id="FIXTURESYNC0001", incident_entry_id=42, call_description="ignored duplicate")
        invalid = {"attributes": {"incident_id": "FIXTUREBAD", "called_at": "nope"}}

        class FakeSource:
            def __init__(self):
                self.where = []

            def fetch_metadata(self):
                return layer

            def count(self, where):
                self.where.append(("count", where))
                if where == "1=1":
                    raise DetroitSourceError("timeout", "The Detroit open-data service timed out.")
                return 2

            def iter_features(self, where, page_size):
                self.where.append(("iter", where, page_size))
                yield 1, feature
                yield 1, duplicate
                yield 1, invalid

        now = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
        source = FakeSource()
        result = sync_recent(self.db, lookback_hours=48, client=source, now=now)
        self.assertTrue(result["ok"])
        self.assertEqual(result["upserted"], 1)
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(result["pages"], 1)
        self.assertIsNone(result["service_count"])
        self.assertEqual(result["newest_event_at"], "2016-09-20T09:36:12Z")
        self.assertEqual(result["source_edited_at"], "2023-11-14T22:13:20Z")
        iterated = [item for item in source.where if item[0] == "iter"]
        self.assertEqual(len(iterated), 1)
        self.assertTrue(iterated[0][1].startswith("called_at >= timestamp '"))
        self.assertNotIn(("iter", "1=1", 2000), source.where)
        self.assertIn(("count", "1=1"), source.where)

        feature["attributes"]["call_description"] = "FIXTURE nature revised"
        layer["editingInfo"] = {"dataLastEditDate": 1700003600000}
        later = now + timedelta(hours=2)
        again = sync_recent(self.db, lookback_hours=48, client=source, now=later)
        self.assertEqual(again["upserted"], 1)
        connection = connect(self.db)
        try:
            count = connection.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
            row = connection.execute(
                "SELECT nature, called_at, retrieved_at FROM incidents WHERE source_record_id = ?",
                ("FIXTURESYNC0001",),
            ).fetchone()
            runs = connection.execute("SELECT ok FROM sync_runs").fetchall()
            self.assertEqual(count, 1)
            self.assertEqual(row["nature"], "FIXTURE nature revised")
            self.assertEqual(row["called_at"], "2016-09-20T09:36:12Z")
            self.assertEqual(row["retrieved_at"], isoformat(later))
            self.assertNotEqual(row["called_at"], row["retrieved_at"])
            self.assertEqual([item["ok"] for item in runs], [1, 1])
        finally:
            connection.close()

    def test_unchanged_publication_skips_download_and_keeps_history(self):
        layer = load_layer()
        feature = fixture_feature(incident_id="FIXTURESYNC0001", incident_entry_id=42)

        class FakeSource:
            def __init__(self):
                self.where = []

            def fetch_metadata(self):
                return layer

            def count(self, where):
                self.where.append(("count", where))
                return 1

            def iter_features(self, where, page_size):
                self.where.append(("iter", where, page_size))
                yield 1, feature

        now = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
        source = FakeSource()
        first = sync_recent(self.db, lookback_hours=48, client=source, now=now)
        self.assertFalse(first["unchanged"])
        historical = parse_feature(
            fixture_feature(incident_id="FIXTUREHISTORY01", incident_entry_id=7, call_description="FIXTURE kept"),
            now - timedelta(days=30),
        )
        connection = connect(self.db)
        try:
            upsert_many(connection, [historical])
            connection.commit()
        finally:
            connection.close()
        calls_before = len(source.where)
        later = now + timedelta(minutes=5)
        again = sync_recent(self.db, lookback_hours=48, client=source, now=later)
        self.assertTrue(again["unchanged"])
        self.assertEqual(again["upserted"], 0)
        self.assertEqual(len(source.where), calls_before)
        connection = connect(self.db)
        try:
            rows = connection.execute(
                "SELECT source_record_id, nature, retrieved_at FROM incidents ORDER BY source_record_id"
            ).fetchall()
            self.assertEqual([row["source_record_id"] for row in rows], ["FIXTUREHISTORY01", "FIXTURESYNC0001"])
            kept = rows[0]
            synced = rows[1]
            self.assertEqual(kept["nature"], "FIXTURE kept")
            self.assertEqual(kept["retrieved_at"], isoformat(now - timedelta(days=30)))
            self.assertEqual(synced["retrieved_at"], isoformat(now))
            runs = connection.execute(
                "SELECT ok, upserted, source_edited_at FROM sync_runs ORDER BY id"
            ).fetchall()
            self.assertEqual(len(runs), 2)
            self.assertEqual(runs[1]["ok"], 1)
            self.assertEqual(runs[1]["upserted"], 0)
            self.assertEqual(runs[1]["source_edited_at"], runs[0]["source_edited_at"])
        finally:
            connection.close()
        code, payload = handle("/api/detroit/status", {})
        self.assertEqual(code, 200)
        self.assertFalse(payload["live_dispatch"])
        self.assertEqual(payload["sync_state"], "current")
        self.assertEqual(payload["local_records"], 2)
        self.assertNotEqual(payload["last_checked_at"], payload["source_edited_at"])
        self.assertIn("not live", payload["sync_state_detail"].lower())

    def test_sync_status_separates_publication_from_the_check(self):
        now = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
        success = {
            "ok": 1,
            "finished_at": "2026-11-16T11:58:00Z",
            "started_at": "2026-11-16T11:57:40Z",
            "source_edited_at": "2026-11-15T02:00:00Z",
        }
        latest = dict(success)
        current = describe_sync(latest, success, now, 300)
        self.assertEqual(current["sync_state"], "current")
        self.assertEqual(current["source_edited_at"], "2026-11-15T02:00:00Z")
        self.assertEqual(current["last_checked_at"], "2026-11-16T11:58:00Z")
        self.assertNotEqual(current["source_edited_at"], current["last_checked_at"])
        self.assertIn("not live", current["sync_state_detail"].lower())
        self.assertIn("publication", current["publication_note"].lower())
        delayed = describe_sync(
            {"ok": 1, "finished_at": "2026-11-16T11:30:00Z"},
            {"ok": 1, "finished_at": "2026-11-16T11:30:00Z", "source_edited_at": "2026-11-15T02:00:00Z"},
            now,
            300,
        )
        self.assertEqual(delayed["sync_state"], "delayed")
        stale = describe_sync(
            {"ok": 1, "finished_at": "2026-11-16T04:00:00Z"},
            {"ok": 1, "finished_at": "2026-11-16T04:00:00Z", "source_edited_at": "2026-11-15T02:00:00Z"},
            now,
            300,
        )
        self.assertEqual(stale["sync_state"], "stale")
        failed = describe_sync(
            {"ok": 0, "finished_at": "2026-11-16T11:59:00Z", "started_at": "2026-11-16T11:58:30Z", "error": "timed out"},
            success,
            now,
            300,
        )
        self.assertEqual(failed["sync_state"], "error")
        self.assertEqual(failed["last_success_at"], success["finished_at"])
        self.assertEqual(failed["source_edited_at"], success["source_edited_at"])
        self.assertEqual(failed["last_checked_at"], "2026-11-16T11:59:00Z")
        empty = describe_sync(None, None, now, 300)
        self.assertEqual(empty["sync_state"], "never")
        self.assertEqual(empty["sync_interval_seconds"], 300)

    def test_source_failure_is_recorded_and_a_bad_window_does_not_open_the_database(self):
        class Down:
            def fetch_metadata(self):
                raise DetroitSourceError("unavailable", "The Detroit open-data service is not reachable.")

        with self.assertRaises(DetroitSourceError):
            sync_recent(self.db, lookback_hours=2, client=Down(), now=datetime(2026, 11, 14, tzinfo=timezone.utc))
        connection = connect(self.db)
        try:
            run = connection.execute("SELECT ok, error FROM sync_runs").fetchone()
            count = connection.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
            self.assertEqual(run["ok"], 0)
            self.assertIn("not reachable", run["error"])
            self.assertEqual(count, 0)
        finally:
            connection.close()

        absent = Path(self.tmp.name) / "never.sqlite"
        with self.assertRaises(DetroitSourceError):
            sync_recent(absent, lookback_hours=0)
        self.assertFalse(absent.exists())
        with self.assertRaises(DetroitSourceError):
            sync_recent(absent, lookback_hours=24 * 14 + 1)
        self.assertFalse(absent.exists())

    def test_retry_after_is_capped_and_invalid_values_are_ignored(self):
        self.assertEqual(retry_after_seconds("120"), 120)
        self.assertEqual(retry_after_seconds("99999"), 3600)
        self.assertIsNone(retry_after_seconds("soon"))
        self.assertIsNone(retry_after_seconds(None))
        header = Message()
        header["Retry-After"] = "45"
        opener, _calls = scripted([
            urllib.error.HTTPError(
                "https://services2.arcgis.com/fixture",
                429,
                "limited",
                header,
                io.BytesIO(b""),
            )
        ])
        client = DetroitClient(opener=opener, timeout=1)
        with self.assertRaises(DetroitSourceError) as caught:
            client.fetch_metadata()
        self.assertEqual(caught.exception.code, "rate_limited")
        self.assertEqual(caught.exception.retry_after, 45)

    def test_reconciliation_widens_only_after_a_change_and_keeps_older_rows(self):
        layer = load_layer()
        feature = fixture_feature(incident_id="FIXTURESYNC0001", incident_entry_id=42)

        class FakeSource:
            def __init__(self):
                self.where = []

            def fetch_metadata(self):
                return layer

            def count(self, where):
                self.where.append(("count", where))
                return 1

            def iter_features(self, where, page_size):
                self.where.append(("iter", where, page_size))
                yield 1, feature

        now = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
        source = FakeSource()
        first = sync_recent(
            self.db,
            lookback_hours=48,
            client=source,
            now=now,
            reconcile_hours=168,
            reconcile_interval_seconds=6 * 60 * 60,
        )
        self.assertTrue(first["reconciled"])
        self.assertEqual(first["lookback_hours"], 168)
        self.assertIn("timestamp '2026-11-09 12:00:00'", [item for item in source.where if item[0] == "iter"][-1][1])
        historical = parse_feature(
            fixture_feature(incident_id="FIXTUREHISTORY01", incident_entry_id=7, call_description="FIXTURE kept"),
            now - timedelta(days=40),
        )
        connection = connect(self.db)
        try:
            upsert_many(connection, [historical])
            connection.commit()
        finally:
            connection.close()

        layer["editingInfo"] = {"dataLastEditDate": 1700001800000}
        narrow = sync_recent(
            self.db,
            lookback_hours=48,
            client=source,
            now=now + timedelta(hours=1),
            reconcile_hours=168,
            reconcile_interval_seconds=10 ** 9,
        )
        self.assertFalse(narrow["reconciled"])
        self.assertEqual(narrow["lookback_hours"], 48)
        self.assertIn("timestamp '2026-11-14 13:00:00'", [item for item in source.where if item[0] == "iter"][-1][1])

        calls_before = len(source.where)
        skipped = sync_recent(
            self.db,
            lookback_hours=48,
            client=source,
            now=now + timedelta(hours=2),
            reconcile_hours=168,
            reconcile_interval_seconds=10 ** 9,
        )
        self.assertTrue(skipped["unchanged"])
        self.assertFalse(skipped["reconciled"])
        self.assertEqual(len(source.where), calls_before)

        layer["editingInfo"] = {"dataLastEditDate": 1700003600000}
        feature["attributes"]["call_description"] = "FIXTURE nature revised"
        wide = sync_recent(
            self.db,
            lookback_hours=48,
            client=source,
            now=now + timedelta(hours=8),
            reconcile_hours=168,
            reconcile_interval_seconds=0,
        )
        self.assertTrue(wide["reconciled"])
        self.assertIn("timestamp '2026-11-09 20:00:00'", [item for item in source.where if item[0] == "iter"][-1][1])
        connection = connect(self.db)
        try:
            rows = connection.execute(
                "SELECT source_record_id, nature, retrieved_at FROM incidents ORDER BY source_record_id"
            ).fetchall()
            self.assertEqual([row["source_record_id"] for row in rows], ["FIXTUREHISTORY01", "FIXTURESYNC0001"])
            self.assertEqual(rows[0]["nature"], "FIXTURE kept")
            self.assertEqual(rows[0]["retrieved_at"], isoformat(now - timedelta(days=40)))
            self.assertEqual(rows[1]["nature"], "FIXTURE nature revised")
            deleted = connection.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
            self.assertEqual(deleted, 2)
        finally:
            connection.close()

    def test_cli_rejects_a_bad_window_without_writing_a_database(self):
        absent = Path(self.tmp.name) / "cli.sqlite"
        result = subprocess.run(
            [sys.executable, "-m", "detroit.sync", "--lookback-hours", "0", "--db", str(absent)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("lookback", result.stderr.lower())
        self.assertFalse(absent.exists())

    def test_map_frame_matches_the_privacy_box(self):
        script = (ROOT / "static" / "detroit.js").read_text()
        self.assertIn("west: -83.4", script)
        self.assertIn("east: -82.8", script)
        self.assertIn("south: 42.2", script)
        self.assertIn("north: 42.5", script)
        self.assertEqual((LON_MIN, LON_MAX, LAT_MIN, LAT_MAX), (-83.4, -82.8, 42.2, 42.5))
        self.assertNotIn("innerHTML", script)
        self.assertNotIn("real-time", script.lower())
        self.assertNotIn("realtime", script.lower())
        self.assertNotIn("unpkg.com", script.lower())
        self.assertNotIn("cdnjs", script.lower())
        self.assertNotIn("tile.openstreetmap.org", script.lower())
        self.assertIn('referrerPolicy: "origin"', script)
        self.assertIn("https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png?key=", script)
        self.assertIn("https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", script)
        self.assertIn("scrollWheelZoom: true", script)
        self.assertIn("Powered by Esri", script)
        self.assertIn("function clusterGroups", script)
        self.assertIn("Zoomed out, nearby calls combine", script)
        self.assertIn("911 line", script)
        self.assertIn("No call audio is published with this incident.", script)
        self.assertIn("https://www.openstreetmap.org/copyright", script)
        self.assertIn("https://carto.com/attribution/", script)
        self.assertIn("SOURCE: ", script)
        self.assertIn("DATA TYPE: ", script)
        self.assertIn("VERIFICATION: ", script)
        self.assertIn("var DETROIT_REFRESH_MS = 30000;", script)
        self.assertIn("Last checked", script)
        self.assertIn("Last successful synchronization", script)
        self.assertIn("City publication timestamp", script)
        refresh = script[script.index("function refreshDetroitQuiet"):script.index("async function renderDetroit")]
        markers = script[script.index("function replaceDetroitMarkers"):script.index("async function refreshDetroitQuiet")]
        self.assertNotIn("placeDetroitMap", refresh)
        self.assertNotIn("fitBounds", refresh)
        self.assertNotIn("location.hash", refresh)
        self.assertNotIn("filterForm(", refresh)
        self.assertIn("replaceDetroitMarkers", refresh)
        self.assertIn("detroitQuery", refresh)
        self.assertIn("detroitFiltersBusy()", refresh)
        self.assertNotIn("placeDetroitMap", markers)
        self.assertNotIn("fitBounds", markers)
        self.assertIn("removeLayer", markers)
        self.assertIn("getCenter", markers)
        self.assertIn("getZoom", markers)
        app = (ROOT / "static" / "app.js").read_text()
        self.assertIn("stopDetroitRefresh", app)
        harness = subprocess.run(
            ["node", str(ROOT / "tests" / "detroit_refresh_harness.js")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(harness.returncode, 0, harness.stderr or harness.stdout)
        self.assertIn("refresh-ok", harness.stdout)


if __name__ == "__main__":
    unittest.main()
