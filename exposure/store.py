"""Local cache and lookup history. This is not the Detroit database."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "shodan.sqlite"
_LOCK = threading.Lock()


def database_path() -> Path:
    override = os.environ.get("SHODAN_DB", "").strip()
    return Path(override) if override else DEFAULT_DB


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS response_cache (
            cache_key TEXT PRIMARY KEY,
            body TEXT NOT NULL,
            stored_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS assets (
            ip TEXT PRIMARY KEY,
            body TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at TEXT NOT NULL,
            ok INTEGER NOT NULL,
            cached INTEGER NOT NULL,
            asset_count INTEGER NOT NULL,
            message TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    return connection


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def read_cache(cache_key: str, ttl_seconds: int, now: str | None = None) -> dict | None:
    current = _parse_time(now or utc_now())
    with _LOCK:
        connection = _connect()
        try:
            row = connection.execute(
                "SELECT body, stored_at FROM response_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        finally:
            connection.close()
    if row is None or current is None:
        return None
    stored = _parse_time(row["stored_at"])
    if stored is None or current - stored > timedelta(seconds=ttl_seconds):
        return None
    try:
        payload = json.loads(row["body"])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def write_cache(cache_key: str, payload: dict, stored_at: str | None = None) -> None:
    body = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    with _LOCK:
        connection = _connect()
        try:
            connection.execute(
                """
                INSERT INTO response_cache (cache_key, body, stored_at)
                VALUES (?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET body = excluded.body, stored_at = excluded.stored_at
                """,
                (cache_key, body, stored_at or utc_now()),
            )
            connection.commit()
        finally:
            connection.close()


def save_assets(assets: list[dict], updated_at: str | None = None) -> None:
    stamp = updated_at or utc_now()
    with _LOCK:
        connection = _connect()
        try:
            for asset in assets:
                ip = asset.get("ip")
                if not isinstance(ip, str) or not ip:
                    continue
                connection.execute(
                    """
                    INSERT INTO assets (ip, body, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(ip) DO UPDATE SET body = excluded.body, updated_at = excluded.updated_at
                    """,
                    (ip, json.dumps(asset, ensure_ascii=True, separators=(",", ":")), stamp),
                )
            connection.commit()
        finally:
            connection.close()


def add_history(
    kind: str,
    value: str,
    ok: bool,
    cached: bool,
    asset_count: int,
    message: str,
    created_at: str | None = None,
) -> None:
    with _LOCK:
        connection = _connect()
        try:
            connection.execute(
                """
                INSERT INTO history (kind, value, created_at, ok, cached, asset_count, message)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (kind, value, created_at or utc_now(), int(ok), int(cached), asset_count, message[:240]),
            )
            connection.commit()
        finally:
            connection.close()


def list_history(limit: int = 20) -> list[dict]:
    with _LOCK:
        connection = _connect()
        try:
            rows = connection.execute(
                """
                SELECT id, kind, value, created_at, ok, cached, asset_count, message
                FROM history ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            connection.close()
    return [
        {
            "id": row["id"],
            "kind": row["kind"],
            "value": row["value"],
            "created_at": row["created_at"],
            "ok": bool(row["ok"]),
            "cached": bool(row["cached"]),
            "asset_count": row["asset_count"],
            "message": row["message"],
        }
        for row in rows
    ]


def set_meta(key: str, value: str) -> None:
    with _LOCK:
        connection = _connect()
        try:
            connection.execute(
                """
                INSERT INTO meta (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            connection.commit()
        finally:
            connection.close()


def get_meta(key: str) -> str | None:
    with _LOCK:
        connection = _connect()
        try:
            row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        finally:
            connection.close()
    return None if row is None else row["value"]


def _load_assets(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute("SELECT body FROM assets").fetchall()
    assets = []
    for row in rows:
        try:
            payload = json.loads(row["body"])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            assets.append(payload)
    return assets


def _ranked(counter: dict, key: str) -> list[dict[str, Any]]:
    ordered = sorted(counter.items(), key=lambda item: (-item[1], str(item[0])))
    return [{key: name, "count": count} for name, count in ordered[:12]]


def _lookup_activity(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = connection.execute(
        "SELECT created_at, ok, cached FROM history ORDER BY id DESC LIMIT 200"
    ).fetchall()
    cached = 0
    fresh = 0
    last_lookup_at = None
    for row in rows:
        if last_lookup_at is None:
            last_lookup_at = row["created_at"]
        if row["cached"]:
            cached += 1
        elif row["ok"]:
            fresh += 1
    return {"cached": cached, "fresh": fresh, "last_lookup_at": last_lookup_at}


def summary() -> dict[str, Any]:
    with _LOCK:
        connection = _connect()
        try:
            assets = _load_assets(connection)
            last_sync = connection.execute(
                "SELECT value FROM meta WHERE key = 'last_sync'"
            ).fetchone()
            history = _lookup_activity(connection)
        finally:
            connection.close()
    services = 0
    ports = 0
    version_reviews = 0
    vulnerability_reviews = 0
    port_counts: dict[int, int] = {}
    service_counts: dict[str, int] = {}
    country_counts: dict[str, int] = {}
    for asset in assets:
        service_rows = asset.get("services") if isinstance(asset.get("services"), list) else []
        port_rows = asset.get("ports") if isinstance(asset.get("ports"), list) else []
        vuln_rows = asset.get("vulnerabilities") if isinstance(asset.get("vulnerabilities"), list) else []
        services += len(service_rows)
        ports += len(port_rows)
        version_reviews += sum(1 for item in service_rows if isinstance(item, dict) and item.get("version"))
        vulnerability_reviews += len(vuln_rows)
        for port in port_rows:
            if isinstance(port, bool) or not isinstance(port, int):
                continue
            port_counts[port] = port_counts.get(port, 0) + 1
        for service in service_rows:
            if not isinstance(service, dict):
                continue
            name = service.get("name") or service.get("product") or ""
            if isinstance(name, str) and name.strip():
                label = name.strip()[:80]
                service_counts[label] = service_counts.get(label, 0) + 1
        country = asset.get("country")
        if isinstance(country, str) and country.strip():
            label = country.strip()[:80]
            country_counts[label] = country_counts.get(label, 0) + 1
    return {
        "assets": len(assets),
        "ip_addresses": len(assets),
        "services": services,
        "open_ports": ports,
        "version_reviews": version_reviews,
        "vulnerability_reviews": vulnerability_reviews,
        "port_breakdown": _ranked(port_counts, "port"),
        "service_breakdown": _ranked(service_counts, "name"),
        "country_breakdown": _ranked(country_counts, "name"),
        "cached_lookups": history["cached"],
        "fresh_lookups": history["fresh"],
        "last_lookup_at": history["last_lookup_at"],
        "last_sync": None if last_sync is None else last_sync["value"],
    }
