"""Shodan exposure tests. They use a fake API client and a temporary database."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
import urllib.parse
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import exposure.api as exposure_api  # noqa: E402
from exposure.api import ApiError, handle  # noqa: E402
from exposure.client import ShodanClient, ShodanError  # noqa: E402
from exposure.store import read_cache, summary  # noqa: E402

KEY = "TESTSHODANKEY1234"
PUBLIC_IP = "1.2.3.4"


def fail_if_secret(blob: str) -> None:
    if KEY in blob or "bad key" in blob:
        raise AssertionError("response included credential material")


class Clock:
    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


class ShodanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="shodan-test-")
        self._key = os.environ.get("SHODAN_API_KEY")
        self._db = os.environ.get("SHODAN_DB")
        os.environ["SHODAN_DB"] = str(Path(self.tmp) / "shodan.sqlite")
        os.environ.pop("SHODAN_API_KEY", None)
        self.calls: list[str] = []
        self.original_client = exposure_api.client_for
        exposure_api.client_for = self.client_for

    def tearDown(self) -> None:
        exposure_api.client_for = self.original_client
        if self._key is None:
            os.environ.pop("SHODAN_API_KEY", None)
        else:
            os.environ["SHODAN_API_KEY"] = self._key
        if self._db is None:
            os.environ.pop("SHODAN_DB", None)
        else:
            os.environ["SHODAN_DB"] = self._db
        shutil.rmtree(self.tmp, ignore_errors=True)

    def client_for(self, key: str) -> ShodanClient:
        self.assertEqual(key, KEY)
        return ShodanClient(key, fetch=self.fetch, min_interval=0, sleep=lambda _seconds: None)

    def fetch(self, url: str, timeout: float) -> tuple[int, bytes]:
        self.assertGreater(timeout, 0)
        parsed = urllib.parse.urlsplit(url)
        self.assertEqual(parsed.scheme + "://" + parsed.netloc, "https://api.shodan.io")
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(query.get("key"), [KEY])
        self.calls.append(parsed.path)
        if self.mode == "timeout":
            raise TimeoutError("timeout")
        if self.mode == "down":
            raise OSError("failed " + url)
        if self.mode == "unauthorized":
            return 401, ('{"error":"invalid %s"}' % KEY).encode("utf-8")
        if self.mode == "limited":
            return 429, b'{"error":"rate limit"}'
        if self.mode == "bad-json":
            return 200, b"not-json"
        if parsed.path == "/api-info":
            return 200, b'{"plan":"oss","query_credits":4,"scan_credits":0,"unlocked":true}'
        if parsed.path == "/shodan/host/" + PUBLIC_IP:
            return 200, json.dumps(self.host_payload()).encode("utf-8")
        if parsed.path == "/shodan/host/search":
            self.assertEqual(query.get("page"), ["1"])
            self.last_query = query.get("query", [""])[0]
            self.assertNotIn("*", self.last_query)
            return 200, json.dumps({"matches": [self.match_payload()], "total": 90}).encode("utf-8")
        if parsed.path == "/dns/resolve":
            return 200, json.dumps({query["hostnames"][0]: PUBLIC_IP}).encode("utf-8")
        return 404, b'{"error":"missing"}'

    def host_payload(self) -> dict:
        return {
            "ip_str": PUBLIC_IP,
            "hostnames": ["Example.Example", "not a name", "www.example.example"],
            "country_name": "United States",
            "city": "Example City",
            "isp": "Example Hosting",
            "org": "Example Org",
            "asn": "AS64500",
            "last_update": "2026-11-01T12:00:00.000000",
            "vulns": {
                "CVE-2024-12345": {"cvss": 9.8, "summary": "Reported by Shodan. " + KEY, "verified": False},
                "not-a-cve": {"cvss": 10},
            },
            "data": [
                {
                    "port": 443,
                    "transport": "tcp",
                    "product": "nginx",
                    "version": "1.18.0",
                    "timestamp": "2026-11-01T12:00:00.000000",
                    "data": "banner " + KEY,
                    "_shodan": {"module": "https"},
                },
                {"port": True, "product": "ignored"},
            ],
        }

    def match_payload(self) -> dict:
        return {
            "ip_str": PUBLIC_IP,
            "hostnames": ["www.example.example"],
            "org": "Example Org",
            "isp": "Example Hosting",
            "asn": "AS64500",
            "location": {"country_name": "United States", "city": "Example City", "latitude": 42.1, "longitude": -83.1},
            "port": 80,
            "transport": "tcp",
            "product": "Apache httpd",
            "version": "2.4.41",
            "timestamp": "2026-10-01T00:00:00Z",
            "data": "HTTP banner",
            "vulns": ["CVE-2021-44228"],
        }

    def test_missing_and_malformed_keys_do_not_call_shodan(self) -> None:
        self.mode = "unauthorized"
        status, payload = handle("/api/shodan/status", {})
        self.assertEqual(status, 200)
        self.assertFalse(payload["configured"])
        self.assertEqual(self.calls, [])
        fail_if_secret(json.dumps(payload))
        os.environ["SHODAN_API_KEY"] = "bad key"
        status, payload = handle("/api/shodan/status", {})
        self.assertFalse(payload["configured"])
        self.assertEqual(self.calls, [])
        fail_if_secret(json.dumps(payload))

    def test_env_file_is_used_only_when_the_process_has_no_key(self) -> None:
        from envfile import load_env

        path = Path(self.tmp) / "local.env"
        path.write_text('# comment\nSHODAN_API_KEY="%s"\nNOT_A_REAL_SETTING=hidden\n' % KEY)
        os.chmod(path, 0o644)
        os.environ["SHODAN_API_KEY"] = "OTHERSHODANKEY999"
        skipped = load_env(path)
        self.assertEqual(skipped["applied"], [])
        self.assertEqual(os.environ["SHODAN_API_KEY"], "OTHERSHODANKEY999")
        self.assertNotIn("NOT_A_REAL_SETTING", os.environ)
        os.environ.pop("SHODAN_API_KEY", None)
        applied = load_env(path)
        self.assertEqual(applied["applied"], ["SHODAN_API_KEY"])
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.mode = "ok"
        status, payload = handle("/api/shodan/status", {})
        self.assertEqual(status, 200)
        self.assertTrue(payload["configured"])
        self.assertNotIn("key_length", payload)
        blob = json.dumps(payload)
        fail_if_secret(blob)
        self.assertNotIn("OTHERSHODANKEY999", blob)
        page = (ROOT / "static" / "exposure.js").read_text()
        html = (ROOT / "static" / "index.html").read_text()
        self.assertNotIn(KEY, page)
        self.assertNotIn(KEY, html)
        self.assertNotIn("OTHERSHODANKEY999", page)

    def test_status_uses_api_info_and_hides_the_key(self) -> None:
        self.mode = "ok"
        os.environ["SHODAN_API_KEY"] = KEY
        status, payload = handle("/api/shodan/status", {})
        self.assertEqual(status, 200)
        self.assertTrue(payload["connected"])
        self.assertEqual(payload["plan"], "oss")
        self.assertEqual(payload["query_credits"], 4)
        self.assertNotIn("unlocked", payload)
        fail_if_secret(json.dumps(payload))
        self.assertEqual(self.calls, ["/api-info"])
        self.calls.clear()
        _, cached = handle("/api/shodan/status", {})
        self.assertTrue(cached["status_from_cache"])
        self.assertEqual(self.calls, [])

    def test_lookup_normalizes_a_host_and_caches_the_second_call(self) -> None:
        self.mode = "ok"
        os.environ["SHODAN_API_KEY"] = KEY
        status, payload = handle("/api/shodan/lookup", {"ip": PUBLIC_IP, "authorized": "yes"})
        self.assertEqual(status, 200)
        self.assertEqual(self.calls, ["/shodan/host/" + PUBLIC_IP])
        asset = payload["assets"][0]
        self.assertEqual(asset["ip"], PUBLIC_IP)
        self.assertEqual(asset["hostnames"], ["example.example", "www.example.example"])
        self.assertEqual(asset["ports"], [443])
        self.assertEqual(asset["services"][0]["name"], "nginx")
        self.assertEqual(asset["vulnerabilities"][0]["cve"], "CVE-2024-12345")
        self.assertEqual(asset["vulnerabilities"][0]["severity"], "critical")
        self.assertFalse(asset["vulnerabilities"][0]["shodan_verified"])
        self.assertEqual(asset["review"], "critical")
        self.assertNotIn("latitude", asset)
        fail_if_secret(json.dumps(payload))
        self.assertNotIn(KEY, asset["services"][0]["banner"])
        self.calls.clear()
        _, again = handle("/api/shodan/lookup", {"ip": PUBLIC_IP, "authorized": "yes"})
        self.assertTrue(again["cached"])
        self.assertEqual(self.calls, [])
        counts = summary()
        self.assertEqual(counts["assets"], 1)
        self.assertEqual(counts["vulnerability_reviews"], 1)
        self.assertEqual(counts["version_reviews"], 1)
        self.assertIsNotNone(counts["last_sync"])

    def test_domain_search_is_built_by_the_server(self) -> None:
        self.mode = "ok"
        os.environ["SHODAN_API_KEY"] = KEY
        status, payload = handle(
            "/api/shodan/search",
            {"type": "domain", "value": "Example.Example.", "authorized": "yes"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["value"], "example.example")
        self.assertEqual(payload["resolved_addresses"], [PUBLIC_IP])
        self.assertEqual(payload["assets"][0]["services"][0]["product"], "Apache httpd")
        self.assertNotIn("latitude", json.dumps(payload))
        self.assertEqual(self.last_query, 'hostname:"example.example"')
        self.assertEqual(self.calls, ["/shodan/host/search", "/dns/resolve"])
        _, org = handle(
            "/api/shodan/search",
            {"type": "organization", "value": "Example Hosting", "authorized": "yes"},
        )
        self.assertEqual(org["kind"], "organization")
        self.assertEqual(self.last_query, 'org:"Example Hosting"')
        cached = read_cache("domain:example.example", 900)
        self.assertIsNotNone(cached)
        _, history = handle("/api/shodan/history", {})
        domain = next(item for item in history["history"] if item["kind"] == "domain")
        self.assertFalse(domain["cached"])
        self.assertEqual(domain["value"], "example.example")

    def test_validation_blocks_broad_and_unauthorized_queries(self) -> None:
        self.mode = "ok"
        os.environ["SHODAN_API_KEY"] = KEY
        rejected = [
            ("/api/shodan/lookup", {"ip": PUBLIC_IP}),
            ("/api/shodan/lookup", {"ip": "203.0.113.10", "authorized": "yes"}),
            ("/api/shodan/lookup", {"ip": "10.1.1.1", "authorized": "yes"}),
            ("/api/shodan/lookup", {"ip": "198.18.0.0/24", "authorized": "yes"}),
            ("/api/shodan/search", {"type": "domain", "value": "*.example.com", "authorized": "yes"}),
            ("/api/shodan/search", {"type": "domain", "value": "example.com port:22", "authorized": "yes"}),
            ("/api/shodan/search", {"type": "organization", "value": "*", "authorized": "yes"}),
            ("/api/shodan/search", {"type": "organization", "value": 'Org "port:22"', "authorized": "yes"}),
            ("/api/shodan/search", {"type": "domain", "value": "example.com", "authorized": "yes", "page": "2"}),
            ("/api/shodan/host/1.1.1.1", {"authorized": "yes"}),
        ]
        for path, query in rejected:
            with self.subTest(path=path, query=query):
                with self.assertRaises(ApiError) as caught:
                    handle(path, query)
                self.assertIn(caught.exception.status, (400, 404))
                fail_if_secret(caught.exception.message)
        self.assertEqual(self.calls, [])

    def test_invalid_credentials_timeout_and_rate_limit(self) -> None:
        os.environ["SHODAN_API_KEY"] = KEY
        self.mode = "unauthorized"
        with self.assertRaises(ApiError) as caught:
            handle("/api/shodan/lookup", {"ip": PUBLIC_IP, "authorized": "yes"})
        self.assertEqual(caught.exception.status, 401)
        fail_if_secret(caught.exception.message)
        self.mode = "limited"
        with self.assertRaises(ApiError) as limited:
            handle("/api/shodan/lookup", {"ip": "1.2.3.5", "authorized": "yes"})
        self.assertEqual(limited.exception.status, 429)
        self.mode = "timeout"
        with self.assertRaises(ApiError) as timed:
            handle("/api/shodan/lookup", {"ip": "1.2.3.6", "authorized": "yes"})
        self.assertEqual(timed.exception.status, 504)
        self.mode = "down"
        with self.assertRaises(ApiError) as down:
            handle("/api/shodan/lookup", {"ip": "1.2.3.7", "authorized": "yes"})
        self.assertEqual(down.exception.status, 502)
        fail_if_secret(down.exception.message)
        self.mode = "bad-json"
        with self.assertRaises(ApiError) as bad:
            handle("/api/shodan/lookup", {"ip": "1.2.3.8", "authorized": "yes"})
        self.assertEqual(bad.exception.status, 502)
        self.assertIn("could not read", bad.exception.message)

    def test_client_spaces_requests_and_scrubs_errors(self) -> None:
        clock = Clock()

        def fetch(url: str, timeout: float) -> tuple[int, bytes]:
            return 200, b'{"plan":"oss","query_credits":1,"scan_credits":0}'

        client = ShodanClient(KEY, fetch=fetch, min_interval=1.1, now=clock.now, sleep=clock.sleep)
        client.info()
        client.info()
        self.assertEqual(clock.slept, [1.1])
        def leak(url: str, timeout: float) -> tuple[int, bytes]:
            raise OSError(url)

        leaking = ShodanClient(KEY, fetch=leak, min_interval=0, sleep=lambda _seconds: None)
        try:
            leaking.info()
        except ShodanError as exc:
            fail_if_secret(exc.message)
            self.assertNotIn("api.shodan.io", exc.message)
        else:
            self.fail("unreachable Shodan should raise")


if __name__ == "__main__":
    unittest.main()
