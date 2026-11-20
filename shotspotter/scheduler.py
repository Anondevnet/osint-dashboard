"""Background checks for the published Detroit ShotSpotter CAD layer.

One check runs at a time. A check does not mean a new gunshot occurred.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

from shotspotter.client import ShotspotterSourceError
from shotspotter.sync import DEFAULT_LOOKBACK_HOURS, MAX_LOOKBACK_HOURS, sync_recent

DEFAULT_INTERVAL_SECONDS = 60
MIN_INTERVAL_SECONDS = 60


def interval_seconds(raw: str | None) -> int:
    """Return the check interval. Zero disables checks. Values below 60 seconds become 60."""
    if raw is None or str(raw).strip() == "":
        return DEFAULT_INTERVAL_SECONDS
    text = str(raw).strip()
    if not text.isdigit():
        return DEFAULT_INTERVAL_SECONDS
    value = int(text)
    if value == 0:
        return 0
    if value < MIN_INTERVAL_SECONDS:
        return MIN_INTERVAL_SECONDS
    return value


def lookback_hours(raw: str | None) -> float:
    if raw is None or str(raw).strip() == "":
        return float(DEFAULT_LOOKBACK_HOURS)
    try:
        value = float(str(raw).strip())
    except ValueError:
        return float(DEFAULT_LOOKBACK_HOURS)
    if value <= 0:
        return float(DEFAULT_LOOKBACK_HOURS)
    return min(value, float(MAX_LOOKBACK_HOURS))


def make_sync_job(db_path: Path | None, hours: float, client=None):
    told = False

    def run() -> dict:
        nonlocal told
        try:
            return sync_recent(db_path=db_path, lookback_hours=hours, client=client)
        except ShotspotterSourceError as exc:
            if exc.code == "bad_window" and not told:
                print(exc.message, file=sys.stderr)
                told = True
            payload = {"ok": False, "error": exc.code, "message": exc.message}
            if exc.retry_after is not None:
                payload["retry_after"] = exc.retry_after
            return payload

    return run


class SyncScheduler:
    def __init__(self, interval: int, run, wait=None):
        self.interval = interval
        self._run = run
        self._wait = wait or self._stop_wait
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._not_before = 0.0
        self.overlaps = 0

    def _stop_wait(self, seconds: float) -> bool:
        return self._stop.wait(seconds)

    def _remember_retry(self, result: dict) -> None:
        if result.get("error") != "rate_limited":
            return
        wait = result.get("retry_after")
        if isinstance(wait, bool) or not isinstance(wait, (int, float)) or wait <= 0:
            return
        self._not_before = time.monotonic() + float(wait)

    def run_once(self) -> dict:
        if time.monotonic() < self._not_before:
            return {"ok": False, "skipped": True, "reason": "retry_after"}
        if not self._lock.acquire(blocking=False):
            self.overlaps += 1
            return {"ok": False, "skipped": True, "reason": "overlap"}
        try:
            result = self._run()
        finally:
            self._lock.release()
        if isinstance(result, dict):
            self._remember_retry(result)
        return result

    def start(self) -> "SyncScheduler":
        if self._thread is not None:
            return self
        self._thread = threading.Thread(target=self._loop, name="shotspotter-sync", daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 5) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                pass
            if self._wait(self.interval):
                return


def start_from_environment() -> SyncScheduler | None:
    interval = interval_seconds(os.environ.get("SHOTSPOTTER_SYNC_INTERVAL_SECONDS"))
    if interval == 0:
        return None
    scheduler = SyncScheduler(
        interval,
        make_sync_job(None, lookback_hours(os.environ.get("SHOTSPOTTER_LOOKBACK_HOURS"))),
    )
    return scheduler.start()
