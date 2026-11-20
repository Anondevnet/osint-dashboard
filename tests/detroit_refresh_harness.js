"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const source = fs.readFileSync(path.join(__dirname, "..", "static", "detroit.js"), "utf8");
const context = {
  console,
  process,
  URLSearchParams,
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
};
vm.createContext(context);

const prelude = `
var calls = { fitBounds: 0, setView: [], removed: 0, added: 0, api: [], forms: 0 };
var layers = [];
var savedCenter = { lat: 42.35, lng: -83.05 };
var savedZoom = 13;
var map = {
  getCenter: function () { return savedCenter; },
  getZoom: function () { return savedZoom; },
  latLngToLayerPoint: function (latlng) {
    var lat = Array.isArray(latlng) ? latlng[0] : latlng.lat;
    var lng = Array.isArray(latlng) ? latlng[1] : latlng.lng;
    return { x: lng * 10000, y: lat * 10000 };
  },
  on: function () {},
  setView: function (center, zoom) { calls.setView.push([center, zoom]); },
  removeLayer: function (marker) {
    var index = layers.indexOf(marker);
    if (index === -1) throw new Error("removed a marker that was not on the map");
    layers.splice(index, 1);
    calls.removed += 1;
  },
  fitBounds: function () { calls.fitBounds += 1; },
  invalidateSize: function () {},
};
var L = {
  circleMarker: function () {
    return {
      on: function () {},
      bindTooltip: function () {},
      getElement: function () { return null; },
      setStyle: function () {},
      addTo: function () { layers.push(this); calls.added += 1; return this; },
    };
  },
  map: function () { return map; },
  latLngBounds: function () { return {}; },
  tileLayer: function () { return { addTo: function () {} }; },
};
function h() {
  return { replaceChildren: function () {}, appendChild: function () {}, classList: { toggle: function () {} } };
}
function formatNumber(value) { return String(value); }
function formatTime(value) { return String(value); }
function errorBlock(message) { return { message: message }; }
function pageHead() { return {}; }
function current() { return true; }
var location = { hash: "#/detroit?category=FIXTURE&priority=3" };
function parseHash() {
  var raw = String(location.hash || "").replace(/^#/, "");
  var query = raw.indexOf("?") === -1 ? "" : raw.slice(raw.indexOf("?") + 1);
  return { parts: ["detroit"], params: new URLSearchParams(query) };
}
var slots = {};
var document = {
  activeElement: null,
  getElementById: function (id) {
    if (!slots[id]) slots[id] = { textContent: "", replaceChildren: function () {}, appendChild: function () {} };
    return slots[id];
  },
  querySelectorAll: function () { return []; },
  createElement: function () {
    return { setAttribute: function () {}, addEventListener: function () {}, appendChild: function () {}, className: "" };
  },
};
function api(path) {
  calls.api.push(path);
  if (path.indexOf("/detroit/status") === 0) {
    return Promise.resolve({
      local_records: 2,
      newest_event_at: "2026-11-15T00:00:00Z",
      last_checked_at: "2026-11-16T12:00:00Z",
      last_success_at: "2026-11-16T12:00:00Z",
      source_edited_at: "2026-11-15T02:00:00Z",
      sync_state: "current",
      sync_state_detail: "These rows are not live incidents.",
      sync_interval_seconds: 300,
      publication_note: "Polling does not make this a live dispatch feed.",
      latest_run: { ok: 1, finished_at: "2026-11-16T12:00:00Z" },
      last_success: { ok: 1, finished_at: "2026-11-16T12:00:00Z", source_edited_at: "2026-11-15T02:00:00Z" },
      live_dispatch: false,
    });
  }
  return Promise.resolve({
    total: 1,
    records: [{
      source_record_id: "FIXTURE1",
      nature: "FIXTURE",
      category: "FIXTURE",
      priority: "3",
      called_at: "2026-11-15T00:00:00Z",
      intersection: "Example St & Sample Ave",
      precinct: 8,
      source_name: "City of Detroit — Police Serviced 911 Calls",
      retrieved_at: "2026-11-16T12:00:00Z",
      freshness: "recently_retrieved",
    }],
    points: [{ source_record_id: "FIXTURE1", latitude: 42.35, longitude: -83.05 }],
  });
}
`;

const checks = `
var hashBefore = location.hash;
var oldA = { name: "old-a" };
var oldB = { name: "old-b" };
layers.push(oldA, oldB);
detroitMapState.map = map;
detroitMapState.root = { isConnected: true };
detroitMapState.markers = [{ ids: ["OLD"], marker: oldA }, { ids: ["OLD2"], marker: oldB }];
detroitMapState.filterKey = detroitQuery(parseHash().params);
var builtForms = 0;
var realFilterForm = filterForm;
filterForm = function () { builtForms += 1; return realFilterForm.apply(this, arguments); };
document.activeElement = {
  tagName: "INPUT",
  closest: function (selector) { return selector === "form.filters" ? {} : null; },
};
refreshDetroitQuiet({}, 1).then(function () {
  if (calls.api.length !== 0) throw new Error("refresh ran while a filter field was focused");
  if (layers.length !== 2) throw new Error("a focused filter refresh changed markers");
  document.activeElement = { tagName: "BODY", closest: function () { return null; } };
  refreshDetroitQuiet({}, 1).then(function () {
  refreshDetroitQuiet({}, 1).then(function () {
    if (calls.fitBounds !== 0) throw new Error("quiet refresh moved the map with fitBounds");
    if (layers.length !== 1) throw new Error("marker count is " + layers.length);
    if (calls.removed < 3) throw new Error("old markers were not removed");
    if (location.hash !== hashBefore) throw new Error("filters in the hash changed");
    if (builtForms !== 0) throw new Error("quiet refresh rebuilt the filter form");
    if (calls.setView.length !== 2) throw new Error("map position was not restored");
    calls.setView.forEach(function (entry) {
      if (entry[0] !== savedCenter || entry[1] !== savedZoom) throw new Error("center or zoom changed");
    });
    var incidentCall = calls.api.filter(function (item) { return item.indexOf("/detroit/incidents") === 0; })[0];
    if (!incidentCall || incidentCall.indexOf("category=FIXTURE") === -1 || incidentCall.indexOf("priority=3") === -1) {
      throw new Error("refresh dropped the active filters");
    }
    if (calls.api.join(" ").indexOf("/detroit/status") === -1) throw new Error("refresh skipped status");
    console.log("refresh-ok");
    }).catch(function (err) { console.error(err && err.stack || err); process.exit(1); });
  }).catch(function (err) { console.error(err && err.stack || err); process.exit(1); });
}).catch(function (err) { console.error(err && err.stack || err); process.exit(1); });
`;

vm.runInContext(prelude + "\n" + source + "\n" + checks, context);