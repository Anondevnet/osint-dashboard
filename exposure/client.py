"""Small Shodan API client. The key is added here and must not be returned."""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

API_ORIGIN = "https://api.shodan.io"
MAX_BYTES = 1_000_000
_KEY_CHARS = re.compile(r"^[A-Za-z0-9]{16,128}$")

Fetch = Callable[[str, float], tuple[int, bytes]]
Clock = Callable[[], float]
Sleeper = Callable[[float], None]


class ShodanError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class HostUnknown(Exception):
    """Shodan has no public record for this address."""


def configured_key(raw: str | None) -> str:
    """Return a usable key, or an empty string when the value is missing or malformed."""
    key = (raw or "").strip()
    if not _KEY_CHARS.fullmatch(key):
        return ""
    return key


def scrub(value, key: str):
    """Drop the credential from any structure before it can reach a caller."""
    if isinstance(value, str):
        return value.replace(key, "") if key else value
    if isinstance(value, list):
        return [scrub(item, key) for item in value]
    if isinstance(value, dict):
        cleaned = {}
        for name, item in value.items():
            if str(name).lower() == "key":
                continue
            cleaned[name] = scrub(item, key)
        return cleaned
    return value


def default_fetch(url: str, timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "osint-dashboard-v2"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(MAX_BYTES)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(8000)
    except TimeoutError as exc:
        raise TimeoutError("timeout") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise TimeoutError("timeout") from exc
        raise OSError("unreachable") from exc


class ShodanClient:
    """One paced GET client. Callers never receive the request URL."""

    def __init__(
        self,
        key: str,
        fetch: Fetch | None = None,
        timeout: float = 12.0,
        min_interval: float = 1.1,
        now: Clock | None = None,
        sleep: Sleeper | None = None,
    ):
        self._key = key
        self._fetch = fetch or default_fetch
        self._timeout = timeout
        self._min_interval = min_interval
        self._now = now or time.monotonic
        self._sleep = sleep or time.sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def info(self) -> dict:
        return self._request("/api-info", {})

    def host(self, ip: str) -> dict | None:
        try:
            return self._request("/shodan/host/" + urllib.parse.quote(ip, safe=":"), {})
        except HostUnknown:
            return None

    def search(self, query: str) -> dict:
        return self._request("/shodan/host/search", {"query": query, "page": "1"})

    def resolve(self, hostname: str) -> list[str]:
        payload = self._request("/dns/resolve", {"hostnames": hostname})
        found = payload.get(hostname) if isinstance(payload, dict) else None
        if isinstance(found, str):
            return [found]
        if isinstance(found, list):
            return [item for item in found if isinstance(item, str)]
        return []

    def _request(self, path: str, params: dict) -> dict:
        self._pace()
        query = dict(params)
        query["key"] = self._key
        url = API_ORIGIN + path + "?" + urllib.parse.urlencode(query)
        try:
            status, body = self._fetch(url, self._timeout)
        except TimeoutError as exc:
            raise ShodanError(504, "Shodan did not answer before the timeout.") from exc
        except OSError as exc:
            raise ShodanError(502, "The dashboard could not reach the Shodan API.") from exc
        if status == 401:
            raise ShodanError(401, "Shodan rejected the API key. Check SHODAN_API_KEY and start the server again.")
        if status == 404 and path.startswith("/shodan/host/") and path != "/shodan/host/search":
            raise HostUnknown()
        if status == 429:
            raise ShodanError(429, "Shodan is limiting requests for this key. Wait a minute and try again.")
        if status >= 400:
            raise ShodanError(502, "Shodan returned an error. Nothing new was saved.")
        try:
            payload = json.loads(body.decode("utf-8", errors="replace") or "null")
        except json.JSONDecodeError as exc:
            raise ShodanError(502, "Shodan returned a response this dashboard could not read.") from exc
        if not isinstance(payload, dict):
            raise ShodanError(502, "Shodan returned a response this dashboard could not read.")
        if payload.get("error"):
            raise ShodanError(502, "Shodan returned an error. Nothing new was saved.")
        return scrub(payload, self._key)

    def _pace(self) -> None:
        with self._lock:
            wait = self._next - self._now()
            if wait > 0:
                self._sleep(wait)
            self._next = self._now() + self._min_interval
