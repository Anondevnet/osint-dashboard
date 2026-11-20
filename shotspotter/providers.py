"""Provider catalog. Only a verified public source is enabled.

Secondary cities are documented here and stay off. None of them is marked live.
"""

from __future__ import annotations

from typing import Any

from shotspotter import ITEM_ID, LAYER_URL, PORTAL_URL, PROVIDER_ID, QUERY_URL

CHECKED_ON = "2026-11-17"

DETROIT_NOTES = (
    "Official item title: DPD ShotSpotter Incident Records. "
    "Public ArcGIS item " + ITEM_ID + ". "
    "The layer is shotspotter_911_calls on FeatureServer/0. "
    "Rows are Detroit Police CAD records initiated by the gunshot detection system "
    "after CAD integration on 15 March 2023. "
    "Records from 15 March 2021 through 15 March 2023 were entered by 911 staff and "
    "remain in Police Serviced 911 Calls. "
    "The city says some ShotSpotter rows also remain in Police Serviced 911 Calls and "
    "DPD CAD Initiated Records after 15 March 2023. "
    "Published locations are the nearest intersection. "
    "The layer has no per-incident publication timestamp and no attachments. "
    "dataLastEditDate is a dataset timestamp, not an event time. "
    "The city describes detection-to-alert inside the vendor workflow as typically "
    "under 60 seconds. That sentence is not a measurement of this open-data extract. "
    "No ShotSpotter, DPD CAD, or Real-Time Crime Center credential was granted. "
    "This provider cannot be labeled LIVE."
)

PROVIDERS: tuple[dict[str, Any], ...] = (
    {
        "provider_id": PROVIDER_ID,
        "city": "Detroit, Michigan",
        "title": "DPD ShotSpotter Incident Records",
        "enabled": 1,
        "status_ceiling": "DELAYED",
        "kind": "public_arcgis_poll",
        "source_url": PORTAL_URL,
        "api_url": LAYER_URL,
        "query_url": QUERY_URL,
        "access": "public",
        "credentials_required": 0,
        "record_kind": "individual_cad",
        "publication": "delayed",
        "realtime_verified": 0,
        "operational": "public_extract",
        "free_api": 1,
        "individual_timestamps": 1,
        "notes": DETROIT_NOTES,
        "checked_on": CHECKED_ON,
    },
    {
        "provider_id": "washington-dc",
        "city": "Washington, DC",
        "title": "Shot Spotter Gun Shots",
        "enabled": 0,
        "status_ceiling": "HISTORICAL",
        "kind": "public_arcgis_discover_only",
        "source_url": "https://opendata.dc.gov/datasets/DCGIS::shot-spotter-gun-shots/about",
        "api_url": None,
        "query_url": None,
        "access": "public",
        "credentials_required": 0,
        "record_kind": "individual_alert",
        "publication": "quarterly",
        "realtime_verified": 0,
        "operational": "public_quarterly_extract",
        "free_api": 1,
        "individual_timestamps": 1,
        "notes": (
            "Open Data DC publishes individual ShotSpotter incidents and says updates are quarterly. "
            "The catalog page reviewed on 2026-11-17 showed a data update in November 2025. "
            "Coordinates on the public table are coarse. This provider is not enabled and is not LIVE."
        ),
        "checked_on": CHECKED_ON,
    },
    {
        "provider_id": "milwaukee",
        "city": "Milwaukee, Wisconsin",
        "title": "No current official public ShotSpotter API confirmed",
        "enabled": 0,
        "status_ceiling": "UNVERIFIED",
        "kind": "not_verified",
        "source_url": "https://data.milwaukee.gov/",
        "api_url": None,
        "query_url": None,
        "access": "unknown",
        "credentials_required": 1,
        "record_kind": "unknown",
        "publication": "unknown",
        "realtime_verified": 0,
        "operational": "unverified",
        "free_api": 0,
        "individual_timestamps": 0,
        "notes": (
            "No current City of Milwaukee open-data API for individual gunshot-detection alerts "
            "was confirmed on 2026-11-17. A 2016 research extract of 2013–2015 dispatches is not "
            "an official live or current source. This provider is not enabled and is not LIVE."
        ),
        "checked_on": CHECKED_ON,
    },
    {
        "provider_id": "chicago",
        "city": "Chicago, Illinois",
        "title": "Violence Reduction - Shotspotter Alerts - Historical",
        "enabled": 0,
        "status_ceiling": "HISTORICAL",
        "kind": "historical_socrata_discover_only",
        "source_url": "https://data.cityofchicago.org/Public-Safety/Violence-Reduction-Shotspotter-Alerts-Historical/3h7q-7mdb",
        "api_url": "https://data.cityofchicago.org/resource/3h7q-7mdb.json",
        "query_url": None,
        "access": "public",
        "credentials_required": 0,
        "record_kind": "individual_alert",
        "publication": "historical",
        "realtime_verified": 0,
        "operational": "ended",
        "free_api": 1,
        "individual_timestamps": 1,
        "notes": (
            "The City of Chicago says it ended ShotSpotter on 22 September 2024. "
            "The public dataset is historical only and was last updated 4 October 2024. "
            "It is not enabled. It is not a current feed and it is not LIVE."
        ),
        "checked_on": CHECKED_ON,
    },
)


def catalog() -> list[dict[str, Any]]:
    return [dict(item) for item in PROVIDERS]


def enabled_provider() -> dict[str, Any]:
    for item in PROVIDERS:
        if item["provider_id"] == PROVIDER_ID and item["enabled"]:
            return dict(item)
    raise RuntimeError("The Detroit public provider is missing from the catalog.")


def public_provider(item: dict[str, Any], feed_status: str | None = None) -> dict[str, Any]:
    """Fields safe to show in the browser. No credentials exist to leak."""
    status = feed_status if item.get("enabled") else item["status_ceiling"]
    if status == "LIVE":
        status = item["status_ceiling"]
    return {
        "provider_id": item["provider_id"],
        "city": item["city"],
        "title": item["title"],
        "enabled": bool(item["enabled"]),
        "feed_status": status,
        "kind": item["kind"],
        "source_url": item["source_url"],
        "api_url": item["api_url"],
        "access": item["access"],
        "credentials_required": bool(item["credentials_required"]),
        "record_kind": item["record_kind"],
        "publication": item["publication"],
        "realtime_verified": False,
        "operational": item["operational"],
        "free_api": bool(item["free_api"]),
        "individual_timestamps": bool(item["individual_timestamps"]),
        "notes": item["notes"],
        "checked_on": item["checked_on"],
        "live": False,
    }
