"""Read-only client for the City of Detroit Police Serviced 911 Calls layer."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterator

from detroit import LAYER_NAME, LAYER_URL, QUERY_URL, SPATIAL_WKID
from detroit.records import FIELD_NAMES

PAGE_CAP = 2000
MAX_PAGES = 25
MAX_RETRY_AFTER_SECONDS = 3600


class DetroitSourceError(Exception):
    def __init__(self, code: str, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after = retry_after


def retry_after_seconds(raw: str | None) -> int | None:
    """Read an HTTP Retry-After value. Seconds are capped at one hour."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.isdigit():
        return min(int(text), MAX_RETRY_AFTER_SECONDS)
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    delta = int((moment - datetime.now(timezone.utc)).total_seconds())
    if delta < 0:
        return 0
    return min(delta, MAX_RETRY_AFTER_SECONDS)


def _header(headers: Any, name: str) -> str | None:
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    value = getter(name)
    if value is None:
        return None
    return str(value)


def _rate_limited(headers: Any) -> DetroitSourceError:
    return DetroitSourceError(
        "rate_limited",
        "The Detroit open-data service is rate limiting requests. "
        "The next check waits before trying again.",
        retry_after=retry_after_seconds(_header(headers, "Retry-After")),
    )


def timestamp_literal(value: datetime) -> str:
    moment = value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return "timestamp '%s'" % moment


def window_where(start: datetime) -> str:
    return "called_at >= %s" % timestamp_literal(start)


def _feature_tail(feature: Any) -> str:
    if not isinstance(feature, dict):
        return ""
    attributes = feature.get("attributes")
    if not isinstance(attributes, dict):
        return ""
    return "%s|%s|%s" % (
        attributes.get("called_at"),
        attributes.get("incident_entry_id"),
        attributes.get("incident_id"),
    )


def validate_metadata(layer: dict[str, Any]) -> int:
    if layer.get("name") != LAYER_NAME or layer.get("id") != 0:
        raise DetroitSourceError(
            "unexpected_layer",
            "The service did not return police_serviced_911_calls layer 0.",
        )
    wkid = (layer.get("spatialReference") or {}).get("wkid")
    if wkid != SPATIAL_WKID:
        raise DetroitSourceError(
            "unexpected_spatial_reference",
            "The layer spatial reference was not WGS 84 (4326).",
        )
    names = {
        field.get("name")
        for field in layer.get("fields") or []
        if isinstance(field, dict)
    }
    missing = [name for name in FIELD_NAMES if name not in names]
    if missing:
        raise DetroitSourceError(
            "unexpected_fields",
            "The layer is missing documented fields: " + ", ".join(missing),
        )
    if layer.get("geometryType") != "esriGeometryPoint":
        raise DetroitSourceError(
            "unexpected_geometry",
            "The layer geometry type was not a point.",
        )
    if layer.get("objectIdField") != "ESRI_OID":
        raise DetroitSourceError(
            "unexpected_object_id",
            "The layer object id field was not the documented ESRI_OID field.",
        )
    advanced = layer.get("advancedQueryCapabilities") or {}
    if advanced.get("supportsPagination") is not True:
        raise DetroitSourceError(
            "unexpected_pagination",
            "The layer does not advertise result pagination.",
        )
    page_size = layer.get("maxRecordCount")
    if not isinstance(page_size, int) or page_size < 1:
        raise DetroitSourceError(
            "unexpected_page_size",
            "The layer did not publish a maximum record count.",
        )
    return min(page_size, PAGE_CAP)


class DetroitClient:
    def __init__(
        self,
        query_url: str = QUERY_URL,
        layer_url: str = LAYER_URL,
        timeout: float = 30.0,
        opener: Any = None,
    ):
        self.query_url = query_url
        self.layer_url = layer_url
        self.timeout = timeout
        self.opener = opener or urllib.request.urlopen

    def _get_json(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        target = url + "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            target,
            headers={"Accept": "application/json", "User-Agent": "osint-dashboard-v2"},
            method="GET",
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                status = getattr(response, "status", 200)
                body = response.read()
        except TimeoutError as exc:
            raise DetroitSourceError("timeout", "The Detroit open-data service timed out.") from exc
        except socket.timeout as exc:
            raise DetroitSourceError("timeout", "The Detroit open-data service timed out.") from exc
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise _rate_limited(exc.headers) from exc
            raise DetroitSourceError(
                "http_%s" % exc.code,
                "The Detroit open-data service returned HTTP %s." % exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise DetroitSourceError("timeout", "The Detroit open-data service timed out.") from exc
            raise DetroitSourceError(
                "unavailable",
                "The Detroit open-data service is not reachable.",
            ) from exc
        if status == 429:
            raise _rate_limited(getattr(response, "headers", None))
        if status >= 400:
            raise DetroitSourceError(
                "http_%s" % status,
                "The Detroit open-data service returned HTTP %s." % status,
            )
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DetroitSourceError(
                "malformed",
                "The Detroit open-data service returned malformed JSON.",
            ) from exc
        if not isinstance(payload, dict):
            raise DetroitSourceError("malformed", "The Detroit open-data service returned an unexpected document.")
        if payload.get("error"):
            message = "The Detroit open-data service rejected the query."
            error = payload["error"]
            if isinstance(error, dict) and error.get("message"):
                message = "Detroit open data: " + str(error["message"])[:240]
            raise DetroitSourceError("query_error", message)
        return payload

    def fetch_metadata(self) -> dict[str, Any]:
        return self._get_json(self.layer_url, {"f": "json"})

    def count(self, where: str) -> int | None:
        payload = self._get_json(
            self.query_url,
            {"where": where, "returnCountOnly": "true", "f": "json"},
        )
        count = payload.get("count")
        if isinstance(count, int):
            return count
        return None

    def query_page(self, where: str, offset: int, page_size: int) -> dict[str, Any]:
        return self._get_json(
            self.query_url,
            {
                "where": where,
                "outFields": ",".join(FIELD_NAMES),
                "orderByFields": "called_at ASC, incident_entry_id ASC",
                "resultOffset": str(offset),
                "resultRecordCount": str(page_size),
                "returnGeometry": "false",
                "f": "json",
            },
        )

    def iter_features(self, where: str, page_size: int) -> Iterator[tuple[int, dict[str, Any]]]:
        """Yield (page_number, feature). Never walks the undated full archive."""
        offset = 0
        previous_tail = None
        for page in range(1, MAX_PAGES + 1):
            payload = self.query_page(where, offset, page_size)
            features = payload.get("features")
            if not isinstance(features, list):
                raise DetroitSourceError("malformed", "A query page did not contain a feature list.")
            if not features:
                return
            tail = _feature_tail(features[-1])
            if tail == previous_tail:
                raise DetroitSourceError(
                    "page_stuck",
                    "The Detroit service repeated a page instead of advancing.",
                )
            previous_tail = tail
            for feature in features:
                yield page, feature
            if len(features) < page_size or payload.get("exceededTransferLimit") is False:
                return
            offset += len(features)
        raise DetroitSourceError(
            "page_limit",
            "The recent-window query exceeded %s pages and was stopped." % MAX_PAGES,
        )
