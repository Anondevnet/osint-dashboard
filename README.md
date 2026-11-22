# OSINT Dashboard v2

A separate, read-only view of investigations already saved by a separate local OSINT command center.

This app does not start scans, does not write to the command center database, and does not install packages. It serves a dark dashboard on port 8010 and forwards a fixed list of GET requests to the command center.

## Launch

Start the existing command center first. That process is unchanged:

From that command center's repository root:

```bash
python3 -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000
```

Then, from this repository root, start this dashboard:

```bash
python3 server.py
```

Open http://127.0.0.1:8010

Check that this server is up:

```bash
curl -s http://127.0.0.1:8010/healthz
```

Check that it can read the command center:

```bash
curl -s http://127.0.0.1:8010/api/health
curl -s http://127.0.0.1:8010/api/stats
```

Python 3.9 or newer is enough. The standard library is the only dependency.

## What you can do

- Overview is the command center landing page. It shows investigation counts, Detroit publication status, and saved Shodan totals when those local sources respond. It does not invent missing numbers.
- Search filters saved investigations and saved records. The target-type panel lists the tools that would apply. It does not run them.
- Findings explains each saved fact and its confidence label.
- Evidence shows the tool output behind a finding, with the same confidence and “how we know” labels.
- An investigation page shows progress through Discovery, Infrastructure, Identity, Correlation, and Report, plus each tool’s status.
- Detroit 911 shows a local copy of the City of Detroit’s published police-serviced 911 calls. It is not a live dispatch feed.

## Detroit police-serviced 911 calls

The dashboard reads a recent window from the City of Detroit open-data layer and stores it in `data/detroit_911.sqlite`. That file is separate from the command center database. Each refresh upserts the window. It does not download the full archive.

Official dataset: <https://data.detroitmi.gov/datasets/detroitmi::police-serviced-911-calls>

Service layer: `Police_Serviced_911_Calls` FeatureServer layer 0 (`police_serviced_911_calls`).

The server checks this layer when it starts, then every 5 minutes. `DETROIT_SYNC_INTERVAL_SECONDS=0` turns those checks off. A value below 60 seconds is raised to 60. `DETROIT_LOOKBACK_HOURS` defaults to 48 and cannot exceed 14 days. Either name can live in `.env`; a value already set in the process environment wins.

A check compares the city's `dataLastEditDate` with the last successful check. When that publication timestamp has not changed, the check does not download rows. When it has changed, the recent window is read again and upserted. If the last successful wide pass is older than `DETROIT_RECONCILE_INTERVAL_SECONDS` (default 6 hours), that changed check uses `DETROIT_RECONCILE_HOURS` instead (default 168 hours, maximum 14 days). Set `DETROIT_RECONCILE_HOURS=0` to keep every download on the recent window. Older stored rows stay. Records older than 14 days are not refreshed by the automatic check. The full archive is not paged.

Each query page asks for at most `min(maxRecordCount, 2000)` rows and stops after 25 pages. Hitting that cap records a failed check and does not treat a partial page set as success. Source record IDs are the published incident IDs, and a repeat ID updates the stored row instead of inserting a duplicate.

The five-minute value is how often this dashboard checks. It is not the city's publication schedule, and the city may publish about once a day. A mapped point is a published call-for-service record, not a call that is happening now. HTTP 429 waits for a `Retry-After` delay, capped at one hour, before another check is allowed to contact the service. That wait does not run a second check at the same time.

```bash
python3 -m detroit.sync
```

The manual command uses the same windows. The Detroit page reloads stored records, counts, and map markers every 30 seconds without reloading the browser page. Applied filters and the current map position stay in place. A refresh waits while a filter field is focused, so it does not replace the controls the user is editing.

The map uses Leaflet 1.9.4 stored in `static/leaflet.js` and `static/leaflet.css`. It does not load the library from a CDN. The default view is satellite imagery from Esri World Imagery, with a place-name overlay. It can be dragged and zoomed in the browser. The map shows “Powered by Esri” and the imagery and label providers. Optional street imagery is the CARTO Voyager raster basemap. The Python process does not download tiles.

Street tiles need a CARTO basemap key used only by this local dashboard. The server reads it from the environment. Do not put the key in a source file or commit it. Anyone using the page can see the key in the browser's tile requests, so treat it as visible.

```bash
CARTO_BASEMAP_KEY="your-dedicated-key" python3 server.py
```

Request a key at <https://carto.com/basemaps/apikey/>. Then open the [CARTO Basemaps dashboard](https://dashboard.basemaps.carto.com) and:

- Keep this key separate from any other project.
- Restrict its websites to `127.0.0.1` and `localhost`. Do not add a public host. A key that lists those local names cannot also list public sites. CARTO matches the host in the Referer and ignores the port.
- Tile images send `Referer: http://127.0.0.1:8010/` when you open the app at that address, or `http://localhost:8010/` if you open it as localhost. Only those image requests send an origin. The rest of the dashboard still sends no Referer.
- Do not open the app at `http://[::1]/`. CARTO does not accept that host.
- Watch the hourly request count and the refused count. If the key leaks, revoke it in that dashboard and start the server with a replacement.

Without `CARTO_BASEMAP_KEY`, the Detroit page still shows satellite imagery, stored incidents, and the published intersection dots. It does not request CARTO street tiles. The map keeps the OpenStreetMap and CARTO credit visible whenever Voyager tiles are on.

## ShotSpotter Detroit

Open `#/shotspotter-detroit`. The page reads City of Detroit open data for DPD ShotSpotter Incident Records and stores a recent window in `data/shotspotter.sqlite`. That file is separate from the 911 database and the command center database. Rows are upserted. Nothing in this module deletes stored incidents.

Official item: [DPD ShotSpotter Incident Records](https://www.arcgis.com/home/item.html?id=0f58d75bf50a48b8ab97c0c8964e8344) (`0f58d75bf50a48b8ab97c0c8964e8344`).

Service layer: `https://services2.arcgis.com/qvkbeam7Wirps6zC/ArcGIS/rest/services/ShotSpotter_911_Calls/FeatureServer/0` (`shotspotter_911_calls`). The object id field is `incident_entry_id`. Coordinates are WGS 84. The layer's maximum page size is 2,000.

These rows are Detroit Police CAD records initiated by the gunshot detection system after CAD integration on 15 March 2023. The city says earlier ShotSpotter-related CAD was entered by 911 staff and remains in Police Serviced 911 Calls, and that some later rows also remain in that dataset and in DPD CAD Initiated Records. Published locations are the nearest intersection, for privacy. The city describes coverage as about 39 square miles. Frequent nature codes are SST345 single gunshot, SST346 multiple gunshots, and SST342 probable gunfire. The layer has no attachments, so no acoustic audio is published. The city says the detection-to-alert step inside the vendor workflow is typically under 60 seconds. That sentence describes the vendor workflow. It is not a measurement of this open-data extract.

The layer has no per-incident publication timestamp. `editingInfo.dataLastEditDate` is the dataset publication time. This dashboard does not treat a successful request, or the time it retrieved a row, as the event time. Average publication delay stays blank unless both a per-incident publication time and an event time exist.

The server checks the layer when it starts, then every 60 seconds. `SHOTSPOTTER_SYNC_INTERVAL_SECONDS=0` turns those checks off. A value below 60 seconds is raised to 60. `SHOTSPOTTER_LOOKBACK_HOURS` defaults to 336 and cannot exceed 14 days. When `dataLastEditDate` is unchanged, the check does not download rows. When it changes, the recent window is read again and upserted. The full archive is not paged. A check stops after 25 pages.

The feed status is `DELAYED`, `HISTORICAL`, `UNAVAILABLE`, or `UNVERIFIED`. It is not `LIVE`. No ShotSpotter, DPD CAD, or Real-Time Crime Center credential was granted, and this dashboard does not call a private or authenticated interface. Washington DC, Milwaukee, and Chicago are listed on the page as discovery notes and stay disabled.

```bash
python3 -m shotspotter.sync
```

Counts on the page are stored CAD incident records. They are not counts of dispatched units, shots, or people. `service_count` is the city layer's published total from the last check. The local table keeps the recent window, not that full archive.

## What this server will not do

POST, PUT, PATCH, and DELETE are refused. Routes that start a job, cancel a job, generate a report, approve a merge, or call a provider are not forwarded.

Optional environment variables:

- `OSINT_UPSTREAM` — command center URL. Default `http://127.0.0.1:8000`. Must be local HTTP with no path.
- `OSINT_V2_HOST` — default `127.0.0.1`. Localhost only.
- `OSINT_V2_PORT` — default `8010`.
- `CARTO_BASEMAP_KEY` — optional dedicated CARTO key for Detroit Voyager street tiles. Leave it unset to keep street tiles off.
- `SHODAN_API_KEY` — optional Shodan key for the Internet Exposure page. Leave it unset to keep lookups off. The server reads it; the browser never receives it. A process environment value wins over the local `.env` file.
- `DETROIT_SYNC_INTERVAL_SECONDS` — how often the server checks the published Detroit layer. Default `300`. `0` turns checks off.
- `DETROIT_LOOKBACK_HOURS` — recent window for each Detroit download. Default `48`. Maximum 14 days.
- `DETROIT_RECONCILE_HOURS` — wider window used after the city timestamp changes, when the last wide pass is old. Default `168`. Maximum 14 days. `0` disables the wider pass.
- `DETROIT_RECONCILE_INTERVAL_SECONDS` — minimum time between wide passes. Default `21600` (6 hours). `0` allows a wide pass on every changed timestamp.
- `SHOTSPOTTER_SYNC_INTERVAL_SECONDS` — how often the server checks the public ShotSpotter CAD layer. Default `60`. `0` turns checks off. Values below 60 are raised to 60.
- `SHOTSPOTTER_LOOKBACK_HOURS` — recent window for each ShotSpotter download. Default `336`. Maximum 14 days.

## Internet Exposure

Open `#/exposure` after the server starts. The page looks up one public IP address, one domain, or an organization name only after you confirm that you own it or are authorized to assess it. It uses Shodan's read-only API. It does not scan hosts, try passwords, or run an internet-wide search.

Save the key once in the local `.env` file. The prompt hides what you type. The script does not print the key, does not contact Shodan, and refuses to replace an existing `.env` file.

```bash
bash scripts/configure-shodan-key.sh
python3 server.py
```

A key already exported in the shell still wins over `.env`. Create the key in your Shodan account. Do not put it in a source file. Results are Shodan's report, including any CVE identifiers Shodan supplies. A listing is not a test, and it is not proof that a vulnerability can be used. Repeat lookups for the same target within 15 minutes are served from `data/shodan.sqlite`, which is separate from the Detroit database.

## Tests

```bash
python3 -m unittest discover -s tests
```

The tests use a local fixture. They do not open the command center database and they do not run OSINT tools.
