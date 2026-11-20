"""Validate authorized exposure lookups and return plain records."""

from __future__ import annotations

import ipaddress
import os
import re
from typing import Any

from exposure import NOTICE, SOURCE
from exposure.client import ShodanClient, ShodanError, configured_key
from exposure.store import (
    add_history,
    list_history,
    read_cache,
    save_assets,
    set_meta,
    summary,
    utc_now,
    write_cache,
)

_DOMAIN = re.compile(
    r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
_ORG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,&'()-]{1,78}[A-Za-z0-9.)]$")
_CVE = re.compile(r"^CVE-\d{4}-\d{4,7}$")
_STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_ASN = re.compile(r"^AS\d{1,10}$")
RESULT_LIMIT = 25
SEARCH_TTL = 15 * 60
STATUS_TTL = 60
_REVIEW_RANK = {"info": 0, "warning": 1, "critical": 2}
_REVIEW_LABEL = {
    "info": "No review flag",
    "warning": "Review recommended",
    "critical": "Priority review",
}
NOT_CONFIGURED = (
    "Set SHODAN_API_KEY in the environment or in the local .env file, then start the dashboard again. "
    "The key stays on the server and is not sent to the browser."
)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def client_for(key: str) -> ShodanClient:
    return ShodanClient(key)


def handle(path: str, query: dict[str, str]) -> tuple[int, dict]:
    if path == "/api/shodan/status":
        if query:
            raise ApiError(400, "bad_request", "That filter is not supported.")
        return 200, status_payload()
    if path == "/api/shodan/history":
        return 200, {"history": list_history(_history_limit(query))}
    if path == "/api/shodan/lookup":
        return 200, lookup_ip(query)
    if path == "/api/shodan/search":
        return 200, search_authorized(query)
    raise ApiError(404, "not_available", "That exposure route is not available.")


def status_payload() -> dict:
    counts = summary()
    key = configured_key(os.environ.get("SHODAN_API_KEY"))
    payload = {
        "configured": bool(key),
        "connected": False,
        "message": NOT_CONFIGURED,
        "plan": "",
        "query_credits": None,
        "scan_credits": None,
        "last_sync": counts.pop("last_sync"),
        "active_scan": False,
        "counts": counts,
        "source": SOURCE,
    }
    if not key:
        return payload
    cached = read_cache("api-info", STATUS_TTL)
    if cached is None:
        try:
            cached = _public_info(client_for(key).info())
        except ShodanError as exc:
            payload["message"] = exc.message
            return payload
        write_cache("api-info", cached)
        set_meta("last_status_check", utc_now())
        payload["status_from_cache"] = False
    else:
        payload["status_from_cache"] = True
    payload["connected"] = True
    payload["message"] = "The Shodan API key was accepted. Lookups are read-only."
    payload["plan"] = cached.get("plan") or ""
    payload["query_credits"] = cached.get("query_credits")
    payload["scan_credits"] = cached.get("scan_credits")
    return payload


def lookup_ip(query: dict[str, str]) -> dict:
    _require_authorized(query)
    if set(query) - {"ip", "authorized"}:
        raise ApiError(400, "bad_request", "That filter is not supported.")
    ip = _public_ip(query.get("ip", ""))
    return _cached_lookup("ip", ip, "host:" + ip, lambda client: _from_host(client, ip))


def search_authorized(query: dict[str, str]) -> dict:
    _require_authorized(query)
    if set(query) - {"type", "value", "authorized"}:
        raise ApiError(400, "bad_request", "That filter is not supported.")
    kind = query.get("type", "")
    value = query.get("value", "").strip()
    if kind == "domain":
        domain = _domain(value)
        return _cached_lookup(
            "domain",
            domain,
            "domain:" + domain,
            lambda client: _from_search(client, 'hostname:"%s"' % domain, domain),
        )
    if kind == "organization":
        org = _organization(value)
        quoted = org.replace("\\", "\\\\").replace('"', "")
        return _cached_lookup(
            "organization",
            org,
            "org:" + org.casefold(),
            lambda client: _from_search(client, 'org:"%s"' % quoted, ""),
        )
    raise ApiError(400, "bad_request", "Choose a domain or an organization name.")


def _cached_lookup(kind: str, value: str, cache_key: str, load) -> dict:
    cached = read_cache(cache_key, SEARCH_TTL)
    if cached is not None:
        result = _envelope(kind, value, cached, cached=True)
        add_history(kind, value, True, True, len(result["assets"]), "Loaded from the local cache. Shodan was not contacted.")
        return result
    client = _require_client()
    try:
        loaded = load(client)
    except ShodanError as exc:
        add_history(kind, value, False, False, 0, exc.message)
        raise ApiError(exc.status, _code_for(exc.status), exc.message) from exc
    write_cache(cache_key, loaded)
    if loaded["assets"]:
        save_assets(loaded["assets"])
    set_meta("last_sync", utc_now())
    result = _envelope(kind, value, loaded, cached=False)
    add_history(
        kind,
        value,
        True,
        False,
        len(result["assets"]),
        _history_message(kind, len(result["assets"])),
    )
    return result


def _from_host(client: ShodanClient, ip: str) -> dict:
    payload = client.host(ip)
    assets = []
    if payload is not None:
        asset = asset_from_host(payload)
        if asset is not None:
            assets.append(asset)
    return {"assets": assets, "resolved_addresses": [], "truncated": False}


def _from_search(client: ShodanClient, query: str, domain: str) -> dict:
    payload = client.search(query)
    matches = payload.get("matches") if isinstance(payload.get("matches"), list) else []
    assets, truncated = assets_from_matches(matches)
    resolved = []
    if domain:
        try:
            resolved = [item for item in client.resolve(domain) if _is_public(item)]
        except ShodanError:
            resolved = []
    return {"assets": assets, "resolved_addresses": resolved[:10], "truncated": truncated}


def _envelope(kind: str, value: str, loaded: dict, cached: bool) -> dict:
    return {
        "source": SOURCE,
        "verified_by_dashboard": False,
        "notice": NOTICE,
        "cached": cached,
        "kind": kind,
        "value": value,
        "queried_at": utc_now(),
        "truncated": bool(loaded.get("truncated")),
        "resolved_addresses": loaded.get("resolved_addresses") or [],
        "assets": loaded.get("assets") or [],
    }


def _require_client() -> ShodanClient:
    key = configured_key(os.environ.get("SHODAN_API_KEY"))
    if not key:
        raise ApiError(503, "not_configured", NOT_CONFIGURED)
    return client_for(key)


def _require_authorized(query: dict[str, str]) -> None:
    if query.get("authorized") != "yes":
        raise ApiError(
            400,
            "bad_request",
            "Confirm that you own this target or are authorized to assess it.",
        )


def _history_limit(query: dict[str, str]) -> int:
    if set(query) - {"limit"}:
        raise ApiError(400, "bad_request", "That filter is not supported.")
    raw = query.get("limit", "")
    if not raw:
        return 20
    if not raw.isdigit() or not 1 <= int(raw) <= 50:
        raise ApiError(400, "bad_request", "History limit must be a whole number from 1 to 50.")
    return int(raw)


def _public_ip(value: str) -> str:
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError as exc:
        raise ApiError(400, "bad_request", "Enter one public IP address.") from exc
    if not address.is_global:
        raise ApiError(
            400,
            "bad_request",
            "Enter one public IP address. Private, local, and documentation addresses are not looked up.",
        )
    return str(address)


def _is_public(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).is_global
    except ValueError:
        return False


def _domain(value: str) -> str:
    domain = value.strip().lower().rstrip(".")
    if not _DOMAIN.fullmatch(domain):
        raise ApiError(400, "bad_request", "Enter one domain name, without a wildcard or a search operator.")
    return domain


def _organization(value: str) -> str:
    org = " ".join(value.split())
    if not _ORG.fullmatch(org):
        raise ApiError(
            400,
            "bad_request",
            "Enter an organization name of at least 3 characters, without search operators.",
        )
    return org


def _public_info(payload: dict) -> dict:
    plan = payload.get("plan") if isinstance(payload.get("plan"), str) else ""
    return {
        "plan": plan[:40],
        "query_credits": _count(payload.get("query_credits")),
        "scan_credits": _count(payload.get("scan_credits")),
    }


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0:
        return None
    return value


def _code_for(status: int) -> str:
    if status == 401:
        return "unauthorized"
    if status == 429:
        return "rate_limited"
    if status == 504:
        return "timeout"
    if status == 503:
        return "not_configured"
    return "upstream"


def _history_message(kind: str, count: int) -> str:
    if count == 0:
        return "Shodan has no public record for that authorized target."
    noun = "address" if count == 1 else "addresses"
    return "Shodan reported %s %s." % (count, noun)


def asset_from_host(payload: dict) -> dict | None:
    ip = payload.get("ip_str") if isinstance(payload.get("ip_str"), str) else ""
    if not _is_public(ip):
        return None
    services = []
    for banner in payload.get("data") or []:
        if isinstance(banner, dict):
            service = service_from_banner(banner)
            if service is not None:
                services.append(service)
    return _assemble(
        ip=ip,
        hostnames=payload.get("hostnames"),
        country=_text(payload.get("country_name"), 80),
        city=_text(payload.get("city"), 80),
        isp=_text(payload.get("isp"), 120),
        organization=_text(payload.get("org"), 120),
        asn=payload.get("asn"),
        services=services,
        vulnerabilities=vulns_from(payload.get("vulns")),
        seen=_stamp(payload.get("last_update")),
    )


def assets_from_matches(matches: list) -> tuple[list[dict], bool]:
    grouped: dict[str, dict] = {}
    order: list[str] = []
    truncated = False
    for match in matches:
        if not isinstance(match, dict):
            continue
        ip = match.get("ip_str") if isinstance(match.get("ip_str"), str) else ""
        if not _is_public(ip):
            continue
        if ip not in grouped:
            if len(order) >= RESULT_LIMIT:
                truncated = True
                continue
            location = match.get("location") if isinstance(match.get("location"), dict) else {}
            grouped[ip] = _assemble(
                ip=ip,
                hostnames=match.get("hostnames"),
                country=_text(location.get("country_name"), 80),
                city=_text(location.get("city"), 80),
                isp=_text(match.get("isp"), 120),
                organization=_text(match.get("org"), 120),
                asn=match.get("asn"),
                services=[],
                vulnerabilities=[],
                seen="",
            )
            order.append(ip)
        asset = grouped[ip]
        service = service_from_banner(match)
        if service is not None:
            _add_service(asset, service)
        for vuln in vulns_from(match.get("vulns")):
            _add_vuln(asset, vuln)
        for name in _hostnames(match.get("hostnames")):
            if name not in asset["hostnames"]:
                asset["hostnames"].append(name)
        _finish_asset(asset)
    return [grouped[ip] for ip in order], truncated


def service_from_banner(banner: dict) -> dict | None:
    port = banner.get("port")
    if isinstance(port, str) and port.isdigit():
        port = int(port)
    if not isinstance(port, int) or isinstance(banner.get("port"), bool) or not 1 <= port <= 65535:
        return None
    transport = _token(banner.get("transport") or "tcp").lower() or "tcp"
    product = _text(banner.get("product"), 80)
    version = _text(banner.get("version"), 40)
    module = ""
    meta = banner.get("_shodan")
    if isinstance(meta, dict):
        module = _token(meta.get("module"))
    name = product or module or transport.upper()
    return {
        "port": port,
        "transport": transport,
        "name": name,
        "product": product,
        "version": version,
        "banner": _text(banner.get("data"), 240),
        "last_seen": _stamp(banner.get("timestamp")),
        "review": "warning" if version else "info",
    }


def vulns_from(raw: Any) -> list[dict]:
    pairs: list[tuple[str, Any]] = []
    if isinstance(raw, dict):
        pairs = [(str(name), info) for name, info in raw.items()]
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                pairs.append((item, {}))
            elif isinstance(item, dict):
                pairs.append((str(item.get("cve") or item.get("id") or ""), item))
    found = []
    seen = set()
    for name, info in pairs:
        cve = name.strip().upper()
        if not _CVE.fullmatch(cve) or cve in seen:
            continue
        seen.add(cve)
        info = info if isinstance(info, dict) else {}
        cvss = _cvss(info.get("cvss") if "cvss" in info else info.get("cvss_v3"))
        review = _cvss_review(cvss)
        found.append(
            {
                "cve": cve,
                "cvss": cvss,
                "severity": review,
                "summary": _text(info.get("summary"), 240),
                "shodan_verified": info.get("verified") is True,
            }
        )
    return found


def _assemble(
    ip: str,
    hostnames: Any,
    country: str,
    city: str,
    isp: str,
    organization: str,
    asn: Any,
    services: list[dict],
    vulnerabilities: list[dict],
    seen: str,
) -> dict:
    asset = {
        "ip": ip,
        "hostnames": _hostnames(hostnames),
        "country": country,
        "city": city,
        "location": ", ".join(part for part in (city, country) if part),
        "isp": isp,
        "organization": organization,
        "asn": asn.strip().upper() if isinstance(asn, str) and _ASN.fullmatch(asn.strip().upper()) else "",
        "ports": [],
        "services": [],
        "vulnerabilities": [],
        "last_seen": seen,
        "review": "info",
        "review_label": _REVIEW_LABEL["info"],
    }
    for service in services:
        _add_service(asset, service)
    for vuln in vulnerabilities:
        _add_vuln(asset, vuln)
    _finish_asset(asset)
    return asset


def _add_service(asset: dict, service: dict) -> None:
    key = (service["port"], service["transport"], service["product"], service["version"])
    existing = {
        (item["port"], item["transport"], item["product"], item["version"])
        for item in asset["services"]
    }
    if key not in existing:
        asset["services"].append(service)
    if service["port"] not in asset["ports"]:
        asset["ports"].append(service["port"])
        asset["ports"].sort()


def _add_vuln(asset: dict, vuln: dict) -> None:
    if any(item["cve"] == vuln["cve"] for item in asset["vulnerabilities"]):
        return
    asset["vulnerabilities"].append(vuln)


def _finish_asset(asset: dict) -> None:
    review = "info"
    for service in asset["services"]:
        review = _higher(review, service["review"])
        if service["last_seen"] and service["last_seen"] > asset["last_seen"]:
            asset["last_seen"] = service["last_seen"]
    for vuln in asset["vulnerabilities"]:
        review = _higher(review, vuln["severity"])
    asset["review"] = review
    asset["review_label"] = _REVIEW_LABEL[review]
    asset["services"].sort(key=lambda item: (item["port"], item["transport"]))
    asset["vulnerabilities"].sort(key=lambda item: item["cve"])


def _higher(left: str, right: str) -> str:
    return left if _REVIEW_RANK[left] >= _REVIEW_RANK[right] else right


def _cvss_review(cvss: float | None) -> str:
    if cvss is None:
        return "warning"
    if cvss >= 9:
        return "critical"
    if cvss >= 4:
        return "warning"
    return "info"


def _cvss(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
    else:
        return None
    if not 0 <= number <= 10:
        return None
    return round(number, 1)


def _hostnames(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    found = []
    for item in value:
        if not isinstance(item, str):
            continue
        name = item.strip().lower().rstrip(".")
        if _HOSTNAME.fullmatch(name) and name not in found:
            found.append(name)
        if len(found) == 12:
            break
    return found


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = "".join(char if char.isprintable() else " " for char in value)
    return " ".join(cleaned.split())[:limit]


def _token(value: Any) -> str:
    text = _text(value, 40)
    if not text or not re.fullmatch(r"[A-Za-z0-9._+-]{1,40}", text):
        return ""
    return text


def _stamp(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    match = _STAMP.match(value.strip())
    return "" if match is None else match.group(1) + "Z"
