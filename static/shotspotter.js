/* Detroit ShotSpotter CAD extract. Published open data, not a live alert feed. */

var SHOT_FRAME = { west: -83.4, east: -82.8, south: 42.2, north: 42.5 };
var SHOT_FILTERS = ["from", "to", "classification", "q", "neighborhood", "zip_code", "precinct", "freshness", "west", "south", "east", "north", "offset"];
var SHOT_VOYAGER_PREFIX = "https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png?key=";
var SHOT_VOYAGER_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, &copy; <a href="https://carto.com/attribution/">CARTO</a>';
var SHOT_SATELLITE_TILES = "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
var SHOT_SATELLITE_LABELS = "https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}";
var SHOT_SATELLITE_ATTRIBUTION = "Powered by Esri. Imagery: Esri, Vantor, Earthstar Geographics, and the GIS User Community. Labels: Esri, HERE, Garmin, &copy; OpenStreetMap contributors, and the GIS user community.";
var SHOT_CLASS_COLOR = {
  single: "#ff8a3d",
  multiple: "#ff5d73",
  probable: "#e7c15a",
  other: "#8ea0bd",
  unclassified: "#8ea0bd",
};
var shotMapState = { map: null, root: null, markers: [], points: [], filterKey: "", markerZoom: null, suspendZoom: false };
var shotRefreshTimer = null;
var SHOT_REFRESH_MS = 30000;

function shotQuery(params) {
  const search = new URLSearchParams();
  SHOT_FILTERS.forEach(function (key) {
    const value = params.get(key);
    if (value) search.set(key, value);
  });
  const text = search.toString();
  return text ? "?" + text : "";
}

function shotHash(params, extra) {
  const search = new URLSearchParams(shotQuery(params).replace(/^\?/, ""));
  Object.keys(extra || {}).forEach(function (key) {
    const value = extra[key];
    if (value == null || value === "") search.delete(key);
    else search.set(key, String(value));
  });
  const text = search.toString();
  return text ? "#/shotspotter-detroit?" + text : "#/shotspotter-detroit";
}

function shotToLocal(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = function (number) { return String(number).padStart(2, "0"); };
  return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate())
    + "T" + pad(date.getHours()) + ":" + pad(date.getMinutes());
}

function shotFromLocal(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toISOString().replace(/\.\d{3}Z$/, "Z");
}

function shotWhen(iso) {
  if (!iso) return "Not supplied";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  return new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Detroit",
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(date);
}

function shotDuration(seconds) {
  if (seconds == null || seconds === "") return "Not available";
  const value = Number(seconds);
  if (!Number.isFinite(value) || value < 0) return "Not available";
  if (value < 90) return Math.round(value) + " seconds";
  const minutes = Math.round(value / 60);
  if (minutes < 120) return minutes + " minutes";
  const hours = Math.round(minutes / 60);
  if (hours < 48) return hours + " hours";
  return Math.round(hours / 24) + " days";
}

function shotText(value) {
  return value == null || value === "" ? "Not supplied" : String(value);
}

function shotLink(url, label) {
  if (typeof url !== "string") return null;
  const allowed = url.indexOf("https://www.arcgis.com/") === 0
    || url.indexOf("https://services2.arcgis.com/") === 0
    || url.indexOf("https://data.detroitmi.gov/") === 0
    || url.indexOf("https://opendata.dc.gov/") === 0
    || url.indexOf("https://data.cityofchicago.org/") === 0
    || url.indexOf("https://data.milwaukee.gov/") === 0;
  if (!allowed) return null;
  return h("a", { href: url, rel: "noreferrer" }, label);
}

function shotFeedClass(status) {
  const name = String(status || "UNVERIFIED").toLowerCase();
  if (name === "delayed" || name === "historical" || name === "unavailable" || name === "unverified" || name === "live") {
    return "feed-pill feed-" + name;
  }
  return "feed-pill feed-unverified";
}

function shotOptions(values, selected, labels) {
  const items = [h("option", { value: "" }, "All")];
  const seen = {};
  (values || []).forEach(function (value) {
    const text = String(value);
    seen[text] = true;
    const label = labels && labels[text] ? labels[text] : text;
    items.push(h("option", { value: text, selected: text === selected }, label));
  });
  if (selected && !seen[selected]) items.push(h("option", { value: selected, selected: true }, selected));
  return items;
}

function shotVoyager(payload) {
  if (!payload || payload.enabled !== true || typeof payload.tile_url !== "string") return "";
  if (payload.tile_url.indexOf(SHOT_VOYAGER_PREFIX) !== 0) return "";
  return payload.tile_url;
}

function shotStat(label, value) {
  return h("div", { class: "stat" }, [
    h("p", { class: "stat-label" }, label),
    h("p", { class: "stat-value" }, value == null || value === "" ? "—" : String(value)),
  ]);
}

function shotStatusBlock(status) {
  const feed = status.feed_status || "UNVERIFIED";
  return h("div", { id: "shot-status-slot" }, [
    h("div", { class: "shot-strip" }, [
      h("div", { class: "stat" }, [
        h("p", { class: "stat-label" }, "Source status"),
        h("p", { class: "stat-value" }, h("span", { class: shotFeedClass(feed) }, feed)),
      ]),
      shotStat("Last successful sync", shotWhen(status.last_success_at)),
      shotStat("Last source publication", shotWhen(status.source_edited_at)),
      shotStat("Newest event", shotWhen(status.newest_event_at)),
      h("p", { class: "footnote" }, status.feed_detail || "Freshness is unverified."),
      h("p", { class: "footnote" }, status.monitoring || "Monitoring status is not available."),
      h("p", { class: "footnote" }, status.dataset_lag_note || ""),
      status.dataset_lag_seconds == null
        ? h("p", { class: "footnote" }, "Dataset lag was not calculated.")
        : h("p", { class: "footnote" }, "Dataset lag: " + shotDuration(status.dataset_lag_seconds) + ". This is not an average per-incident delay."),
    ]),
  ]);
}

function shotStatsBlock(stats) {
  const average = stats.average_publication_delay_seconds == null
    ? "Not available"
    : shotDuration(stats.average_publication_delay_seconds);
  return h("div", { id: "shot-stats-slot" }, [
    h("div", { class: "shot-strip" }, [
      shotStat("Stored CAD incidents", stats.stored_incidents),
      shotStat("Today (Detroit)", stats.incidents_today),
      shotStat("Last 24 hours", stats.incidents_last_24_hours),
      shotStat("Last 7 days", stats.incidents_last_7_days),
      shotStat("Single gunshot", stats.single_shot),
      shotStat("Multiple gunshots", stats.multiple_shot),
      shotStat("Probable gunfire", stats.probable_gunfire),
      shotStat("Unclassified", stats.unclassified),
      shotStat("Other published code", stats.other_classification),
      shotStat("Average publication delay", average),
      h("p", { class: "footnote" }, stats.count_note || stats.count_basis || ""),
      h("p", { class: "footnote" }, stats.publication_delay_note || ""),
      stats.service_count == null
        ? h("p", { class: "footnote" }, "The city service total has not been read yet.")
        : h("p", { class: "footnote" }, "City service total at last check: " + stats.service_count + ". Stored rows are the recent window, not that full archive."),
    ]),
  ]);
}

function shotForm(params, status) {
  const facets = (status && status.facets) || {};
  const labels = {
    single: "Single gunshot",
    multiple: "Multiple gunshots",
    probable: "Probable gunfire",
    unclassified: "Unclassified",
    other: "Other published code",
    DELAYED: "Delayed record",
    HISTORICAL: "Historical record",
    UNVERIFIED: "Unverified",
  };
  return h("form", {
    class: "shot-filters",
    onsubmit: function (event) {
      event.preventDefault();
      const data = new FormData(event.target);
      const next = new URLSearchParams();
      ["q", "classification", "neighborhood", "zip_code", "precinct", "freshness"].forEach(function (key) {
        const value = String(data.get(key) || "").trim();
        if (value) next.set(key, value);
      });
      const from = shotFromLocal(String(data.get("from") || ""));
      const to = shotFromLocal(String(data.get("to") || ""));
      if (from) next.set("from", from);
      if (to) next.set("to", to);
      ["west", "south", "east", "north"].forEach(function (key) {
        if (params.get(key)) next.set(key, params.get(key));
      });
      location.hash = shotHash(next, {});
    },
  }, [
    h("label", { class: "span-2" }, ["Search", h("input", { name: "q", value: params.get("q") || "", maxlength: "80" })]),
    h("label", null, ["From", h("input", { type: "datetime-local", name: "from", value: shotToLocal(params.get("from")) })]),
    h("label", null, ["To", h("input", { type: "datetime-local", name: "to", value: shotToLocal(params.get("to")) })]),
    h("label", null, ["Classification", h("select", { name: "classification" }, shotOptions(["single", "multiple", "probable", "unclassified", "other"], params.get("classification") || "", labels))]),
    h("label", null, ["Freshness", h("select", { name: "freshness" }, shotOptions(["DELAYED", "HISTORICAL", "UNVERIFIED"], params.get("freshness") || "", labels))]),
    h("label", null, ["Neighborhood", h("select", { name: "neighborhood" }, shotOptions(facets.neighborhood, params.get("neighborhood") || ""))]),
    h("label", null, ["ZIP", h("select", { name: "zip_code" }, shotOptions(facets.zip_code, params.get("zip_code") || ""))]),
    h("label", null, ["Precinct", h("select", { name: "precinct" }, shotOptions(facets.precinct, params.get("precinct") || ""))]),
    h("div", null, [
      h("button", { type: "submit" }, "Apply"),
      " ",
      h("a", { class: "btn", href: "#/shotspotter-detroit" }, "Clear"),
    ]),
  ]);
}

function shotCard(record, params) {
  const selected = (params.get("id") || "") === record.source_record_id;
  return h("article", { class: "card" + (selected ? " is-selected" : "") }, [
    record.newly_retrieved_historical
      ? h("p", { class: "historical-banner" }, "Historical record retrieved recently. This is not a new alert.")
      : null,
    h("h3", null, shotText(record.incident_id || record.source_record_id)),
    h("p", null, shotWhen(record.called_at) + " · " + shotText(record.classification_label)),
    h("p", null, record.intersection ? record.intersection : shotText(record.location_note)),
    h("p", { class: "muted" }, shotText(record.source_name)),
    h("p", null, "Publication delay: " + (record.publication_delay_seconds == null ? "Not available" : shotDuration(record.publication_delay_seconds))),
    h("p", null, record.verification_note || "Published CAD record. It is not a live alert."),
    h("p", null, h("span", { class: shotFeedClass(record.freshness) }, record.freshness || "UNVERIFIED")),
    h("p", null, h("a", { href: shotHash(params, { id: record.source_record_id }) }, "Open incident")),
  ]);
}

function shotList(params, incidents) {
  const records = (incidents && incidents.records) || [];
  const total = incidents && incidents.total != null ? incidents.total : records.length;
  const limit = Number(incidents && incidents.limit) || records.length || 50;
  const offset = Number(incidents && incidents.offset) || 0;
  const page = Math.floor(offset / Math.max(limit, 1)) + 1;
  const pages = Math.max(1, Math.ceil(total / Math.max(limit, 1)));
  const exportParams = new URLSearchParams(shotQuery(params).replace(/^\?/, ""));
  exportParams.delete("offset");
  exportParams.set("format", "csv");
  exportParams.set("limit", "2000");
  return h("div", { id: "shot-list-slot" }, [
    h("div", { class: "section-bar" }, [
      h("h2", null, "Incident activity"),
      h("a", { href: "/api/shotspotter-detroit/incidents?" + exportParams.toString() }, "Export public records"),
    ]),
    records.length
      ? h("div", null, records.map(function (record) { return shotCard(record, params); }))
      : h("p", { class: "empty" }, "No stored records match these filters."),
    h("div", { class: "shot-pager" }, [
      offset > 0
        ? h("a", { href: shotHash(params, { offset: String(Math.max(0, offset - limit)) }) }, "Previous")
        : h("span", { class: "muted" }, "Previous"),
      h("span", null, "Page " + page + " of " + pages + " · " + total + " records"),
      offset + limit < total
        ? h("a", { href: shotHash(params, { offset: String(offset + limit) }) }, "Next")
        : h("span", { class: "muted" }, "Next"),
    ]),
  ]);
}

function shotDetail(record, error) {
  if (error) return h("div", { id: "shot-detail-slot", class: "shot-detail" }, errorBlock(error));
  if (!record) {
    return h("div", { id: "shot-detail-slot", class: "shot-detail card" }, [
      h("h2", null, "Incident detail"),
      h("p", { class: "muted" }, "Select a record or a map marker. No acoustic audio is published with these incidents."),
    ]);
  }
  const rows = [
    ["Incident ID", record.incident_id || record.source_record_id],
    ["Event time", shotWhen(record.called_at)],
    ["Source publication time", "Not supplied for each incident"],
    ["Dataset publication time", shotWhen(record.dataset_edited_at)],
    ["Classification", record.classification_label],
    ["Published code", record.call_code],
    ["Latitude", record.plottable ? record.latitude : "Not plotted"],
    ["Longitude", record.plottable ? record.longitude : "Not plotted"],
    ["Location", record.intersection || record.location_note],
    ["Data source", record.source_name],
    ["Retrieved", shotWhen(record.retrieved_at)],
    ["First stored", shotWhen(record.first_retrieved_at)],
    ["Freshness", record.freshness],
    ["Event age", shotDuration(record.event_age_seconds)],
    ["Publication delay", record.publication_delay_seconds == null ? "Not available" : shotDuration(record.publication_delay_seconds)],
    ["Retrieval delay", record.retrieval_delay_seconds == null ? "Not available" : shotDuration(record.retrieval_delay_seconds)],
  ];
  return h("div", { id: "shot-detail-slot", class: "shot-detail card" }, [
    record.newly_retrieved_historical
      ? h("p", { class: "historical-banner" }, record.verification_note)
      : h("p", { class: "muted" }, record.verification_note),
    h("h2", null, shotText(record.incident_id || record.source_record_id)),
    h("p", null, h("span", { class: shotFeedClass(record.freshness) }, record.freshness || "UNVERIFIED")),
    h("p", { class: "muted" }, "No acoustic audio is published with this incident."),
    h("dl", null, rows.map(function (pair) {
      return [h("dt", null, pair[0]), h("dd", null, shotText(pair[1]))];
    })),
    record.publication_delay_reason ? h("p", { class: "footnote" }, record.publication_delay_reason) : null,
    record.retrieval_delay_reason ? h("p", { class: "footnote" }, record.retrieval_delay_reason) : null,
    shotLink(record.source_url, "Open the official dataset"),
  ]);
}

function shotSources(payload) {
  const providers = (payload && payload.providers) || [];
  return h("section", { id: "shot-sources-slot" }, [
    h("h2", null, "Other cities"),
    h("p", { class: "muted" }, payload && payload.message ? payload.message : "Secondary cities stay disabled until a source is verified."),
    h("div", null, providers.map(function (item) {
      return h("article", { class: "card" }, [
        h("h3", null, item.city),
        h("p", null, item.title),
        h("p", null, h("span", { class: shotFeedClass(item.feed_status) }, item.feed_status || "UNVERIFIED")),
        h("p", { class: "muted" }, item.enabled ? "Enabled for this dashboard." : "Not enabled."),
        h("p", null, item.notes),
        shotLink(item.source_url, "Source page"),
      ]);
    })),
  ]);
}

function shotGroupPoints(points) {
  const groups = {};
  (points || []).forEach(function (point) {
    if (typeof point.latitude !== "number" || typeof point.longitude !== "number") return;
    const key = point.latitude.toFixed(4) + "," + point.longitude.toFixed(4);
    if (!groups[key]) groups[key] = { latitude: point.latitude, longitude: point.longitude, ids: [], classification: point.classification };
    groups[key].ids.push(point.source_record_id);
  });
  return Object.keys(groups).map(function (key) { return groups[key]; });
}

function shotCluster(map, groups) {
  const plain = groups.map(function (group) {
    return { latitude: group.latitude, longitude: group.longitude, ids: group.ids.slice(), members: [group], classification: group.classification };
  });
  if (!map || typeof map.latLngToLayerPoint !== "function" || map.getZoom() >= 15) return plain;
  const buckets = {};
  groups.forEach(function (group) {
    const point = map.latLngToLayerPoint([group.latitude, group.longitude]);
    const key = Math.floor(point.x / 64) + ":" + Math.floor(point.y / 64);
    if (!buckets[key]) buckets[key] = [];
    buckets[key].push(group);
  });
  return Object.keys(buckets).map(function (key) {
    const members = buckets[key];
    let latitude = 0;
    let longitude = 0;
    let weight = 0;
    const ids = [];
    members.forEach(function (group) {
      const count = group.ids.length;
      latitude += group.latitude * count;
      longitude += group.longitude * count;
      weight += count;
      group.ids.forEach(function (id) { ids.push(id); });
    });
    return { latitude: latitude / weight, longitude: longitude / weight, ids: ids, members: members, classification: members[0].classification };
  });
}

function shotMarkerStyle(chosen, count, classification, grouped) {
  const style = {
    radius: Math.min(6 + count, 16),
    color: chosen ? "#ffffff" : "#071018",
    weight: chosen ? 2 : 1,
    fillColor: SHOT_CLASS_COLOR[classification] || "#8ea0bd",
    fillOpacity: 0.92,
  };
  if (grouped) {
    style.radius = Math.max(12, Math.min(28, 8 + Math.round(Math.sqrt(count))));
    style.fillColor = "#e7eef8";
    style.color = "#102033";
    style.fillOpacity = 0.94;
  }
  return style;
}

function shotBindMarker(marker, label, onActivate) {
  function apply() {
    const element = marker.getElement();
    if (!element || element.dataset.shotBound === "1") return;
    element.dataset.shotBound = "1";
    element.setAttribute("tabindex", "0");
    element.setAttribute("role", "button");
    element.setAttribute("aria-label", label);
    element.setAttribute("title", label);
    element.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        onActivate();
      }
    });
  }
  marker.on("add", apply);
  apply();
}

function shotOpen(recordId, params) {
  location.hash = shotHash(params, { id: recordId });
}

function shotZoomCluster(map, cluster) {
  const bounds = L.latLngBounds(cluster.members.map(function (group) {
    return [group.latitude, group.longitude];
  }));
  const nextZoom = Math.min((map.getZoom() || 11) + 2, 18);
  const northeast = bounds.getNorthEast && bounds.getNorthEast();
  const southwest = bounds.getSouthWest && bounds.getSouthWest();
  if (northeast && southwest && northeast.equals && northeast.equals(southwest)) {
    map.setView(bounds.getCenter(), nextZoom);
    return;
  }
  map.fitBounds(bounds, { padding: [40, 40], maxZoom: nextZoom });
}

function shotAddMarkers(map, points, selected, params) {
  const markers = [];
  shotCluster(map, shotGroupPoints(points)).forEach(function (cluster) {
    const grouped = cluster.members.length > 1;
    const chosen = cluster.ids.indexOf(selected) !== -1;
    const label = grouped
      ? cluster.ids.length + " published CAD records in this area. Activate to zoom in."
      : "Published CAD record " + cluster.ids[0];
    const activate = grouped
      ? function () { shotZoomCluster(map, cluster); }
      : function () { shotOpen(cluster.ids[0], params); };
    const marker = L.circleMarker(
      [cluster.latitude, cluster.longitude],
      shotMarkerStyle(chosen, cluster.ids.length, cluster.classification, grouped)
    );
    if (grouped || cluster.ids.length > 1) {
      marker.bindTooltip(String(cluster.ids.length), { permanent: true, direction: "top", opacity: 1 });
    }
    marker.on("click", activate);
    shotBindMarker(marker, label, activate);
    marker.addTo(map);
    markers.push({ ids: cluster.ids, marker: marker, grouped: grouped, classification: cluster.classification });
  });
  return markers;
}

function shotPlace(map, points) {
  const frame = L.latLngBounds([SHOT_FRAME.south, SHOT_FRAME.west], [SHOT_FRAME.north, SHOT_FRAME.east]);
  const coords = [];
  (points || []).forEach(function (point) {
    if (typeof point.latitude === "number" && typeof point.longitude === "number") coords.push([point.latitude, point.longitude]);
  });
  shotMapState.suspendZoom = true;
  if (coords.length) map.fitBounds(L.latLngBounds(coords), { padding: [28, 28], maxZoom: 15, animate: false });
  else map.fitBounds(frame, { padding: [12, 12], animate: false });
  shotMapState.suspendZoom = false;
  shotMapState.markerZoom = map.getZoom();
}

function shotReplaceMarkers(points, selected, params) {
  const map = shotMapState.map;
  if (!map) return;
  const center = map.getCenter();
  const zoom = map.getZoom();
  shotMapState.markerZoom = zoom;
  (shotMapState.markers || []).forEach(function (entry) { map.removeLayer(entry.marker); });
  shotMapState.markers = shotAddMarkers(map, points, selected, params);
  shotMapState.points = points || [];
  map.setView(center, zoom, { animate: false });
}

function mountShotMap(container, points, selected, params, tileUrl) {
  if (typeof L === "undefined") {
    container.appendChild(h("p", { class: "empty" }, "The map did not load."));
    return null;
  }
  const map = L.map(container, {
    attributionControl: true,
    zoomControl: true,
    scrollWheelZoom: true,
    minZoom: 9,
    maxZoom: 19,
  });
  container.setAttribute("aria-label", "Satellite map of published Detroit ShotSpotter CAD locations for the current filter");
  const imagery = L.tileLayer(SHOT_SATELLITE_TILES, { attribution: SHOT_SATELLITE_ATTRIBUTION, maxNativeZoom: 19, maxZoom: 19 });
  const labels = L.tileLayer(SHOT_SATELLITE_LABELS, { maxNativeZoom: 19, maxZoom: 19, pane: "overlayPane" });
  imagery.addTo(map);
  labels.addTo(map);
  if (tileUrl) {
    const streets = L.tileLayer(tileUrl, {
      attribution: SHOT_VOYAGER_ATTRIBUTION,
      referrerPolicy: "origin",
      maxNativeZoom: 18,
      maxZoom: 19,
    });
    L.control.layers({ Satellite: imagery, Streets: streets }, { "Place names": labels }, { collapsed: false }).addTo(map);
  }
  shotPlace(map, points);
  const markers = shotAddMarkers(map, points, selected, params);
  map.on("zoomend", function () {
    if (shotMapState.suspendZoom || shotMapState.map !== map) return;
    if (map.getZoom() === shotMapState.markerZoom) return;
    const live = typeof parseHash === "function" ? parseHash().params : params;
    shotReplaceMarkers(shotMapState.points || [], live.get("id") || "", live);
  });
  requestAnimationFrame(function () {
    if (!shotMapState.map) return;
    shotMapState.map.invalidateSize();
  });
  return { map: map, markers: markers };
}

function disposeShotspotterMap() {
  if (shotMapState.map) shotMapState.map.remove();
  shotMapState.map = null;
  shotMapState.root = null;
  shotMapState.markers = [];
  shotMapState.points = [];
  shotMapState.filterKey = "";
  shotMapState.markerZoom = null;
}

function stopShotspotterRefresh() {
  if (shotRefreshTimer) clearInterval(shotRefreshTimer);
  shotRefreshTimer = null;
}

function shotFiltersBusy() {
  const active = document.activeElement;
  if (!active || typeof active.closest !== "function") return false;
  if (!active.closest("form.shot-filters")) return false;
  const tag = String(active.tagName || "").toLowerCase();
  return tag === "input" || tag === "select" || tag === "textarea";
}

function shotLimitToView() {
  const map = shotMapState.map;
  if (!map) return;
  const bounds = map.getBounds();
  const params = typeof parseHash === "function" ? parseHash().params : new URLSearchParams();
  location.hash = shotHash(params, {
    west: bounds.getWest().toFixed(5),
    south: bounds.getSouth().toFixed(5),
    east: bounds.getEast().toFixed(5),
    north: bounds.getNorth().toFixed(5),
    offset: "",
  });
}

function paintShot(view, params, status, stats, incidents, mapData, sources, detail, detailError, tileUrl) {
  const selected = params.get("id") || "";
  const points = (mapData && mapData.points) || [];
  const container = h("div", { class: "shot-map" });
  view.replaceChildren(
    h("header", { class: "page-head" }, [
      h("p", { class: "eyebrow" }, "Detroit, Michigan"),
      h("h1", null, "SHOTSPOTTER DETROIT"),
      h("p", { class: "lede" }, "Published Detroit Police CAD records initiated by the gunshot detection system. This is not a live alert feed."),
    ]),
    h("section", { class: "detroit-notice", role: "note" }, [
      h("p", null, [h("strong", null, "Not a live alert feed. "), status.notice || "These points are published CAD records."]),
      h("p", null, "No acoustic audio is published with these incidents."),
      h("details", null, [
        h("summary", null, "What this dataset includes"),
        h("p", null, status.coverage || ""),
        h("p", null, status.import_note || ""),
        h("p", null, status.publication_delay_note || ""),
      ]),
    ]),
    shotStatusBlock(status),
    shotStatsBlock(stats),
    h("p", { class: "footnote" }, [
      shotLink(status.source_url, "Open the official dataset"),
      " ",
      shotLink(status.service_url, "Open the feature service"),
    ]),
    shotForm(params, status),
    h("div", { class: "detroit-layout" }, [
      h("div", null, [
        h("div", { class: "section-bar" }, [
          h("h2", null, "Incident map"),
          h("button", { type: "button", onclick: shotLimitToView }, "Filter to this map view"),
        ]),
        h("p", { class: "footnote" }, (mapData && mapData.plotted != null ? mapData.plotted : points.length) + " plotted · " + ((mapData && mapData.unplotted) || 0) + " stored records have no reliable coordinates and stay in the list."),
        h("div", { class: "priority-legend" }, [
          h("span", null, [h("i", { class: "swatch swatch-single" }), " Single"]),
          h("span", null, [h("i", { class: "swatch swatch-multiple" }), " Multiple"]),
          h("span", null, [h("i", { class: "swatch swatch-probable" }), " Probable"]),
          h("span", null, [h("i", { class: "swatch swatch-other" }), " Other or unclassified"]),
        ]),
        container,
      ]),
      shotDetail(detail, detailError),
    ]),
    shotList(params, incidents),
    shotSources(sources)
  );
  const mounted = mountShotMap(container, points, selected, params, tileUrl);
  shotMapState.filterKey = shotQuery(params);
  shotMapState.points = points;
  shotMapState.root = container;
  if (mounted) {
    shotMapState.map = mounted.map;
    shotMapState.markers = mounted.markers;
  }
}

function shotReplaceSlot(id, node) {
  const currentNode = document.getElementById(id);
  if (!currentNode || !currentNode.parentNode) return;
  currentNode.replaceWith(node);
}

async function refreshShotQuiet(view, gen) {
  if (typeof current === "function" && !current(gen)) return;
  if (shotFiltersBusy()) return;
  if (!shotMapState.map || !shotMapState.root || !shotMapState.root.isConnected) return;
  const params = typeof parseHash === "function" ? parseHash().params : new URLSearchParams();
  const filterKey = shotQuery(params);
  if (filterKey !== shotMapState.filterKey) return;
  let loaded;
  try {
    loaded = await Promise.all([
      api("/shotspotter-detroit/status"),
      api("/shotspotter-detroit/statistics"),
      api("/shotspotter-detroit/incidents" + filterKey),
      api("/shotspotter-detroit/map" + filterKey),
    ]);
  } catch (err) {
    return;
  }
  if (typeof current === "function" && !current(gen)) return;
  if (!shotMapState.map) return;
  const live = typeof parseHash === "function" ? parseHash().params : params;
  if (shotQuery(live) !== filterKey) return;
  shotReplaceSlot("shot-status-slot", shotStatusBlock(loaded[0]));
  shotReplaceSlot("shot-stats-slot", shotStatsBlock(loaded[1]));
  shotReplaceSlot("shot-list-slot", shotList(live, loaded[2]));
  shotReplaceMarkers((loaded[3] && loaded[3].points) || [], live.get("id") || "", live);
  const selected = live.get("id") || "";
  if (!selected) {
    shotReplaceSlot("shot-detail-slot", shotDetail(null, ""));
    return;
  }
  try {
    const payload = await api("/shotspotter-detroit/incidents/" + encodeURIComponent(selected));
    if (typeof current === "function" && !current(gen)) return;
    shotReplaceSlot("shot-detail-slot", shotDetail(payload.record || null, ""));
  } catch (err) {
    if (typeof current === "function" && !current(gen)) return;
    shotReplaceSlot("shot-detail-slot", shotDetail(null, err.message));
  }
}

function startShotRefresh(view, gen) {
  stopShotspotterRefresh();
  shotRefreshTimer = setInterval(function () { refreshShotQuiet(view, gen); }, SHOT_REFRESH_MS);
}

async function renderShotspotter(view, gen, params) {
  startShotRefresh(view, gen);
  const filterKey = shotQuery(params);
  const keep = !!(shotMapState.map && shotMapState.root && shotMapState.root.isConnected && shotMapState.filterKey === filterKey);
  if (keep) {
    const selected = params.get("id") || "";
    shotReplaceMarkers(shotMapState.points || [], selected, params);
    if (!selected) {
      shotReplaceSlot("shot-detail-slot", shotDetail(null, ""));
      return;
    }
    try {
      const payload = await api("/shotspotter-detroit/incidents/" + encodeURIComponent(selected));
      if (typeof current !== "function" || current(gen)) shotReplaceSlot("shot-detail-slot", shotDetail(payload.record || null, ""));
    } catch (err) {
      if (typeof current !== "function" || current(gen)) shotReplaceSlot("shot-detail-slot", shotDetail(null, err.message));
    }
    return;
  }
  disposeShotspotterMap();
  view.replaceChildren(h("p", null, "Loading published ShotSpotter records…"));
  let status;
  let stats;
  let incidents;
  let mapData;
  let sources;
  let basemap = null;
  try {
    const loaded = await Promise.all([
      api("/shotspotter-detroit/status"),
      api("/shotspotter-detroit/statistics"),
      api("/shotspotter-detroit/incidents" + filterKey),
      api("/shotspotter-detroit/map" + filterKey),
      api("/shotspotter-detroit/sources"),
    ]);
    if (typeof current === "function" && !current(gen)) return;
    status = loaded[0];
    stats = loaded[1];
    incidents = loaded[2];
    mapData = loaded[3];
    sources = loaded[4];
    try { basemap = await api("/detroit/basemap"); } catch (err) { basemap = null; }
  } catch (err) {
    if (typeof current === "function" && !current(gen)) return;
    view.replaceChildren(errorBlock(err.message || "The ShotSpotter records could not be loaded."));
    return;
  }
  if (typeof current === "function" && !current(gen)) return;
  let detail = null;
  let detailError = "";
  const selected = params.get("id") || "";
  if (selected) {
    try {
      const payload = await api("/shotspotter-detroit/incidents/" + encodeURIComponent(selected));
      if (typeof current === "function" && !current(gen)) return;
      detail = payload.record || null;
    } catch (err) {
      detailError = err.message || "That incident could not be loaded.";
    }
  }
  if (typeof current === "function" && !current(gen)) return;
  paintShot(view, params, status, stats, incidents, mapData, sources, detail, detailError, shotVoyager(basemap));
}
