"""Contract tests for the read-only dashboard server. No network installs."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


JOB_ID = "abc123def456"

FIXTURE = {
    "/health": {"status": "ok"},
    "/stats": {"jobs": 2, "findings": 3, "entities": 4, "evidence": 5, "relationships": 6},
    "/jobs": [
        {
            "id": JOB_ID,
            "target": "example.com",
            "target_type": "domain",
            "requested_tools": ["subfinder"],
            "status": "completed",
            "passive": 1,
            "created_at": "2026-01-01T00:00:00Z",
            "started_at": "2026-01-01T00:00:01Z",
            "completed_at": "2026-01-01T00:03:00Z",
            "errors": [],
            "results": [{"tool": "subfinder", "status": "completed", "finding_count": 2}],
            "stages": [
                {"stage": "DISCOVERY", "status": "completed"},
                {"stage": "INFRASTRUCTURE", "status": "completed"},
                {"stage": "IDENTITY", "status": "skipped"},
                {"stage": "CORRELATION", "status": "completed"},
                {"stage": "REPORTING", "status": "pending"},
            ],
        }
    ],
    "/jobs/" + JOB_ID: {
        "id": JOB_ID,
        "target": "example.com",
        "target_type": "domain",
        "requested_tools": ["subfinder"],
        "status": "completed",
        "passive": 1,
        "created_at": "2026-01-01T00:00:00Z",
        "started_at": "2026-01-01T00:00:01Z",
        "completed_at": "2026-01-01T00:03:00Z",
        "errors": [],
        "results": [{"tool": "subfinder", "status": "completed", "finding_count": 2}],
        "tool_results": [{
            "tool": "subfinder",
            "status": "completed",
            "finding_count": 2,
            "duration_seconds": 1.5,
            "exit_code": 0,
            "error": None,
        }],
        "stages": [
            {"stage": "DISCOVERY", "status": "completed"},
            {"stage": "REPORTING", "status": "pending"},
        ],
    },
    "/investigations/" + JOB_ID + "/summary": {
        "job_id": JOB_ID,
        "target": "example.com",
        "statement": "Insufficient public evidence to associate this account with a named public identity.",
        "overall_confidence": "LOW",
        "status": "UNRESOLVED",
        "why": "No source explicitly names a public identity.",
        "recommendations": [{
            "check_text": "Compare the public username across independent services.",
            "why": "the same handle on independent services is corroborating, not proof",
            "passive": True,
        }],
        "summary": {"identity_candidates": [], "contradictions": [], "strong_evidence": []},
        "source_quality": {"tier_1": 0, "tier_2": 1, "tier_3": 0, "tier_4": 0},
        "temporal": {"notes": ["No dates were published."], "conflicts": []},
    },
    "/tools": [
        {
            "name": "subfinder",
            "description": "Passive subdomain discovery",
            "capabilities": ["subdomain_discovery"],
            "supported_target_types": ["domain"],
            "available": True,
            "version": "test",
        },
        {
            "name": "holehe",
            "description": "Check which sites an email address is registered on",
            "capabilities": ["email_account_discovery"],
            "supported_target_types": ["email"],
            "available": False,
            "version": None,
        },
    ],
    "/findings": [
        {
            "id": 1,
            "job_id": JOB_ID,
            "tool": "subfinder",
            "target": "example.com",
            "entity_type": "subdomain",
            "value": "www.example.com",
            "relationship_type": "HAS_SUBDOMAIN",
            "dst_type": "domain",
            "dst_value": "example.com",
            "confidence": "MEDIUM",
            "service_level": "OBSERVED",
            "evidence_id": 9,
            "created_at": "2026-01-01T00:01:00Z",
        }
    ],
    "/evidence": [
        {
            "id": 9,
            "tool": "subfinder",
            "target": "example.com",
            "timestamp": "2026-01-01T00:01:00Z",
            "confidence": "MEDIUM",
            "service_level": "OBSERVED",
            "exit_code": 0,
            "error": None,
        }
    ],
    "/evidence/9": {
        "id": 9,
        "tool": "subfinder",
        "target": "example.com",
        "timestamp": "2026-01-01T00:01:00Z",
        "confidence": "MEDIUM",
        "service_level": "OBSERVED",
        "exit_code": 0,
        "command": "subfinder -d example.com -silent",
        "stdout_excerpt": "www.example.com",
        "stderr_excerpt": "",
        "error": None,
        "findings": [],
        "why_linked": [],
    },
    "/entities": [
        {
            "id": 4,
            "type": "domain",
            "value": "example.com",
            "display_name": None,
            "confidence": "MEDIUM",
            "first_seen": "2026-01-01T00:00:00Z",
            "last_seen": "2026-01-01T00:03:00Z",
        }
    ],
    "/entities/4": {
        "id": 4,
        "type": "domain",
        "value": "example.com",
        "confidence": "MEDIUM",
        "first_seen": "2026-01-01T00:00:00Z",
        "last_seen": "2026-01-01T00:03:00Z",
    },
    "/entities/4/relationships": [],
    "/entities/4/evidence": [],
}


class UpstreamState:
    def __init__(self):
        self.requests = []
        self.delay = 0.0
        self.lock = threading.Lock()


STATE = UpstreamState()


class UpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        path, _, query = self.path.partition("?")
        with STATE.lock:
            STATE.requests.append((self.command, path, query))
            delay = STATE.delay
        if delay:
            time.sleep(delay)
        if path not in FIXTURE:
            body = json.dumps({"detail": "not found"}).encode("utf-8")
            self._send(404, body)
            return
        body = json.dumps(FIXTURE[path]).encode("utf-8")
        self._send(200, body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or "0")
        self.rfile.read(length)
        with STATE.lock:
            STATE.requests.append((self.command, self.path, ""))
        body = json.dumps({"id": "should-not-run"}).encode("utf-8")
        self._send(201, body)

    def _send(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
        upstream.daemon_threads = True
        cls.upstream = upstream
        cls.upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        cls.upstream_thread.start()
        upstream_url = "http://127.0.0.1:%s" % upstream.server_address[1]
        cls.httpd = server.make_server("127.0.0.1", 0, upstream_url)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%s" % cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.upstream.shutdown()
        cls.httpd.server_close()
        cls.upstream.server_close()

    def setUp(self):
        with STATE.lock:
            STATE.requests.clear()
            STATE.delay = 0.0
        self.httpd.RequestHandlerClass.timeout_override = None
        self._detroit_db = os.environ.get("DETROIT_DB")
        self._detroit_dir = tempfile.mkdtemp(prefix="detroit-server-test-")
        os.environ["DETROIT_DB"] = str(Path(self._detroit_dir) / "absent.sqlite")
        self._carto_key = os.environ.get("CARTO_BASEMAP_KEY")
        os.environ.pop("CARTO_BASEMAP_KEY", None)
        self._shodan_key = os.environ.get("SHODAN_API_KEY")
        self._shodan_db = os.environ.get("SHODAN_DB")
        os.environ.pop("SHODAN_API_KEY", None)
        os.environ["SHODAN_DB"] = str(Path(self._detroit_dir) / "shodan.sqlite")

    def tearDown(self):
        if self._detroit_db is None:
            os.environ.pop("DETROIT_DB", None)
        else:
            os.environ["DETROIT_DB"] = self._detroit_db
        if self._carto_key is None:
            os.environ.pop("CARTO_BASEMAP_KEY", None)
        else:
            os.environ["CARTO_BASEMAP_KEY"] = self._carto_key
        if self._shodan_key is None:
            os.environ.pop("SHODAN_API_KEY", None)
        else:
            os.environ["SHODAN_API_KEY"] = self._shodan_key
        if self._shodan_db is None:
            os.environ.pop("SHODAN_DB", None)
        else:
            os.environ["SHODAN_DB"] = self._shodan_db
        shutil.rmtree(self._detroit_dir, ignore_errors=True)

    def open(self, path, method="GET", data=None, headers=None):
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                body = response.read()
                return response.status, dict(response.headers), body
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def test_pages_and_static_assets(self):
        status, headers, body = self.open("/")
        self.assertEqual(status, 200)
        page = body.decode("utf-8")
        self.assertIn("OSINT Dashboard", page)
        self.assertIn('data-nav="overview"', page)
        self.assertIn('data-nav="search"', page)
        self.assertIn('data-nav="findings"', page)
        self.assertIn('data-nav="evidence"', page)
        self.assertIn('data-nav="detroit"', page)
        self.assertIn('data-nav="shotspotter-detroit"', page)
        self.assertIn("SHOTSPOTTER DETROIT", page)
        self.assertIn('data-nav="exposure"', page)
        self.assertIn('data-nav="analytics"', page)
        self.assertIn('data-nav="reports"', page)
        self.assertIn('data-nav="settings"', page)
        self.assertIn("Internet Exposure", page)
        self.assertIn("Shodan Intelligence", page)
        self.assertLess(page.index("/static/leaflet.js"), page.index("/static/detroit.js"))
        self.assertLess(page.index("/static/detroit.js"), page.index("/static/shotspotter.js"))
        self.assertLess(page.index("/static/shotspotter.js"), page.index("/static/exposure.js"))
        self.assertLess(page.index("/static/exposure.js"), page.index("/static/command.js"))
        self.assertLess(page.index("/static/command.js"), page.index("/static/app.js"))
        self.assertIn("/static/leaflet.css", page)
        self.assertIn('content="no-referrer"', page)
        self.assertNotIn("basemaps.cartocdn.com", page)
        self.assertNotIn("unpkg.com", page.lower())
        self.assertNotIn("cdnjs", page.lower())
        self.assertEqual(headers.get("Content-Security-Policy"), server.HTML_CSP)
        self.assertIn("img-src 'self' data: https://basemaps.cartocdn.com", headers.get("Content-Security-Policy", ""))
        self.assertIn("https://services.arcgisonline.com", headers.get("Content-Security-Policy", ""))
        self.assertIn("script-src 'self'", headers.get("Content-Security-Policy", ""))
        self.assertNotIn("script-src 'self' data:", headers.get("Content-Security-Policy", ""))
        self.assertNotIn("unsafe-inline", headers.get("Content-Security-Policy", ""))
        self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")
        self.assertNotIn("http://", page.lower().replace("http-equiv", ""))
        for name in ("app.js", "results.js", "detect.js", "detroit.js", "shotspotter.js", "exposure.js", "command.js", "leaflet.js", "leaflet.css", "style.css", "favicon.svg"):
            asset_status, _, asset = self.open("/static/" + name)
            self.assertEqual(asset_status, 200, name)
            self.assertTrue(asset)
        leaflet = (ROOT / "static" / "leaflet.js").read_text(encoding="utf-8")
        self.assertIn("1.9.4", leaflet[:300])

    def test_copy_explains_confidence_without_starting_scans(self):
        script = (ROOT / "static" / "app.js").read_text()
        self.assertIn("Treat it as a lead, not a fact.", script)
        self.assertIn("Several independent sources agree.", script)
        self.assertIn("An investigator confirmed this.", script)
        self.assertIn("does not start a scan", script)
        self.assertIn("Still marked running", script)
        self.assertIn("more than a day", script)
        self.assertNotIn("innerHTML", script)
        self.assertNotIn("/api/jobs\",", script)
        detroit = (ROOT / "static" / "detroit.js").read_text()
        self.assertNotIn("innerHTML", detroit)
        shotspotter = (ROOT / "static" / "shotspotter.js").read_text()
        self.assertNotIn("innerHTML", shotspotter)
        self.assertIn("No acoustic audio is published", shotspotter)
        self.assertIn("not a live alert feed", shotspotter)
        self.assertNotIn("<audio", shotspotter)
        self.assertIn("This is not a live dispatch feed.", detroit)
        self.assertIn("Update schedule is not guaranteed.", detroit)
        exposure = (ROOT / "static" / "exposure.js").read_text()
        self.assertNotIn("innerHTML", exposure)
        self.assertNotIn("api.shodan.io", exposure)
        self.assertIn("203.0.113.10", exposure)
        self.assertIn("Illustration only", exposure)
        self.assertIn("not proof that it can be used", exposure)

    def test_healthz_is_local_and_read_only(self):
        status, _, body = self.open("/healthz")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["read_only"])
        self.assertTrue(payload["upstream"].startswith("http://127.0.0.1:"))

    def test_get_proxy_returns_saved_counts_and_records_the_path(self):
        status, _, body = self.open("/api/stats")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["findings"], 3)
        status, _, body = self.open("/api/findings?limit=500&job_id=" + JOB_ID)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)[0]["value"], "www.example.com")
        status, _, body = self.open("/api/investigations/%s/summary" % JOB_ID)
        self.assertEqual(status, 200)
        self.assertIn("Insufficient public evidence", json.loads(body)["statement"])
        with STATE.lock:
            paths = [item[1] for item in STATE.requests]
        self.assertIn("/stats", paths)
        self.assertIn("/findings", paths)
        self.assertNotIn("POST", [item[0] for item in STATE.requests])

    FEEDBACK_HEADERS = {"Content-Type": "application/json", "X-OSINT-Feedback": "1"}

    def test_verdict_post_is_validated_and_forwarded(self):
        body = json.dumps({"finding_ids": [1, 2], "verdict": "correct"}).encode("utf-8")
        status, _, _ = self.open("/api/findings/verdicts", method="POST", data=body, headers=self.FEEDBACK_HEADERS)
        self.assertEqual(status, 201)  # the fake upstream answers 201
        with STATE.lock:
            self.assertEqual(STATE.requests, [("POST", "/findings/verdicts", "")])

    def test_learning_reset_needs_confirm(self):
        status, _, _ = self.open("/api/learning/reset", method="POST", data=b"{}", headers=self.FEEDBACK_HEADERS)
        self.assertEqual(status, 400)
        status, _, _ = self.open("/api/learning/reset", method="POST",
                                 data=b'{"confirm": true}', headers=self.FEEDBACK_HEADERS)
        self.assertEqual(status, 201)

    def test_feedback_post_refuses_bad_requests(self):
        good = json.dumps({"finding_ids": [1], "verdict": "wrong"}).encode("utf-8")
        cases = [
            ({"Content-Type": "application/json"}, good, 403),                      # no dashboard header
            (dict(self.FEEDBACK_HEADERS, Origin="http://evil.example"), good, 403),  # cross-site
            ({"X-OSINT-Feedback": "1", "Content-Type": "text/plain"}, good, 415),
            (self.FEEDBACK_HEADERS, b"not json", 400),
            (self.FEEDBACK_HEADERS, json.dumps({"finding_ids": [], "verdict": "wrong"}).encode(), 400),
            (self.FEEDBACK_HEADERS, json.dumps({"finding_ids": ["1"], "verdict": "wrong"}).encode(), 400),
            (self.FEEDBACK_HEADERS, json.dumps({"finding_ids": [True], "verdict": "wrong"}).encode(), 400),
            (self.FEEDBACK_HEADERS, json.dumps({"finding_ids": [1], "verdict": "run"}).encode(), 400),
            (self.FEEDBACK_HEADERS, json.dumps({"finding_ids": [1], "verdict": "wrong", "target": "x"}).encode(), 400),
            (self.FEEDBACK_HEADERS, b"{" + b" " * 20000 + b"}", 413),
        ]
        for headers, data, want in cases:
            status, _, _ = self.open("/api/findings/verdicts", method="POST", data=data, headers=headers)
            self.assertEqual(status, want, (headers, data[:60]))
        with STATE.lock:
            self.assertEqual(STATE.requests, [])

    def test_other_writes_stay_blocked(self):
        for path in ("/api/jobs", "/api/jobs/smart", "/api/learning", "/api/findings/verdicts?x=1"):
            status, _, _ = self.open(path, method="POST", data=b"{}", headers=self.FEEDBACK_HEADERS)
            self.assertIn(status, (400, 405), path)
        status, _, _ = self.open("/api/findings/verdicts", method="PUT", data=b"{}", headers=self.FEEDBACK_HEADERS)
        self.assertEqual(status, 405)
        with STATE.lock:
            self.assertEqual(STATE.requests, [])

    def test_smart_and_learning_reads_are_allowed(self):
        for path in ("/api/detect?q=example.com&type=domain&also_username=true", "/api/learning",
                     "/api/learning/verdicts?job_id=" + JOB_ID, "/api/learning/export"):
            status, _, body = self.open(path)
            self.assertEqual(status, 404, path)  # allowed through; the fake upstream has no fixture
            self.assertIn(b"not found", body)

    def test_post_never_reaches_the_command_center(self):
        status, headers, body = self.open(
            "/api/jobs",
            method="POST",
            data=json.dumps({"target": "example.com", "target_type": "domain"}).encode("utf-8"),
        )
        self.assertEqual(status, 405)
        payload = json.loads(body)
        self.assertEqual(payload["error"], "read_only")
        self.assertIn("GET", headers.get("Allow", ""))
        with STATE.lock:
            self.assertEqual(STATE.requests, [])

    def test_blocked_routes_are_not_forwarded(self):
        for path in (
            "/api/jobs/%s/cancel" % JOB_ID,
            "/api/reports/generate?job_id=%s" % JOB_ID,
            "/api/providers/holehe/lookup",
            "/api/../server.py",
            "/static/../server.py",
            "/api/graph",
        ):
            status, _, _ = self.open(path)
            self.assertIn(status, (400, 404), path)
        with STATE.lock:
            self.assertEqual(STATE.requests, [])

    def test_invalid_limit_is_rejected(self):
        status, _, body = self.open("/api/findings?limit=0")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "bad_request")
        status, _, _ = self.open("/api/findings?limit=5001")
        self.assertEqual(status, 400)
        with STATE.lock:
            self.assertEqual(STATE.requests, [])

    def test_upstream_miss_is_passed_through(self):
        status, _, body = self.open("/api/jobs/missing000001")
        self.assertEqual(status, 404)
        self.assertIn("not found", body.decode("utf-8"))

    def test_offline_upstream_returns_502(self):
        httpd = server.make_server("127.0.0.1", 0, "http://127.0.0.1:9")
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = "http://127.0.0.1:%s" % httpd.server_address[1]
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(base + "/api/health", timeout=3)
            self.assertEqual(caught.exception.code, 502)
            payload = json.loads(caught.exception.read())
            self.assertEqual(payload["error"], "command_center_offline")
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_slow_upstream_times_out(self):
        STATE.delay = 1.5
        self.httpd.RequestHandlerClass.timeout_override = 0.2
        status, _, body = self.open("/api/health")
        self.assertEqual(status, 504)
        self.assertEqual(json.loads(body)["error"], "timeout")

    def test_shodan_status_stays_local_without_a_key(self):
        before = list(STATE.requests)
        status, headers, body = self.open("/api/shodan/status")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertFalse(payload["configured"])
        self.assertFalse(payload["connected"])
        self.assertFalse(payload["active_scan"])
        self.assertIn("SHODAN_API_KEY", payload["message"])
        self.assertNotIn("api.shodan.io", body.decode("utf-8"))
        self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")
        self.assertEqual(STATE.requests, before)
        status, _, body = self.open("/api/shodan/lookup?ip=203.0.113.10")
        self.assertEqual(status, 400)
        self.assertNotIn("203.0.113.10", body.decode("utf-8"))
        status, _, body = self.open("/api/shodan/search?type=domain&value=example.com%20port:22&authorized=yes")
        self.assertEqual(status, 400)
        self.assertNotIn("port:22", body.decode("utf-8"))
        status, _, _ = self.open("/api/shodan/lookup", method="POST", data=b"{}")
        self.assertEqual(status, 405)

    def test_detroit_routes_stay_local(self):
        status, _, body = self.open("/api/detroit/status")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["source_name"], "City of Detroit — Police Serviced 911 Calls")
        self.assertFalse(payload["live_dispatch"])
        self.assertFalse(payload["update_schedule_guaranteed"])
        self.assertFalse(payload["database_present"])
        self.assertEqual(payload["sync_state"], "never")
        self.assertIn("last_checked_at", payload)
        self.assertIn("source_edited_at", payload)
        self.assertIn("lookback_hours", payload)
        self.assertIn("window_note", payload)
        status, _, body = self.open("/api/detroit/runs")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["runs"], [])
        self.assertIn("publication", payload["publication_note"].lower())
        self.assertNotIn("live dispatch", payload["sync_state_detail"].lower())
        status, _, body = self.open("/api/detroit/incidents?from=not-a-time")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "bad_request")
        status, _, body = self.open("/api/detroit/incidents?phone=5550100")
        self.assertEqual(status, 400)
        status, headers, body = self.open("/api/detroit/sync", method="POST", data=b"{}")
        self.assertEqual(status, 405)
        self.assertEqual(json.loads(body)["error"], "read_only")
        self.assertIn("GET", headers.get("Allow", ""))
        with STATE.lock:
            self.assertEqual(STATE.requests, [])
        self.assertFalse(Path(os.environ["DETROIT_DB"]).exists())

    def test_basemap_stays_off_without_a_key(self):
        status, headers, body = self.open("/api/detroit/basemap")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertFalse(payload["enabled"])
        self.assertNotIn("tile_url", payload)
        self.assertNotIn("key=", body.decode("utf-8"))
        self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")
        status, _, body = self.open("/api/detroit/basemap?key=not-allowed")
        self.assertEqual(status, 400)
        self.assertNotIn("not-allowed", body.decode("utf-8"))

    def test_basemap_uses_only_the_environment_key(self):
        os.environ["CARTO_BASEMAP_KEY"] = "local-v2-test-key"
        status, _, body = self.open("/api/detroit/basemap")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["enabled"])
        self.assertEqual(
            payload["tile_url"],
            "https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png?key=local-v2-test-key",
        )
        self.assertNotIn("local-v2-test-key", (ROOT / "server.py").read_text(encoding="utf-8"))
        self.assertNotIn("local-v2-test-key", (ROOT / "static" / "detroit.js").read_text(encoding="utf-8"))
        self.assertNotIn("local-v2-test-key", (ROOT / "static" / "index.html").read_text(encoding="utf-8"))
        os.environ["CARTO_BASEMAP_KEY"] = "abc+def/ghij="
        status, _, body = self.open("/api/detroit/basemap")
        payload = json.loads(body)
        self.assertTrue(payload["enabled"])
        self.assertEqual(
            payload["tile_url"],
            "https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png?key=abc%2Bdef%2Fghij%3D",
        )
        self.assertNotIn("abc+def/ghij=", body.decode("utf-8"))
        os.environ["CARTO_BASEMAP_KEY"] = "bad&extra=1"
        status, _, body = self.open("/api/detroit/basemap")
        payload = json.loads(body)
        self.assertFalse(payload["enabled"])
        self.assertNotIn("bad&extra", body.decode("utf-8"))
        self.assertNotIn("tile_url", payload)

    def test_upstream_must_stay_local(self):
        with self.assertRaises(server.DashboardError):
            server.local_upstream("http://example.com")
        with self.assertRaises(server.DashboardError):
            server.local_upstream("https://127.0.0.1:8000")


if __name__ == "__main__":
    unittest.main()
