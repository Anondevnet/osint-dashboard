"""Background Detroit sync tests. They use a temporary database and a fake source."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TESTS))

from detroit.client import DetroitSourceError  # noqa: E402
from detroit.records import isoformat, parse_feature  # noqa: E402
from detroit.scheduler import (  # noqa: E402
    SyncScheduler,
    interval_seconds,
    make_sync_job,
    reconcile_hours,
    reconcile_interval_seconds,
    start_from_environment,
)
from detroit.store import connect, upsert_many  # noqa: E402
from test_detroit import fixture_feature, load_layer  # noqa: E402


class SchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "scheduler.sqlite"
        self._db = os.environ.get("DETROIT_DB")
        self._interval = os.environ.get("DETROIT_SYNC_INTERVAL_SECONDS")
        os.environ["DETROIT_DB"] = str(self.db)
        os.environ.pop("DETROIT_SYNC_INTERVAL_SECONDS", None)

    def tearDown(self) -> None:
        if self._db is None:
            os.environ.pop("DETROIT_DB", None)
        else:
            os.environ["DETROIT_DB"] = self._db
        if self._interval is None:
            os.environ.pop("DETROIT_SYNC_INTERVAL_SECONDS", None)
        else:
            os.environ["DETROIT_SYNC_INTERVAL_SECONDS"] = self._interval
        self.tmp.cleanup()

    def test_interval_defaults_and_disabled_checks_do_not_create_a_database(self) -> None:
        self.assertEqual(interval_seconds(None), 300)
        self.assertEqual(interval_seconds("0"), 0)
        self.assertEqual(interval_seconds("15"), 60)
        self.assertEqual(interval_seconds("nope"), 300)
        self.assertEqual(reconcile_hours(None), 168)
        self.assertIsNone(reconcile_hours("0"))
        self.assertEqual(reconcile_hours("9000"), 24 * 14)
        self.assertEqual(reconcile_interval_seconds(None), 21600)
        self.assertEqual(reconcile_interval_seconds("0"), 0)
        os.environ["DETROIT_SYNC_INTERVAL_SECONDS"] = "0"
        self.assertIsNone(start_from_environment())
        self.assertFalse(self.db.exists())

    def test_overlapping_run_is_skipped(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def slow():
            entered.set()
            release.wait(3)
            return {"ok": True}

        scheduler = SyncScheduler(300, slow)
        worker = threading.Thread(target=scheduler.run_once)
        worker.start()
        self.assertTrue(entered.wait(1))
        skipped = scheduler.run_once()
        self.assertTrue(skipped["skipped"])
        self.assertEqual(scheduler.overlaps, 1)
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())

    def test_start_returns_while_a_check_is_still_running(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def slow():
            entered.set()
            release.wait(3)
            return {"ok": True}

        scheduler = SyncScheduler(300, slow)
        started = time.monotonic()
        scheduler.start()
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertTrue(entered.wait(1))
        release.set()
        scheduler.stop()

    def test_network_failure_is_recorded_and_a_later_check_still_runs(self) -> None:
        now = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
        historical = parse_feature(
            fixture_feature(incident_id="FIXTUREHISTORY01", incident_entry_id=7, call_description="FIXTURE kept"),
            now - timedelta(days=20),
        )
        connection = connect(self.db)
        try:
            upsert_many(connection, [historical])
            connection.commit()
        finally:
            connection.close()

        class Flaky:
            def __init__(self):
                self.calls = 0

            def fetch_metadata(self):
                self.calls += 1
                if self.calls == 1:
                    raise DetroitSourceError("unavailable", "The Detroit open-data service is not reachable.")
                return load_layer()

            def count(self, where):
                return 0

            def iter_features(self, where, page_size):
                if False:
                    yield 1, {}

        flaky = Flaky()

        def wait(_seconds):
            return flaky.calls >= 2

        scheduler = SyncScheduler(300, make_sync_job(self.db, 48, flaky), wait=wait)
        scheduler.start()
        self.assertIsNotNone(scheduler._thread)
        scheduler._thread.join(3)
        connection = connect(self.db)
        try:
            runs = connection.execute("SELECT ok, error FROM sync_runs ORDER BY id").fetchall()
            row = connection.execute(
                "SELECT nature, retrieved_at FROM incidents WHERE source_record_id = ?",
                ("FIXTUREHISTORY01",),
            ).fetchone()
            self.assertEqual([item["ok"] for item in runs], [0, 1])
            self.assertIn("not reachable", runs[0]["error"])
            self.assertEqual(row["nature"], "FIXTURE kept")
            self.assertEqual(row["retrieved_at"], isoformat(now - timedelta(days=20)))
        finally:
            connection.close()
        scheduler.stop()

    def test_retry_after_skips_the_next_check_without_holding_the_lock(self) -> None:
        calls = {"n": 0}

        def limited():
            calls["n"] += 1
            return {"ok": False, "error": "rate_limited", "retry_after": 3600, "message": "wait"}

        scheduler = SyncScheduler(300, limited)
        first = scheduler.run_once()
        self.assertEqual(first["retry_after"], 3600)
        skipped = scheduler.run_once()
        self.assertEqual(skipped["reason"], "retry_after")
        self.assertTrue(skipped["skipped"])
        self.assertEqual(calls["n"], 1)
        self.assertEqual(scheduler.overlaps, 0)
        self.assertFalse(scheduler._lock.locked())

    def test_sync_job_returns_retry_after_and_keeps_history(self) -> None:
        class Limited:
            def fetch_metadata(self):
                raise DetroitSourceError("rate_limited", "The Detroit open-data service is rate limiting requests.", retry_after=90)

        result = make_sync_job(self.db, 48, Limited())()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "rate_limited")
        self.assertEqual(result["retry_after"], 90)
        connection = connect(self.db)
        try:
            run = connection.execute("SELECT ok, error FROM sync_runs").fetchone()
            count = connection.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
            self.assertEqual(run["ok"], 0)
            self.assertEqual(count, 0)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
