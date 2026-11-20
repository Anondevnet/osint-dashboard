#!/usr/bin/env python3
"""Read-only OSINT Dashboard v2.

Serves the dashboard on 127.0.0.1:8010 and forwards a fixed set of GET
requests to the existing command center. It never forwards a request that
could start a scan, write a report, or change saved records.
"""

from __future__ import annotations

import errno
import http.client
import json
import os
import re
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from detroit.api import ApiError, handle as detroit_handle
from exposure.api import ApiError as ExposureError, handle as exposure_handle
from shotspotter.api import ApiError as ShotspotterError
from shotspotter.api import export_csv as shotspotter_csv
from shotspotter.api import handle as shotspotter_handle

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DEFAULT_UPSTREAM = "http://127.0.0.1:8000"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8010
MAX_PROXY_BYTES = 32 * 1024 * 1024
MAX_QUERY_LENGTH = 4000

STATIC_FILES = {
    "app.js": "text/javascript; charset=utf-8",
    "results.js": "text/javascript; charset=utf-8",
    "detect.js": "text/javascript; charset=utf-8",
    "detroit.js": "text/javascript; charset=utf-8",
    "shotspotter.js": "text/javascript; charset=utf-8",
    "exposure.js": "text/javascript; charset=utf-8",
    "command.js": "text/javascript; charset=utf-8",
    "leaflet.js": "text/javascript; charset=utf-8",
    "leaflet.css": "text/css; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
    "favicon.svg": "image/svg+xml",
}

DETROIT_QUERY_KEYS = {
    "from",
    "to",
    "category",
    "priority",
    "precinct",
    "call_source",
    "source",
    "freshness",
    "limit",
    "offset",
}

SHOTSPOTTER_QUERY_KEYS = {
    "from",
    "to",
    "classification",
    "q",
    "neighborhood",
    "zip_code",
    "precinct",
    "west",
    "south",
    "east",
    "north",
    "limit",
    "offset",
    "format",
    "freshness",
}

_JOB_ID = r"[A-Za-z0-9_-]{1,80}"
_INT_ID = r"[0-9]{1,12}"
_TOOL = r"[A-Za-z0-9_-]{1,40}"

EXACT_PATHS = {
    "/health",
    "/stats",
    "/tools",
    "/jobs",
    "/entities",
    "/findings",
    "/evidence",
    "/detect",
    "/learning",
    "/learning/verdicts",
    "/learning/export",
}

PATH_PATTERNS = (
    re.compile(rf"^/tools/{_TOOL}$"),
    re.compile(rf"^/jobs/{_JOB_ID}$"),
    re.compile(rf"^/entities/{_INT_ID}$"),
    re.compile(rf"^/entities/{_INT_ID}/(?:relationships|evidence)$"),
    re.compile(rf"^/evidence/{_INT_ID}$"),
    re.compile(rf"^/investigations/{_JOB_ID}/summary$"),
)

ALLOWED_QUERY_KEYS = {"q", "type", "limit", "offset", "job_id", "tool", "service_level", "also_username"}

# The only writes this dashboard passes on: an investigator's verdict on a
# finding, and resetting the learning stats. Neither can start a scan.
FEEDBACK_VERDICTS = {"correct", "wrong", "unsure", "clear"}
MAX_FEEDBACK_BYTES = 16384


def clean_feedback(path: str, payload: object) -> dict:
    """Validate and rebuild a feedback body; anything else is refused."""
    if not isinstance(payload, dict):
        raise DashboardError(400, "bad_request", "Send a JSON object.")
    if path == "/api/findings/verdicts":
        ids = payload.get("finding_ids")
        verdict = payload.get("verdict")
        if (
            not isinstance(ids, list)
            or not 1 <= len(ids) <= 500
            or not all(isinstance(i, int) and not isinstance(i, bool) and 0 < i < 2**53 for i in ids)
            or verdict not in FEEDBACK_VERDICTS
            or set(payload) - {"finding_ids", "verdict"}
        ):
            raise DashboardError(400, "bad_request", "That verdict is not valid.")
        return {"finding_ids": ids, "verdict": verdict}
    if path == "/api/learning/reset":
        if payload != {"confirm": True}:
            raise DashboardError(400, "bad_request", "Send confirm: true to reset learning.")
        return {"confirm": True}
    raise DashboardError(404, "not_available", "That route is not available.")


FEEDBACK_PATHS = {"/api/findings/verdicts", "/api/learning/reset"}

# data: is Leaflet's one-pixel image used to cancel a tile request. Scripts stay self-only.
HTML_CSP = (
    "default-src 'self'; "
    "style-src 'self'; "
    "script-src 'self'; "
    "img-src 'self' data: https://basemaps.cartocdn.com https://services.arcgisonline.com https://server.arcgisonline.com; "
    "connect-src 'self'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

# CARTO Voyager raster tiles. The key is an environment variable, never a source literal.
VOYAGER_TILE_TEMPLATE = "https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png?key="
_BASEMAP_KEY = re.compile(r"^[A-Za-z0-9._~+/=-]{8,200}$")


def basemap_config() -> dict:
    """Tile template for the Detroit map. The key stays out of the repository."""
    key = os.environ.get("CARTO_BASEMAP_KEY", "").strip()
    if not _BASEMAP_KEY.fullmatch(key):
        return {
            "enabled": False,
            "message": (
                "Street tiles are off until CARTO_BASEMAP_KEY is set to a dedicated CARTO basemap key."
            ),
        }
    return {
        "enabled": True,
        "tile_url": VOYAGER_TILE_TEMPLATE + urllib.parse.quote(key, safe=""),
    }


class DashboardError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def local_upstream(url: str) -> urllib.parse.SplitResult:
    """Accept only a local HTTP command center. Rejects open-proxy targets."""
    parsed = urllib.parse.urlsplit(url.strip())
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "http"
        or host not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise DashboardError(
            500,
            "bad_upstream",
            "The command center URL must be a local http://127.0.0.1 address with no path.",
        )
    return parsed


def allowed_upstream_path(path: str) -> bool:
    if path in EXACT_PATHS:
        return True
    return any(pattern.fullmatch(path) for pattern in PATH_PATTERNS)


def clean_api_path(request_target: str) -> tuple[str, str]:
    """Return the upstream path and a normalized query string."""
    if len(request_target) > 6000:
        raise DashboardError(400, "bad_request", "That request is too long.")
    raw_path, separator, raw_query = request_target.partition("?")
    if separator and len(raw_query) > MAX_QUERY_LENGTH:
        raise DashboardError(400, "bad_request", "That search is too long.")
    if any(ord(char) < 32 for char in request_target):
        raise DashboardError(400, "bad_request", "That request contains invalid characters.")
    lowered = raw_path.lower()
    if (
        "\\" in raw_path
        or ".." in raw_path
        or raw_path.startswith("//")
        or "%" in raw_path
        or "%2e" in lowered
        or "%2f" in lowered
        or "%5c" in lowered
    ):
        raise DashboardError(404, "not_available", "That route is not available.")
    if not raw_path.startswith("/api/"):
        raise DashboardError(404, "not_available", "That route is not available.")
    upstream_path = raw_path[4:]
    if not upstream_path.startswith("/") or not allowed_upstream_path(upstream_path):
        raise DashboardError(
            404,
            "not_available",
            "That read-only route is not exposed by this dashboard.",
        )
    return upstream_path, normalize_query(raw_query)


def normalize_query(raw_query: str) -> str:
    if not raw_query:
        return ""
    pairs = urllib.parse.parse_qsl(raw_query, keep_blank_values=True, max_num_fields=20)
    cleaned: list[tuple[str, str]] = []
    for key, value in pairs:
        if key not in ALLOWED_QUERY_KEYS:
            raise DashboardError(400, "bad_request", "That filter is not supported.")
        if any(ord(char) < 32 for char in value) or len(value) > 200:
            raise DashboardError(400, "bad_request", "That filter value is not valid.")
        if key in {"limit", "offset"}:
            if not value.isdigit():
                raise DashboardError(400, "bad_request", "Limit and offset must be whole numbers.")
            number = int(value)
            if key == "limit" and not 1 <= number <= 5000:
                raise DashboardError(400, "bad_request", "Limit must be between 1 and 5000.")
            if key == "offset" and number > 1_000_000:
                raise DashboardError(400, "bad_request", "That page offset is too large.")
        cleaned.append((key, value))
    return urllib.parse.urlencode(cleaned)


def safe_media_type(value: str | None) -> str:
    if not value:
        return "application/octet-stream"
    media = value.split(";", 1)[0].strip().lower()
    if not re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", media):
        return "application/octet-stream"
    if media == "application/json" or media.startswith("text/"):
        return media + "; charset=utf-8"
    return media


def timeout_for(path: str) -> float:
    if path.startswith("/investigations/"):
        return 90.0
    return 20.0


def read_limited(response: http.client.HTTPResponse) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        block = response.read(65536)
        if not block:
            return b"".join(chunks)
        total += len(block)
        if total > MAX_PROXY_BYTES:
            raise DashboardError(
                502,
                "response_too_large",
                "The command center response was larger than this dashboard will pass through.",
            )
        chunks.append(block)


def proxy_get(upstream: str, path: str, query: str, timeout: float,
              method: str = "GET", body: bytes | None = None) -> tuple[int, str, bytes]:
    parsed = local_upstream(upstream)
    port = parsed.port or 80
    host = parsed.hostname or "127.0.0.1"
    target = path if not query else path + "?" + query
    connection_host = host if ":" not in host else f"[{host}]"
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        headers = {
            "Accept": "application/json",
            "Host": f"{connection_host}:{port}",
            "Connection": "close",
            "User-Agent": "osint-dashboard-v2",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        conn.request(method, target, body=body, headers=headers)
        response = conn.getresponse()
        status = response.status
        if status in {301, 302, 303, 307, 308}:
            response.close()
            raise DashboardError(
                502,
                "unexpected_redirect",
                "The command center redirected this read, so the dashboard stopped.",
            )
        body = read_limited(response)
        media = safe_media_type(response.getheader("Content-Type"))
        response.close()
        return status, media, body
    except DashboardError:
        raise
    except TimeoutError as exc:
        raise DashboardError(
            504,
            "timeout",
            "The command center did not answer in time.",
        ) from exc
    except OSError as exc:
        raise DashboardError(
            502,
            "command_center_offline",
            "The command center is not accepting connections on "
            + upstream
            + ". Start it, then reload this page.",
        ) from exc
    finally:
        conn.close()


def json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=True).encode("utf-8")


def static_bytes(name: str) -> bytes | None:
    if name not in STATIC_FILES:
        return None
    path = (STATIC_DIR / name).resolve()
    if path.parent != STATIC_DIR.resolve() or not path.is_file():
        return None
    return path.read_bytes()


class DashboardHandler(BaseHTTPRequestHandler):
    upstream = DEFAULT_UPSTREAM
    timeout_override: float | None = None
    server_version = "osint-dashboard-v2"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        path = self.path.split("?", 1)[0]
        sys.stderr.write("[v2] %s %s\n" % (self.command, path))

    def do_HEAD(self) -> None:
        self._route(write_body=False)

    def do_GET(self) -> None:
        self._route(write_body=True)

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] in FEEDBACK_PATHS:
            self._feedback()
            return
        self._reject_write()

    def _feedback(self) -> None:
        path = self.path.split("?", 1)[0]
        try:
            if "?" in self.path:
                raise DashboardError(400, "bad_request", "No query string here.")
            length = self.headers.get("Content-Length") or ""
            if not length.isdigit() or not 0 < int(length) <= MAX_FEEDBACK_BYTES:
                raise DashboardError(413, "too_large", "That request is too large.")
            raw = self.rfile.read(int(length))
            media = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if media != "application/json":
                raise DashboardError(415, "bad_media", "Send JSON.")
            # CSRF guard: a custom header forces a CORS preflight, which this
            # server never answers, and a browser Origin must be this page.
            if self.headers.get("X-OSINT-Feedback") != "1":
                raise DashboardError(403, "forbidden", "Missing dashboard header.")
            origin = self.headers.get("Origin")
            host = self.headers.get("Host") or ""
            if origin is not None and origin not in {"http://" + host}:
                raise DashboardError(403, "forbidden", "Cross-site request refused.")
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise DashboardError(400, "bad_request", "That is not valid JSON.")
            clean = clean_feedback(path, payload)
            status, out_media, body = proxy_get(
                self.upstream, path[len("/api"):], "", 20.0,
                method="POST", body=json.dumps(clean).encode("utf-8"))
            self._send(status, body, out_media)
        except DashboardError as exc:
            self._send(exc.status, json_bytes({"error": exc.code, "message": exc.message}),
                       "application/json; charset=utf-8")

    def do_PUT(self) -> None:
        self._reject_write()

    def do_PATCH(self) -> None:
        self._reject_write()

    def do_DELETE(self) -> None:
        self._reject_write()

    def _reject_write(self) -> None:
        length = self.headers.get("Content-Length")
        if length and length.isdigit():
            size = int(length)
            if 0 < size <= 1_000_000:
                self.rfile.read(size)
        self._send(
            405,
            json_bytes(
                {
                    "error": "read_only",
                    "message": (
                        "This dashboard is read-only. It cannot start scans "
                        "or change saved investigations."
                    ),
                }
            ),
            "application/json; charset=utf-8",
            extra={"Allow": "GET, HEAD"},
        )

    def _route(self, write_body: bool) -> None:
        path = self.path.split("?", 1)[0]
        try:
            if path in {"/", "/index.html"}:
                body = static_bytes("index.html")
                if body is None:
                    index = STATIC_DIR / "index.html"
                    body = index.read_bytes() if index.is_file() else None
                if body is None:
                    raise DashboardError(500, "missing_ui", "The dashboard page is missing.")
                self._send(
                    200,
                    body,
                    "text/html; charset=utf-8",
                    write_body=write_body,
                    extra={"Content-Security-Policy": HTML_CSP},
                )
                return
            if path == "/favicon.ico":
                icon = static_bytes("favicon.svg")
                if icon is None:
                    self._send(204, b"", "image/svg+xml", write_body=write_body)
                    return
                self._send(200, icon, STATIC_FILES["favicon.svg"], write_body=write_body)
                return
            if path == "/healthz":
                self._send(
                    200,
                    json_bytes(
                        {
                            "status": "ok",
                            "service": "osint-dashboard-v2",
                            "read_only": True,
                            "upstream": self.upstream,
                        }
                    ),
                    "application/json; charset=utf-8",
                    write_body=write_body,
                )
                return
            if path.startswith("/static/"):
                name = path[len("/static/") :]
                body = static_bytes(name)
                if body is None:
                    raise DashboardError(404, "not_found", "That file is not part of the dashboard.")
                self._send(200, body, STATIC_FILES[name], write_body=write_body)
                return
            if path.startswith("/api/detroit"):
                self._detroit(write_body)
                return
            if path.startswith("/api/shotspotter-detroit"):
                self._shotspotter(write_body)
                return
            if path.startswith("/api/shodan"):
                self._shodan(write_body)
                return
            if path.startswith("/api/") or path == "/api":
                upstream_path, query = clean_api_path(self.path)
                timeout = timeout_for(upstream_path) if self.timeout_override is None else self.timeout_override
                status, media, body = proxy_get(self.upstream, upstream_path, query, timeout)
                self._send(status, body, media, write_body=write_body)
                return
            raise DashboardError(404, "not_found", "That page does not exist.")
        except DashboardError as exc:
            payload = json_bytes({"error": exc.code, "message": exc.message})
            media = "application/json; charset=utf-8"
            if path in {"/", "/index.html"} and exc.status == 500:
                payload = exc.message.encode("utf-8")
                media = "text/plain; charset=utf-8"
            self._send(exc.status, payload, media, write_body=write_body)

    def _detroit(self, write_body: bool) -> None:
        raw_path, _, raw_query = self.path.partition("?")
        try:
            if raw_path == "/api/detroit/basemap":
                if raw_query:
                    raise ApiError(400, "That filter is not supported.")
                self._send(
                    200,
                    json_bytes(basemap_config()),
                    "application/json; charset=utf-8",
                    write_body=write_body,
                )
                return
            if len(raw_query) > MAX_QUERY_LENGTH:
                raise ApiError(400, "That search is too long.")
            pairs = urllib.parse.parse_qsl(raw_query, keep_blank_values=True, max_num_fields=20)
            query: dict[str, str] = {}
            for key, value in pairs:
                if key not in DETROIT_QUERY_KEYS:
                    raise ApiError(400, "That filter is not supported.")
                if any(ord(char) < 32 for char in value) or len(value) > 200:
                    raise ApiError(400, "That filter value is not valid.")
                query[key] = value
            status, payload = detroit_handle(raw_path, query)
        except ApiError as exc:
            status = exc.status
            payload = {"error": "bad_request" if exc.status < 500 else "detroit_error", "message": exc.message}
        self._send(status, json_bytes(payload), "application/json; charset=utf-8", write_body=write_body)

    def _shotspotter(self, write_body: bool) -> None:
        raw_path, _, raw_query = self.path.partition("?")
        try:
            if len(raw_query) > MAX_QUERY_LENGTH:
                raise ShotspotterError(400, "That search is too long.")
            pairs = urllib.parse.parse_qsl(raw_query, keep_blank_values=True, max_num_fields=20)
            seen: set[str] = set()
            query: dict[str, str] = {}
            for key, value in pairs:
                if key in seen:
                    raise ShotspotterError(400, "That filter is not supported.")
                seen.add(key)
                if key not in SHOTSPOTTER_QUERY_KEYS:
                    raise ShotspotterError(400, "That filter is not supported.")
                if any(ord(char) < 32 for char in value) or len(value) > 200:
                    raise ShotspotterError(400, "That filter value is not valid.")
                query[key] = value
            if query.get("format") not in (None, "", "csv"):
                raise ShotspotterError(400, "That export format is not supported.")
            if raw_path == "/api/shotspotter-detroit/incidents" and query.get("format") == "csv":
                status, text = shotspotter_csv(query)
                self._send(
                    status,
                    text.encode("utf-8"),
                    "text/csv; charset=utf-8",
                    write_body=write_body,
                    extra={"Content-Disposition": 'attachment; filename="shotspotter-detroit.csv"'},
                )
                return
            status, payload = shotspotter_handle(raw_path, query)
        except ShotspotterError as exc:
            status = exc.status
            payload = {
                "error": "rate_limited" if exc.status == 429 else "bad_request" if exc.status < 500 else "shotspotter_error",
                "message": exc.message,
            }
            self._send(status, json_bytes(payload), "application/json; charset=utf-8", write_body=write_body)
            return
        self._send(status, json_bytes(payload), "application/json; charset=utf-8", write_body=write_body)

    def _shodan(self, write_body: bool) -> None:
        raw_path, _, raw_query = self.path.partition("?")
        try:
            if len(raw_query) > MAX_QUERY_LENGTH:
                raise ExposureError(400, "bad_request", "That search is too long.")
            pairs = urllib.parse.parse_qsl(raw_query, keep_blank_values=True, max_num_fields=12)
            seen: set[str] = set()
            query: dict[str, str] = {}
            for key, value in pairs:
                if key in seen:
                    raise ExposureError(400, "bad_request", "That filter is not supported.")
                seen.add(key)
                if any(ord(char) < 32 for char in value) or len(value) > 253:
                    raise ExposureError(400, "bad_request", "That filter value is not valid.")
                query[key] = value
            status, payload = exposure_handle(raw_path, query)
        except ExposureError as exc:
            status = exc.status
            payload = {"error": exc.code, "message": exc.message}
        self._send(status, json_bytes(payload), "application/json; charset=utf-8", write_body=write_body)

    def _send(
        self,
        status: int,
        body: bytes,
        media: str,
        write_body: bool = True,
        extra: dict[str, str] | None = None,
    ) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            if extra:
                for key, value in extra.items():
                    self.send_header(key, value)
            self.end_headers()
            if write_body and self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return


class DashboardServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def make_server(host: str, port: int, upstream: str) -> DashboardServer:
    local_upstream(upstream)
    handler = type(
        "BoundDashboardHandler",
        (DashboardHandler,),
        {"upstream": upstream, "timeout_override": None},
    )
    return DashboardServer((host, port), handler)


def main() -> None:
    import os

    from envfile import load_env

    loaded = load_env(ROOT / ".env")
    if loaded["applied"]:
        print("Loaded local environment: " + ", ".join(loaded["applied"]))

    host = os.environ.get("OSINT_V2_HOST", DEFAULT_HOST)
    upstream = os.environ.get("OSINT_UPSTREAM", DEFAULT_UPSTREAM)
    try:
        port = int(os.environ.get("OSINT_V2_PORT", str(DEFAULT_PORT)))
    except ValueError:
        print("OSINT_V2_PORT must be a whole number.", file=sys.stderr)
        raise SystemExit(1)
    if host not in {"127.0.0.1", "localhost", "::1"}:
        print("Refusing to bind outside localhost.", file=sys.stderr)
        raise SystemExit(1)
    try:
        local_upstream(upstream)
        httpd = make_server(host, port, upstream)
    except DashboardError as exc:
        print(exc.message, file=sys.stderr)
        raise SystemExit(1)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(
                f"Port {port} is already in use. Stop the other process or set OSINT_V2_PORT.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        raise
    from detroit.scheduler import start_from_environment
    from shotspotter.scheduler import start_from_environment as start_shotspotter

    scheduler = start_from_environment()
    shotspotter_scheduler = start_shotspotter()
    print(f"OSINT Dashboard v2  http://{host}:{port}")
    print(f"Read-only proxy     {upstream}")
    print("This server does not start scans.")
    if scheduler is None:
        print("Detroit automatic checks are off.")
    else:
        print(
            "Detroit checks the published layer every %s seconds. Stored calls are not live dispatch."
            % scheduler.interval
        )
    if shotspotter_scheduler is None:
        print("ShotSpotter automatic checks are off.")
    else:
        print(
            "ShotSpotter checks the public CAD extract every %s seconds. Stored rows are not live alerts."
            % shotspotter_scheduler.interval
        )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if scheduler is not None:
            scheduler.stop()
        if shotspotter_scheduler is not None:
            shotspotter_scheduler.stop()
        httpd.server_close()


if __name__ == "__main__":
    main()
