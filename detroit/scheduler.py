"""Background checks for the published Detroit 911 layer.

One check runs at a time. The server keeps answering requests while a check
is in flight. A check does not mean the city published new calls.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

from detroit.client import DetroitSourceError
from detroit.sync import DEFAULT_LOOKBACK_HOURS, MAX_LOOKBACK_HOURS, sync_recent

DEFAULT_INTERVAL_SECONDS = 300
MIN_INTERVAL_SECONDS = 60
DEFAULT_RECONCILE_HOURS = 168.0
DEFAULT_RECONCILE_INTERVAL_SECONDS = 6 * 60 * 60


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
        return float(str(raw).strip())
    except ValueError:
        return float(DEFAULT_LOOKBACK_HOURS)


def reconcile_hours(raw: str | None) -> float | None:
    """Hours for an occasional wider pass. Zero disables it. Above 14 days clamps to 14 days."""
    if raw is None or str(raw).strip() == "":
        return DEFAULT_RECONCILE_HOURS
    try:
        value = float(str(raw).strip())
    except ValueError:
        return DEFAULT_RECONCILE_HOURS
    if value <= 0:
        return None
    return min(value, float(MAX_LOOKBACK_HOURS))


def reconcile_interval_seconds(raw: str | None) -> int:
    """How long after a wide pass before another one is allowed. Zero allows one on every change."""
    if raw is None or str(raw).strip() == "":
        return DEFAULT_RECONCILE_INTERVAL_SECONDS
    text = str(raw).strip()
    if not text.isdigit():
        return DEFAULT_RECONCILE_INTERVAL_SECONDS
    return int(text)


def make_sync_job(
    db_path: Path | None,
    hours: float,
    client=None,
    reconcile_hours_value: float | None = None,
    reconcile_interval: int | None = None,
):
    """Return a callable that records source failures and does not raise them."""
    told = False

    def run() -> dict:
        nonlocal told
        try:
            return sync_recent(
                db_path=db_path,
                lookback_hours=hours,
                client=client,
                reconcile_hours=reconcile_hours_value,
                reconcile_interval_seconds=reconcile_interval,
            )
        except DetroitSourceError as exc:
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
        self._thread = threading.Thread(target=self._loop, name="detroit-sync", daemon=True)
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
    """Start the background checker unless the interval is 0."""
    interval = interval_seconds(os.environ.get("DETROIT_SYNC_INTERVAL_SECONDS"))
    if interval == 0:
        return None
    scheduler = SyncScheduler(
        interval,
        make_sync_job(
            None,
            lookback_hours(os.environ.get("DETROIT_LOOKBACK_HOURS")),
            reconcile_hours_value=reconcile_hours(os.environ.get("DETROIT_RECONCILE_HOURS")),
            reconcile_interval=reconcile_interval_seconds(
                os.environ.get("DETROIT_RECONCILE_INTERVAL_SECONDS")
            ),
        ),
    )
    return scheduler.start()
