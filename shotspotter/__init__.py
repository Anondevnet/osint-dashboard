"""City of Detroit DPD ShotSpotter CAD records from the public open-data service.

This package reads the published FeatureServer. It does not receive a
ShotSpotter, CAD, or Real-Time Crime Center stream.
"""

SOURCE_NAME = "City of Detroit — DPD ShotSpotter Incident Records"
DATA_TYPE = "Public CAD records initiated by the gunshot detection system"
VERIFICATION = (
    "Agency-published CAD extract. A stored row is not a live alert and is not "
    "independent confirmation of a gunshot."
)
ITEM_ID = "0f58d75bf50a48b8ab97c0c8964e8344"
PORTAL_URL = "https://www.arcgis.com/home/item.html?id=0f58d75bf50a48b8ab97c0c8964e8344"
SERVICE_ROOT = (
    "https://services2.arcgis.com/qvkbeam7Wirps6zC/ArcGIS/rest/services/"
    "ShotSpotter_911_Calls/FeatureServer"
)
LAYER_ID = 0
LAYER_NAME = "shotspotter_911_calls"
LAYER_URL = SERVICE_ROOT + "/0"
QUERY_URL = LAYER_URL + "/query"
SPATIAL_WKID = 4326
OBJECT_ID_FIELD = "incident_entry_id"
PROVIDER_ID = "detroit"
