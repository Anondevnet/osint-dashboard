/* Detroit police-serviced 911 calls. Published open data, not a live dispatch feed. */

var DETROIT_FRAME = { west: -83.4, east: -82.8, south: 42.2, north: 42.5 };
var DETROIT_FILTERS = ["from", "to", "category", "priority", "precinct", "source", "call_source", "freshness", "offset"];
var VOYAGER_TILE_PREFIX = "https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png?key=";
var VOYAGER_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, &copy; <a href="https://carto.com/attribution/">CARTO</a>';
var SATELLITE_TILES = "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
var SATELLITE_LABELS = "https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}";
var SATELLITE_ATTRIBUTION = 'Powered by Esri. Imagery: Esri, Vantor, Earthstar Geographics, and the GIS User Community. Labels: Esri, HERE, Garmin, &copy; OpenStreetMap contributors, and the GIS user community.';
var detroitMapState = { map: null, root: null, markers: [], points: [], records: [], filterKey: "", markerZoom: null, suspendZoom: false, listKey: "" };
var detroitListShown = 25;
var DETROIT_REFRESH_MS = 30000;
var detroitRefreshTimer = null;

function detroitQuery(params) {
  const search = new URLSearchParams();
  DETROIT_FILTERS.forEach(function (key) {
    const value = params.get(key);
    if (value) search.set(key, value);
  });
  if (!search.get("limit")) search.set("limit", "2000");
  const text = search.toString();
  return text ? "?" + text : "";
}

function detroitHash(params, extra) {
  const search = new URLSearchParams(detroitQuery(params).replace(/^\?/, ""));
  Object.keys(extra || {}).forEach(function (key) {
    const value = extra[key];
    if (value == null || value === "") search.delete(key);
    else search.set(key, String(value));
  });
  const text = search.toString();
  return text ? "#/detroit?" + text : "#/detroit";
}

function toLocalInput(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = function (number) { return String(number).padStart(2, "0"); };
  return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate())
    + "T" + pad(date.getHours()) + ":" + pad(date.getMinutes());
}

function fromLocalInput(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toISOString().replace(/\.\d{3}Z$/, "Z");
}

function officialLink(url, label) {
  if (typeof url !== "string") return null;
  const allowed = url.indexOf("https://data.detroitmi.gov/") === 0
    || url.indexOf("https://services2.arcgis.com/") === 0;
  if (!allowed) return null;
  return h("a", { href: url, rel: "noreferrer" }, label);
}

function supplied(value) {
  return value == null || value === "" ? "Not supplied" : String(value);
}

function metaLine(label, value) {
  return h("span", { class: "incident-meta" }, [
    h("span", { class: "faint" }, label + " "),
    supplied(value),
  ]);
}

function freshnessName(value) {
  if (value === "recently_retrieved") return "Recently retrieved";
  if (value === "stored") return "Stored";
  if (value === "stale") return "Stale";
  return "Not labeled";
}

function selectOptions(values, selected) {
  const items = [h("option", { value: "" }, "All")];
  const seen = {};
  (values || []).forEach(function (value) {
    const text = String(value);
    seen[text] = true;
    items.push(h("option", { value: text, selected: text === selected }, text));
  });
  if (selected && !seen[selected]) {
    items.push(h("option", { value: selected, selected: true }, selected));
  }
  return items;
}

function detroitNotice(payload) {
  return h("section", { class: "detroit-notice", role: "note" }, [
    h("p", null, [
      h("strong", null, "Update schedule is not guaranteed. "),
      "A point is a published call, not a call happening now and not a unit responding.",
    ]),
    h("details", null, [
      h("summary", null, "What this dataset includes"),
      h("p", null, payload && payload.notice
        ? payload.notice
        : "The City of Detroit does not guarantee when this dataset is refreshed. A point on this page is not a call that is happening now."),
      h("p", null, payload && payload.coverage
        ? payload.coverage
        : "These are publicly released police calls-for-service records. The dataset does not include every emergency call."),
      h("p", null, payload && payload.import_note
        ? payload.import_note
        : "Each refresh reads a recent time window. This page does not download the full archive."),
    ]),
  ]);
}

function labelBlock(payload) {
  return h("p", { class: "footnote" }, "SOURCE: " + payload.source_name + " · DATA TYPE: " + payload.data_type + " · VERIFICATION: " + payload.verification);
}

function syncStateName(state) {
  if (state === "current") return "Check on schedule";
  if (state === "delayed") return "Check delayed";
  if (state === "stale") return "Check stale";
  if (state === "error") return "Check failed";
  if (state === "checking") return "Check in progress";
  return "Not checked yet";
}

function intervalText(seconds) {
  const value = Number(seconds);
  if (!value) return "Off";
  if (value % 60 === 0) return "Every " + (value / 60) + " min";
  return "Every " + value + " sec";
}

function syncCheckedAt(status) {
  if (status.last_checked_at) return status.last_checked_at;
  const latest = status.latest_run;
  if (!latest) return "";
  return latest.finished_at || latest.started_at || "";
}

function syncSucceededAt(status) {
  if (status.last_success_at) return status.last_success_at;
  const success = status.last_success;
  return success && success.finished_at ? success.finished_at : "";
}

function syncPublishedAt(status) {
  if (status.source_edited_at) return status.source_edited_at;
  const success = status.last_success;
  return success && success.source_edited_at ? success.source_edited_at : "";
}

function syncCards(status) {
  const checked = syncCheckedAt(status);
  const succeeded = syncSucceededAt(status);
  const edited = syncPublishedAt(status);
  return h("div", { class: "detroit-strip" }, [
    h("div", { class: "stat" }, [
      h("span", { class: "stat-value" }, formatNumber(status.local_records)),
      h("span", { class: "stat-label" }, "Stored records"),
    ]),
    h("div", { class: "stat" }, [
      h("span", { class: "stat-value" }, status.newest_event_at ? formatTime(status.newest_event_at) : "None"),
      h("span", { class: "stat-label" }, "Newest call received"),
    ]),
    h("div", { class: "stat" }, [
      h("span", { class: "stat-value" }, checked ? formatTime(checked) : "None"),
      h("span", { class: "stat-label" }, "Last checked"),
    ]),
    h("div", { class: "stat" }, [
      h("span", { class: "stat-value" }, edited ? formatTime(edited) : "Not reported"),
      h("span", { class: "stat-label" }, "City publication timestamp"),
    ]),
    h("div", { class: "stat sync-" + (status.sync_state || "never") }, [
      h("span", { class: "stat-value" }, syncStateName(status.sync_state)),
      h("span", { class: "stat-label" }, intervalText(status.sync_interval_seconds == null ? 300 : status.sync_interval_seconds)),
    ]),
    h("p", { class: "footnote" }, "Last successful synchronization: " + (succeeded ? formatTime(succeeded) : "none") + ". " + (status.publication_note || "The check time, the city publication timestamp, and the call-received time are different. These rows are published records, not live incidents.")),
  ]);
}

function filterForm(params, facets) {
  return h("form", {
    class: "filters",
    onsubmit: function (event) {
      event.preventDefault();
      const data = new FormData(event.currentTarget);
      const search = new URLSearchParams();
      ["from", "to", "category", "priority", "precinct", "source", "call_source", "freshness"].forEach(function (key) {
        let value = String(data.get(key) || "").trim();
        if ((key === "from" || key === "to") && value) value = fromLocalInput(value);
        if (value) search.set(key, value);
      });
      const text = search.toString();
      location.hash = text ? "#/detroit?" + text : "#/detroit";
    },
  }, [
    h("label", { class: "field" }, [
      h("span", null, "From"),
      h("input", { type: "datetime-local", name: "from", value: toLocalInput(params.get("from")) }),
    ]),
    h("label", { class: "field" }, [
      h("span", null, "To"),
      h("input", { type: "datetime-local", name: "to", value: toLocalInput(params.get("to")) }),
    ]),
    h("label", { class: "field" }, [
      h("span", null, "Incident type"),
      h("select", { name: "category" }, selectOptions(facets.category, params.get("category") || "")),
    ]),
    h("label", { class: "field" }, [
      h("span", null, "Priority"),
      h("select", { name: "priority" }, selectOptions(facets.priority, params.get("priority") || "")),
    ]),
    h("label", { class: "field" }, [
      h("span", null, "Precinct"),
      h("select", { name: "precinct" }, selectOptions(facets.precinct, params.get("precinct") || "")),
    ]),
    h("label", { class: "field" }, [
      h("span", null, "Source"),
      h("select", { name: "source" }, selectOptions(facets.source, params.get("source") || "")),
    ]),
    h("label", { class: "field" }, [
      h("span", null, "Call line"),
      h("select", { name: "call_source" }, selectOptions(facets.call_source, params.get("call_source") || "")),
    ]),
    h("label", { class: "field" }, [
      h("span", null, "Data freshness"),
      h("select", { name: "freshness" }, [
        h("option", { value: "" }, "All"),
        h("option", { value: "recently_retrieved", selected: params.get("freshness") === "recently_retrieved" }, "Recently retrieved"),
        h("option", { value: "stored", selected: params.get("freshness") === "stored" }, "Stored"),
        h("option", { value: "stale", selected: params.get("freshness") === "stale" }, "Stale"),
      ]),
    ]),
    h("button", { class: "btn btn-primary", type: "submit" }, "Apply filters"),
    h("a", { class: "btn", href: "#/detroit" }, "Clear"),
  ]);
}

function voyagerTileUrl(payload) {
  if (!payload || payload.enabled !== true || typeof payload.tile_url !== "string") return "";
  if (payload.tile_url.indexOf(VOYAGER_TILE_PREFIX) !== 0) return "";
  const key = payload.tile_url.slice(VOYAGER_TILE_PREFIX.length);
  if (!/^[A-Za-z0-9._~%-]{8,600}$/.test(key)) return "";
  return payload.tile_url;
}

var PRIORITY_COLOR = {
  P: "#ff5d73",
  "1": "#ff8a3d",
  "2": "#e15cff",
  "3": "#3ee0ff",
  "4": "#7aa2ff",
  "5": "#3dffb0",
};

function priorityTone(priority) {
  const key = String(priority == null ? "" : priority).toUpperCase();
  return PRIORITY_COLOR[key] || "#8ea0bd";
}

function priorityClass(priority) {
  const key = String(priority == null ? "" : priority).toUpperCase();
  return PRIORITY_COLOR[key] ? " severity-" + key.toLowerCase() : "";
}

function pointStyle(chosen, count, priority) {
  return {
    radius: Math.min(5 + count, 14),
    color: chosen ? "#ffffff" : "#071018",
    weight: chosen ? 2 : 1,
    fillColor: priorityTone(priority),
    fillOpacity: 0.92,
  };
}

function clusterStyle(chosen, count, priority, grouped) {
  const style = pointStyle(chosen, grouped ? Math.min(count, 8) : count, priority);
  if (grouped) {
    style.radius = Math.max(12, Math.min(28, 8 + Math.round(Math.sqrt(count))));
    style.fillColor = "#e7eef8";
    style.color = "#102033";
    style.fillOpacity = 0.94;
  }
  return style;
}

function canKeepDetroitMap(filterKey) {
  return !!(
    detroitMapState.map
    && detroitMapState.filterKey === filterKey
    && detroitMapState.root
    && detroitMapState.root.isConnected
  );
}

function disposeDetroitMap() {
  if (detroitMapState.map) detroitMapState.map.remove();
  detroitMapState.map = null;
  detroitMapState.root = null;
  detroitMapState.markers = [];
  detroitMapState.points = [];
  detroitMapState.records = [];
  detroitMapState.markerZoom = null;
  detroitMapState.filterKey = "";
}

function currentDetroitParams(fallback) {
  if (typeof parseHash === "function") return parseHash().params;
  return fallback;
}

function groupPoints(points) {
  const groups = {};
  (points || []).forEach(function (point) {
    if (typeof point.latitude !== "number" || typeof point.longitude !== "number") return;
    const key = point.latitude.toFixed(4) + "," + point.longitude.toFixed(4);
    if (!groups[key]) {
      groups[key] = { latitude: point.latitude, longitude: point.longitude, ids: [] };
    }
    groups[key].ids.push(point.source_record_id);
  });
  return Object.keys(groups).map(function (key) { return groups[key]; });
}

function openPublishedRecord(recordId, fallbackParams) {
  location.hash = detroitHash(currentDetroitParams(fallbackParams), { id: recordId });
}

function placeDetroitMap(map, points) {
  const frame = L.latLngBounds(
    [DETROIT_FRAME.south, DETROIT_FRAME.west],
    [DETROIT_FRAME.north, DETROIT_FRAME.east]
  );
  const coords = [];
  (points || []).forEach(function (point) {
    if (typeof point.latitude === "number" && typeof point.longitude === "number") {
      coords.push([point.latitude, point.longitude]);
    }
  });
  detroitMapState.suspendZoom = true;
  if (coords.length) map.fitBounds(L.latLngBounds(coords), { padding: [28, 28], maxZoom: 15, animate: false });
  else map.fitBounds(frame, { padding: [12, 12], animate: false });
  detroitMapState.suspendZoom = false;
  detroitMapState.markerZoom = map.getZoom();
}

function zoomToCluster(map, cluster) {
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

function clusterGroups(map, groups) {
  const plain = groups.map(function (group) {
    return { latitude: group.latitude, longitude: group.longitude, ids: group.ids.slice(), members: [group] };
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
    return {
      latitude: latitude / weight,
      longitude: longitude / weight,
      ids: ids,
      members: members,
    };
  });
}

function bestPriority(ids, byId) {
  const rank = { P: 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5 };
  let best = null;
  let bestRank = 99;
  ids.forEach(function (id) {
    const record = byId[id];
    if (!record || record.priority == null || record.priority === "") return;
    const key = String(record.priority).toUpperCase();
    const score = Object.prototype.hasOwnProperty.call(rank, key) ? rank[key] : 50;
    if (score < bestRank) {
      bestRank = score;
      best = record.priority;
    }
  });
  return best;
}

function bindMarkerElement(marker, label, onActivate) {
  function apply() {
    const element = marker.getElement();
    if (!element || element.dataset.detroitBound === "1") return;
    element.dataset.detroitBound = "1";
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

function addDetroitMarkers(map, points, records, selected, params) {
  const byId = {};
  (records || []).forEach(function (record) { byId[record.source_record_id] = record; });
  const markers = [];
  clusterGroups(map, groupPoints(points)).forEach(function (cluster) {
    const grouped = cluster.members.length > 1;
    const chosen = cluster.ids.indexOf(selected) !== -1;
    const record = byId[cluster.ids[0]];
    const priority = bestPriority(cluster.ids, byId);
    const place = record && record.intersection ? record.intersection : "published intersection";
    const label = grouped
      ? cluster.ids.length + " published calls in this area. Activate to zoom in."
      : cluster.ids.length + " published call" + (cluster.ids.length === 1 ? "" : "s") + " at " + place;
    const activate = grouped
      ? function () { zoomToCluster(map, cluster); }
      : function () { openPublishedRecord(cluster.ids[0], params); };
    const marker = L.circleMarker([cluster.latitude, cluster.longitude], clusterStyle(chosen, cluster.ids.length, priority, grouped));
    if (grouped || cluster.ids.length > 1) {
      marker.bindTooltip(String(cluster.ids.length), {
        permanent: true,
        direction: "top",
        className: "detroit-count",
        opacity: 1,
      });
    }
    marker.on("click", activate);
    bindMarkerElement(marker, label, activate);
    marker.addTo(map);
    markers.push({ ids: cluster.ids, marker: marker, priority: priority, grouped: grouped });
  });
  return markers;
}

function mountDetroitMap(container, points, records, selected, params, tileUrl) {
  if (typeof L === "undefined") {
    container.appendChild(h("p", { class: "empty" }, "The street map did not load."));
    return null;
  }
  const map = L.map(container, {
    attributionControl: true,
    zoomControl: true,
    scrollWheelZoom: true,
    doubleClickZoom: true,
    dragging: true,
    keyboard: true,
    touchZoom: true,
    boxZoom: true,
    minZoom: 9,
    maxZoom: 19,
  });
  container.setAttribute("aria-label", "Satellite map of published Detroit nearest-intersection points for the current filter");
  const imagery = L.tileLayer(SATELLITE_TILES, {
    attribution: SATELLITE_ATTRIBUTION,
    maxNativeZoom: 19,
    maxZoom: 19,
  });
  const labels = L.tileLayer(SATELLITE_LABELS, {
    maxNativeZoom: 19,
    maxZoom: 19,
    pane: "overlayPane",
  });
  imagery.addTo(map);
  labels.addTo(map);
  if (tileUrl) {
    const streets = L.tileLayer(tileUrl, {
      attribution: VOYAGER_ATTRIBUTION,
      referrerPolicy: "origin",
      maxNativeZoom: 18,
      maxZoom: 19,
    });
    L.control.layers(
      { Satellite: imagery, Streets: streets },
      { "Place names": labels },
      { collapsed: false }
    ).addTo(map);
  }
  placeDetroitMap(map, points);
  const markers = addDetroitMarkers(map, points, records, selected, params);
  map.on("zoomend", function () {
    if (detroitMapState.suspendZoom || detroitMapState.map !== map) return;
    if (map.getZoom() === detroitMapState.markerZoom) return;
    const liveParams = currentDetroitParams(params);
    replaceDetroitMarkers(detroitMapState.points || [], detroitMapState.records || [], liveParams.get("id") || "", liveParams);
  });
  requestAnimationFrame(function () {
    if (!detroitMapState.map) return;
    detroitMapState.map.invalidateSize();
    placeDetroitMap(detroitMapState.map, detroitMapState.points || points);
    const liveParams = currentDetroitParams(params);
    replaceDetroitMarkers(detroitMapState.points || points, detroitMapState.records || records, liveParams.get("id") || "", liveParams);
  });
  return { map: map, markers: markers };
}

function incidentMap(points, records, selected, params, tileUrl) {
  const groups = groupPoints(points);
  const container = h("div", {
    class: "detroit-map",
    id: "detroit-leaflet",
  });
  return {
    card: h("div", { class: "card" }, [
      h("div", { class: "card-head" }, [
        h("h2", null, "Published locations"),
        h("span", { class: "faint", id: "detroit-point-count" }, groups.length + " intersection points"),
      ]),
      container,
      h("div", { class: "priority-legend", "aria-label": "Published priority colors" }, [
        ["P", "P"], ["1", "1"], ["2", "2"], ["3", "3"], ["4", "4"], ["5", "5"]
      ].map(function (item) {
        return h("span", null, [
          h("i", { class: "swatch swatch-" + item[0].toLowerCase() }),
          item[1],
        ]);
      })),
      h("p", { class: "footnote" }, "Marker color is the city's published priority. P is highest, then 1 through 5. It is not an independent severity score."),
      h("p", { class: "footnote" }, "Zoomed out, nearby calls combine into a count. Zoom in, or activate a count, to see each published intersection. Satellite imagery can be dragged and zoomed with the scroll wheel, a pinch, a double-click, or the + and − controls. No address was reverse-geocoded. There is no live unit layer."
        + (tileUrl ? " The Streets choice uses the CARTO Voyager basemap." : "")),
    ]),
    container: container,
    groups: groups,
  };
}

function paintDetroitSelection(params, detail, detailError) {
  const selected = params.get("id") || "";
  const detailSlot = document.getElementById("detroit-detail-slot");
  if (!detailSlot || !detroitMapState.map) return false;
  detailSlot.replaceChildren(detailPanel(detail, detailError, detroitMapState.points || []));
  document.querySelectorAll(".incident-card").forEach(function (card) {
    const href = card.getAttribute("href") || "";
    const query = href.indexOf("?") === -1 ? "" : href.slice(href.indexOf("?") + 1);
    const cardId = new URLSearchParams(query).get("id") || "";
    card.classList.toggle("is-selected", cardId === selected);
  });
  (detroitMapState.markers || []).forEach(function (entry) {
    entry.marker.setStyle(clusterStyle(entry.ids.indexOf(selected) !== -1, entry.ids.length, entry.priority, entry.grouped));
  });
  return true;
}

function callLineLabel(source) {
  const code = String(source || "").trim().toUpperCase();
  if (code === "W911" || code === "E911") return "911 line";
  if (code === "PHONE") return "Non-emergency phone";
  if (!code) return "Call line not supplied";
  return "Call line " + String(source).trim();
}

function incidentCard(record, params, selected) {
  return h("a", {
    class: "incident-card" + priorityClass(record.priority) + (record.source_record_id === selected ? " is-selected" : ""),
    href: detroitHash(params, { id: record.source_record_id }),
  }, [
    h("span", { class: "incident-title" }, record.nature || record.category || "Published call"),
    h("span", { class: "incident-line" }, [
      h("span", { class: "pill" }, callLineLabel(record.call_source)),
      h("span", { class: "pill" }, record.priority ? "Priority " + record.priority : "No priority"),
      h("span", null, formatTime(record.called_at)),
      h("span", null, record.intersection || record.location_note || "No published intersection"),
    ]),
  ]);
}

function detailPanel(record, error, points) {
  if (error) return h("div", { class: "card detroit-detail" }, errorBlock(error));
  if (!record) {
    return h("div", { class: "card detroit-detail" }, [
      h("h2", null, "Incident detail"),
      h("p", { class: "muted" }, "Select a stored record to see its call-received time and the later time this dashboard retrieved it."),
    ]);
  }
  const samePoint = typeof record.latitude === "number" && typeof record.longitude === "number";
  const siblings = samePoint ? (points || []).filter(function (point) {
    return Math.abs(point.latitude - record.latitude) < 0.00005
      && Math.abs(point.longitude - record.longitude) < 0.00005
      && point.source_record_id !== record.source_record_id;
  }) : [];
  return h("div", { class: "card detroit-detail" }, [
    h("h2", null, record.nature || record.category || "Published call"),
    h("p", { class: "call-line" }, callLineLabel(record.call_source) + (record.call_source ? " (" + record.call_source + ")" : "")),
    h("p", { class: "muted" }, "No call audio is published with this incident."),
    h("p", { class: "muted" }, "Call received and data retrieved are different times."),
    h("ol", { class: "timeline" }, (record.timeline || []).map(function (item) {
      return h("li", null, [
        h("strong", null, item.label),
        h("div", null, formatTime(item.at)),
        h("div", { class: "mono faint" }, item.at || ""),
        h("p", { class: "muted" }, item.detail || ""),
      ]);
    })),
    h("dl", null, [
      h("dt", null, "Category"),
      h("dd", null, supplied(record.category)),
      h("dt", null, "Nature code"),
      h("dd", null, supplied(record.nature_code)),
      h("dt", null, "Priority"),
      h("dd", null, record.priority
        ? record.priority + ". Published priority: P is highest, then 1 through 5, with 1 higher than 5. This is not an independent severity score."
        : "Not supplied"),
      h("dt", null, "Call line"),
      h("dd", null, record.call_source
        ? callLineLabel(record.call_source) + " (" + record.call_source + "). This is the published line type, not a phone number, and not a recording."
        : "Not supplied"),
      h("dt", null, "Location"),
      h("dd", null, supplied(record.intersection || record.location_note)),
      h("dt", null, "Location note"),
      h("dd", null, supplied(record.location_note)),
      h("dt", null, "Precinct"),
      h("dd", null, supplied(record.precinct)),
      h("dt", null, "Call group"),
      h("dd", null, supplied(record.call_group)),
      h("dt", null, "Scout car area"),
      h("dd", null, record.scout_car_area
        ? record.scout_car_area + ". This is a published patrol area, not a vehicle location."
        : "Not supplied"),
      h("dt", null, "Neighborhood"),
      h("dd", null, supplied(record.neighborhood_name)),
      h("dt", null, "ZIP"),
      h("dd", null, supplied(record.zip_code)),
      h("dt", null, "Council district"),
      h("dd", null, supplied(record.council_district)),
      h("dt", null, "Source"),
      h("dd", null, record.source_name),
      h("dt", null, "Data type"),
      h("dd", null, record.data_type),
      h("dt", null, "Verification"),
      h("dd", null, record.verification),
      h("dt", null, "Record ID"),
      h("dd", { class: "mono" }, record.source_record_id),
      h("dt", null, "Entry ID"),
      h("dd", { class: "mono" }, supplied(record.incident_entry_id)),
      h("dt", null, "Freshness"),
      h("dd", null, record.freshness_label || freshnessName(record.freshness)),
    ]),
    siblings.length
      ? h("p", { class: "footnote" }, siblings.length + " other stored record" + (siblings.length === 1 ? "" : "s") + " on this page share this published point.")
      : null,
    h("h3", null, "Published elapsed times"),
    record.durations && record.durations.length
      ? h("ul", null, record.durations.map(function (item) {
          return h("li", null, item.label + ": " + item.minutes + ". " + item.note);
        }))
      : h("p", { class: "muted" }, "No elapsed times were published on this row."),
    h("div", { class: "callout" }, [
      h("p", null, "This record does not mean a call is happening now, and it does not mean a unit is currently responding."),
      h("p", null, "The source does not independently establish an arrest, injury, or crime outcome for this row."),
    ]),
  ]);
}

function countRows(records, field) {
  const counts = {};
  (records || []).forEach(function (record) {
    const raw = record[field];
    const label = raw == null || raw === "" ? "Not supplied" : String(raw);
    counts[label] = (counts[label] || 0) + 1;
  });
  return Object.keys(counts).map(function (label) {
    return { label: label, count: counts[label] };
  }).sort(function (left, right) { return right.count - left.count || left.label.localeCompare(right.label); }).slice(0, 8);
}

function detroitChartSection(incidents) {
  if (typeof barChart !== "function" || typeof timeBuckets !== "function") return null;
  const records = incidents.records || [];
  const sample = records.length + " loaded record" + (records.length === 1 ? "" : "s");
  const total = incidents.total || 0;
  const note = total > records.length
    ? "Charts use the " + sample + " on this page, not all " + total + " matches and not the city archive."
    : "Charts use the " + sample + " for the current filter. They are stored published calls, not live incidents.";
  return h("div", { class: "chart-grid" }, [
    h("section", { class: "card" }, [
      h("div", { class: "card-head" }, [h("h2", null, "Call-received activity"), h("span", { class: "faint" }, "Stored sample")]),
      h("p", { class: "footnote" }, note),
      barChart(timeBuckets(records), "Published call-received times in the loaded sample"),
    ]),
    h("section", { class: "card" }, [
      h("div", { class: "card-head" }, [h("h2", null, "Classifications"), h("span", { class: "faint" }, "Category")]),
      barChart(countRows(records, "category").map(function (row) {
        row.color = "#b06cff";
        return row;
      }), "Incident categories in the loaded sample"),
    ]),
    h("section", { class: "card" }, [
      h("div", { class: "card-head" }, [h("h2", null, "Geographic distribution"), h("span", { class: "faint" }, "Precinct")]),
      barChart(countRows(records, "precinct").map(function (row) {
        row.color = "#3dffb0";
        return row;
      }), "Precinct counts in the loaded sample"),
    ]),
  ]);
}

function pager(params, incidents) {
  const offset = incidents.offset || 0;
  const limit = incidents.limit || 500;
  const total = incidents.total || 0;
  const links = [];
  if (offset > 0) {
    links.push(h("a", { class: "btn", href: detroitHash(params, { offset: Math.max(0, offset - limit) || "" }) }, "Previous"));
  }
  if (offset + (incidents.records || []).length < total) {
    links.push(h("a", { class: "btn", href: detroitHash(params, { offset: offset + limit }) }, "Next"));
  }
  if (!links.length) return null;
  return h("div", { class: "pager" }, links.concat([
    h("span", { class: "muted" }, "Showing " + (incidents.records || []).length + " of " + total),
  ]));
}

function syncFailed(status) {
  if (!status) return false;
  if (status.sync_state) return status.sync_state === "error";
  return !!(status.latest_run && status.latest_run.ok === 0 && status.latest_run.finished_at);
}

function paintDetroitSync(status) {
  const slot = document.getElementById("detroit-sync-slot");
  if (!slot) return;
  const failed = syncFailed(status);
  slot.replaceChildren(
    syncCards(status),
    failed ? errorBlock((status.latest_run && status.latest_run.error) || "The last synchronization check failed.") : null,
    status.window_note ? h("p", { class: "footnote" }, status.window_note) : null
  );
}

function paintDetroitList(params, incidents) {
  const slot = document.getElementById("detroit-list-slot");
  if (!slot) return;
  const selected = params.get("id") || "";
  const records = incidents.records || [];
  const filterKey = detroitQuery(params);
  if (detroitMapState.listKey !== filterKey) {
    detroitListShown = 25;
    detroitMapState.listKey = filterKey;
  }
  const visible = records.slice(0, detroitListShown);
  const more = records.length - visible.length;
  slot.replaceChildren(
    h("div", { class: "card" }, [
      h("div", { class: "card-head" }, [
        h("h2", null, "Stored incidents"),
        h("span", { class: "faint" }, (incidents.total || 0) + " match"),
      ]),
      records.length
        ? h("div", { class: "incident-list" }, visible.map(function (record) {
            return incidentCard(record, params, selected);
          }))
        : h("p", { class: "empty" }, incidents.message || "No stored records match these filters."),
      more > 0
        ? h("button", { type: "button", class: "btn", onclick: function () {
            detroitListShown += 25;
            paintDetroitList(params, incidents);
          } }, "Show " + Math.min(25, more) + " more")
        : null,
      records.length
        ? h("p", { class: "footnote" }, "Showing " + visible.length + " of " + records.length + " loaded records. Open one for the full published fields.")
        : null,
      pager(params, incidents),
    ])
  );
}

function paintDetroit(view, params, status, facets, incidents, detail, detailError, basemap) {
  const selected = params.get("id") || "";
  const records = incidents.records || [];
  const tileUrl = voyagerTileUrl(basemap);
  const mapView = incidentMap(incidents.points || [], records, selected, params, tileUrl);
  view.replaceChildren(
    pageHead(
      "Detroit police calls for service",
      "Published records from the City of Detroit. This is not a live dispatch feed."
    ),
    h("div", { id: "detroit-sync-slot" }),
    detroitNotice(status),
    labelBlock(status),
    h("p", { class: "footnote" }, [
      officialLink(status.source_url, "Open the official dataset"),
      " ",
      officialLink(status.service_url, "Open the feature service"),
    ]),
    filterForm(params, facets || {}),
    h("p", { class: "footnote" }, "Filters apply to the list and the map. The map shows at most 2,000 matching stored records, newest call first."),
    h("div", { class: "detroit-layout" }, [
      mapView.card,
      h("div", { id: "detroit-detail-slot" }, detailPanel(detail, detailError, incidents.points || [])),
    ]),
    detroitChartSection(incidents),
    h("div", { id: "detroit-list-slot" })
  );
  paintDetroitSync(status);
  paintDetroitList(params, incidents);
  const mounted = mountDetroitMap(mapView.container, incidents.points || [], records, selected, params, tileUrl);
  detroitMapState.filterKey = detroitQuery(params);
  detroitMapState.points = incidents.points || [];
  detroitMapState.records = records;
  detroitMapState.root = mapView.container;
  if (mounted) {
    detroitMapState.map = mounted.map;
    detroitMapState.markers = mounted.markers;
  }
}

function stopDetroitRefresh() {
  if (detroitRefreshTimer) clearInterval(detroitRefreshTimer);
  detroitRefreshTimer = null;
}

function startDetroitRefresh(view, gen) {
  stopDetroitRefresh();
  detroitRefreshTimer = setInterval(function () {
    refreshDetroitQuiet(view, gen);
  }, DETROIT_REFRESH_MS);
}

function replaceDetroitMarkers(points, records, selected, params) {
  const map = detroitMapState.map;
  if (!map) return false;
  const center = map.getCenter();
  const zoom = map.getZoom();
  detroitMapState.markerZoom = zoom;
  (detroitMapState.markers || []).forEach(function (entry) {
    map.removeLayer(entry.marker);
  });
  detroitMapState.records = records || [];
  detroitMapState.markers = addDetroitMarkers(map, points, records, selected, params);
  detroitMapState.points = points || [];
  map.setView(center, zoom, { animate: false });
  return true;
}

function detroitFiltersBusy() {
  const active = document.activeElement;
  if (!active || typeof active.closest !== "function") return false;
  if (!active.closest("form.filters")) return false;
  const tag = String(active.tagName || "").toLowerCase();
  return tag === "input" || tag === "select" || tag === "textarea";
}

async function refreshDetroitQuiet(view, gen) {
  if (typeof current === "function" && !current(gen)) return;
  if (detroitFiltersBusy()) return;
  if (!detroitMapState.map || !detroitMapState.root || !detroitMapState.root.isConnected) return;
  const params = currentDetroitParams(new URLSearchParams());
  const filterKey = detroitQuery(params);
  if (filterKey !== detroitMapState.filterKey) return;
  let status;
  let incidents;
  try {
    const loaded = await Promise.all([
      api("/detroit/status"),
      api("/detroit/incidents" + filterKey),
    ]);
    if (typeof current === "function" && !current(gen)) return;
    if (detroitQuery(currentDetroitParams(new URLSearchParams())) !== filterKey) return;
    status = loaded[0];
    incidents = loaded[1];
  } catch (err) {
    if (typeof current === "function" && !current(gen)) return;
    const slot = document.getElementById("detroit-sync-slot");
    if (slot) {
      const note = document.getElementById("detroit-refresh-note");
      const message = err.message || "The Detroit records could not be refreshed.";
      if (note) note.textContent = message;
      else slot.appendChild(h("p", { id: "detroit-refresh-note", class: "footnote" }, message));
    }
    return;
  }
  if (!detroitMapState.map) return;
  paintDetroitSync(status);
  paintDetroitList(params, incidents);
  const selected = params.get("id") || "";
  replaceDetroitMarkers(incidents.points || [], incidents.records || [], selected, params);
  const count = document.getElementById("detroit-point-count");
  if (count) count.textContent = groupPoints(incidents.points || []).length + " intersection points";
  let detail = null;
  let detailError = "";
  if (selected) {
    try {
      const payload = await api("/detroit/incidents/" + encodeURIComponent(selected));
      if (typeof current === "function" && !current(gen)) return;
      detail = payload.record || null;
    } catch (err) {
      if (typeof current === "function" && !current(gen)) return;
      detailError = err.message || "That incident could not be loaded.";
    }
  }
  if (!detroitMapState.map) return;
  paintDetroitSelection(params, detail, detailError);
}

async function renderDetroit(view, gen, params) {
  startDetroitRefresh(view, gen);
  const filterKey = detroitQuery(params);
  const keep = canKeepDetroitMap(filterKey);
  if (keep) {
    let detail = null;
    let detailError = "";
    const selected = params.get("id") || "";
    if (selected) {
      try {
        const payload = await api("/detroit/incidents/" + encodeURIComponent(selected));
        if (!current(gen)) return;
        detail = payload.record || null;
      } catch (err) {
        if (!current(gen)) return;
        detailError = err.message || "That incident could not be loaded.";
      }
    }
    if (!current(gen)) return;
    if (canKeepDetroitMap(filterKey) && paintDetroitSelection(params, detail, detailError)) return;
  }
  disposeDetroitMap();
  view.replaceChildren(
    pageHead(
      "Detroit police calls for service",
      "Published records from the City of Detroit. This is not a live dispatch feed."
    ),
    detroitNotice(null),
    h("p", { class: "muted" }, "Loading stored records…")
  );
  let status;
  let facets;
  let incidents;
  let basemap;
  try {
    const loaded = await Promise.all([
      api("/detroit/status"),
      api("/detroit/facets"),
      api("/detroit/incidents" + filterKey),
      api("/detroit/basemap"),
    ]);
    if (!current(gen)) return;
    status = loaded[0];
    facets = loaded[1];
    incidents = loaded[2];
    basemap = loaded[3];
  } catch (err) {
    if (!current(gen)) return;
    disposeDetroitMap();
    view.replaceChildren(
      pageHead(
        "Detroit police calls for service",
        "Published records from the City of Detroit. This is not a live dispatch feed."
      ),
      detroitNotice(null),
      errorBlock(err.message || "The Detroit records could not be loaded.")
    );
    return;
  }
  let detail = null;
  let detailError = "";
  const selected = params.get("id") || "";
  if (selected) {
    try {
      const payload = await api("/detroit/incidents/" + encodeURIComponent(selected));
      if (!current(gen)) return;
      detail = payload.record || null;
    } catch (err) {
      if (!current(gen)) return;
      detailError = err.message || "That incident could not be loaded.";
    }
  }
  if (!current(gen)) return;
  paintDetroit(view, params, status, facets, incidents, detail, detailError, basemap);
}
