"""City of Detroit police-serviced 911 calls, read from the public open-data service."""

SOURCE_NAME = "City of Detroit — Police Serviced 911 Calls"
DATA_TYPE = "Publicly released police calls-for-service records"
VERIFICATION = "Agency-published record; not independent confirmation of incident outcome."
PORTAL_URL = "https://data.detroitmi.gov/datasets/detroitmi::police-serviced-911-calls"
SERVICE_ROOT = (
    "https://services2.arcgis.com/qvkbeam7Wirps6zC/ArcGIS/rest/services/"
    "Police_Serviced_911_Calls/FeatureServer"
)
LAYER_ID = 0
LAYER_NAME = "police_serviced_911_calls"
LAYER_URL = SERVICE_ROOT + "/0"
QUERY_URL = LAYER_URL + "/query"
SPATIAL_WKID = 4326
