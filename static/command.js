"use strict";

function svgEl(name) {
  return document.createElementNS("http://www.w3.org/2000/svg", name);
}

function barChart(rows, label) {
  const list = rows || [];
  if (!list.length) return h("p", { class: "empty" }, "No stored values for this chart.");
  let max = 1;
  list.forEach(function (row) {
    if (row.count > max) max = row.count;
  });
  const svg = svgEl("svg");
  svg.setAttribute("viewBox", "0 0 640 " + (list.length * 28 + 8));
  svg.setAttribute("class", "chart");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", label);
  list.forEach(function (row, index) {
    const y = 6 + index * 28;
    const name = svgEl("text");
    name.setAttribute("x", "0");
    name.setAttribute("y", String(y + 13));
    name.setAttribute("class", "chart-label");
    name.textContent = String(row.label).slice(0, 24);
    const width = Math.max(4, Math.round((row.count / max) * 360));
    const bar = svgEl("rect");
    bar.setAttribute("x", "176");
    bar.setAttribute("y", String(y));
    bar.setAttribute("width", String(width));
    bar.setAttribute("height", "16");
    bar.setAttribute("rx", "4");
    bar.setAttribute("fill", row.color || "#3ee0ff");
    const value = svgEl("text");
    value.setAttribute("x", String(184 + width));
    value.setAttribute("y", String(y + 13));
    value.setAttribute("class", "chart-value");
    value.textContent = String(row.count);
    svg.appendChild(name);
    svg.appendChild(bar);
    svg.appendChild(value);
  });
  return svg;
}

function timeBuckets(records) {
  const days = {};
  const hours = {};
  (records || []).forEach(function (record) {
    const stamp = record && record.called_at ? String(record.called_at) : "";
    if (stamp.length < 13) return;
    const day = stamp.slice(0, 10);
    const hour = stamp.slice(0, 13) + ":00Z";
    days[day] = (days[day] || 0) + 1;
    hours[hour] = (hours[hour] || 0) + 1;
  });
  const useHours = Object.keys(days).length > 0 && Object.keys(days).length <= 2;
  const source = useHours ? hours : days;
  return Object.keys(source).sort().slice(-16).map(function (key) {
    return { label: useHours ? key.slice(5, 13).replace("T", " ") : key, count: source[key], color: "#3ee0ff" };
  });
}

function countLabeled(records, field, color) {
  const counts = {};
  (records || []).forEach(function (record) {
    const raw = record[field];
    const label = raw == null || raw === "" ? "Not supplied" : String(raw);
    counts[label] = (counts[label] || 0) + 1;
  });
  return Object.keys(counts).map(function (label) {
    return { label: label, count: counts[label], color: color };
  }).sort(function (left, right) {
    return right.count - left.count || left.label.localeCompare(right.label);
  }).slice(0, 8);
}

function settled(promise) {
  return promise.then(function (value) {
    return { ok: true, value: value };
  }, function (err) {
    return { ok: false, message: err && err.message ? err.message : "Unavailable" };
  });
}

function readHealth() {
  return fetch("/healthz", { headers: { Accept: "application/json" }, cache: "no-store" }).then(function (response) {
    if (!response.ok) throw new Error("The local health check returned " + response.status + ".");
    return response.json();
  }).then(function (data) {
    const ok = !!(data && data.status === "ok");
    return {
      ok: ok,
      read_only: !!(data && data.read_only),
      detail: ok
        ? "This dashboard process answered its health check."
        : "The health check did not report ok.",
    };
  });
}

async function loadCommandDeck() {
  const parts = await Promise.all([
    settled(api("/detroit/status")),
    settled(api("/detroit/runs?limit=40")),
    settled(api("/detroit/incidents?limit=2000")),
    settled(api("/shodan/status")),
    settled(api("/shodan/history?limit=50")),
    settled(readHealth()),
    settled(api("/detroit/basemap")),
    settled(api("/jobs")),
  ]);
  return {
    detroit: parts[0],
    runs: parts[1],
    incidents: parts[2],
    shodan: parts[3],
    history: parts[4],
    health: parts[5],
    basemap: parts[6],
    jobs: parts[7],
  };
}

function deckValue(part) {
  return part && part.ok ? part.value : null;
}

function deckMessage(part, fallback) {
  if (part && part.ok) return null;
  return part && part.message ? part.message : fallback;
}

function commandStat(value, label, help, tone) {
  return h("article", { class: "stat" + (tone ? " " + tone : "") }, [
    h("span", { class: "stat-value" }, value),
    h("span", { class: "stat-label" }, label),
    h("span", { class: "stat-help" }, help),
  ]);
}

function shodanState(status) {
  if (!status) return { value: "Unavailable", tone: "sync-error", help: "Shodan status could not be read." };
  if (!status.configured) return { value: "Not configured", tone: "sync-never", help: status.message || "No API key is loaded." };
  if (status.connected) return { value: "Key accepted", tone: "sync-current", help: status.message || "The last status check was accepted." };
  return { value: "Needs attention", tone: "sync-delayed", help: status.message || "The key is present, and the last status check was not accepted." };
}

function renderCommandDeck(deck) {
  if (!deck) return null;
  const detroit = deckValue(deck.detroit);
  const shodan = deckValue(deck.shodan);
  const counts = (shodan && shodan.counts) || {};
  const health = deckValue(deck.health);
  const incidents = deckValue(deck.incidents);
  const records = (incidents && incidents.records) || [];
  const runs = (deckValue(deck.runs) || {}).runs || [];
  const apiState = shodanState(shodan);
  const syncState = detroit && detroit.sync_state ? detroit.sync_state : "never";
  return h("div", { class: "command-deck" }, [
    h("div", { class: "stats" }, [
      commandStat(
        detroit ? formatNumber(detroit.local_records) : "Unavailable",
        "Detroit stored records",
        detroit ? "Published calls saved on this computer. Not a live dispatch count." : deckMessage(deck.detroit, "Detroit status is unavailable."),
        detroit ? "sync-" + syncState : "sync-error"
      ),
      commandStat(
        detroit && detroit.newest_event_at ? formatTime(detroit.newest_event_at) : "None",
        "Newest call received",
        "Original published call time of the newest stored row.",
        ""
      ),
      commandStat(
        detroit && typeof syncStateName === "function" ? syncStateName(detroit.sync_state) : "Unavailable",
        "Detroit check",
        detroit && detroit.sync_state_detail ? detroit.sync_state_detail : "Check status is unavailable.",
        "sync-" + syncState
      ),
      commandStat(
        detroit && detroit.source_edited_at ? formatTime(detroit.source_edited_at) : "Not reported",
        "City publication",
        "The city's dataLastEditDate from the last successful check.",
        ""
      ),
      commandStat(apiState.value, "Shodan API", apiState.help, apiState.tone),
      commandStat(
        counts.assets == null ? "—" : formatNumber(counts.assets),
        "Saved exposures",
        "Authorized addresses already saved. This is not an internet-wide total.",
        ""
      ),
      commandStat(
        counts.open_ports == null ? "—" : formatNumber(counts.open_ports),
        "Open ports",
        "Port listings Shodan attached to those saved addresses.",
        ""
      ),
      commandStat(
        counts.vulnerability_reviews == null ? "—" : formatNumber(counts.vulnerability_reviews),
        "Shodan CVE notes",
        "CVE identifiers Shodan supplied. They were not tested here.",
        counts.vulnerability_reviews ? "sync-error" : ""
      ),
    ]),
    h("div", { class: "chart-grid" }, [
      h("section", { class: "card" }, [
        h("div", { class: "card-head" }, [
          h("h2", null, "Detroit activity"),
          h("span", { class: "faint" }, records.length ? records.length + " newest stored" : "No sample"),
        ]),
        h("p", { class: "footnote" }, incidents
          ? "Call-received times from the newest stored rows returned by the local database. Not live incidents."
          : deckMessage(deck.incidents, "Incident sample is unavailable.")),
        incidents ? barChart(timeBuckets(records), "Stored Detroit call-received times") : null,
      ]),
      h("section", { class: "card" }, [
        h("div", { class: "card-head" }, [
          h("h2", null, "Saved exposure locations"),
          h("span", { class: "faint" }, "Authorized lookups"),
        ]),
        h("p", { class: "footnote" }, "Countries on addresses you already looked up. This is not a global threat feed, and empty means no country was saved."),
        barChart((counts.country_breakdown || []).map(function (row) {
          return { label: row.name, count: row.count, color: "#b06cff" };
        }), "Countries on saved authorized addresses"),
      ]),
    ]),
    h("div", { class: "layout-2" }, [
      h("section", { class: "card" }, [
        h("div", { class: "card-head" }, [h("h2", null, "Service health"), h("span", { class: "pill" }, health && health.ok ? "Responding" : "Check failed")]),
        h("p", null, health ? health.detail : deckMessage(deck.health, "Health was not read.")),
        h("p", { class: "footnote" }, health && health.read_only ? "The local server reports itself as read-only." : "Read-only status was not confirmed."),
        h("p", { class: "footnote" }, detroit && detroit.last_checked_at
          ? "Detroit last checked " + formatTime(detroit.last_checked_at) + ". Last successful sync " + (detroit.last_success_at ? formatTime(detroit.last_success_at) : "none") + "."
          : "No Detroit check time is stored."),
      ]),
      h("section", { class: "card" }, [
        h("div", { class: "card-head" }, [h("h2", null, "Recent Detroit checks"), h("a", { href: "#/reports" }, "Reports")]),
        runs.length ? runTable(runs.slice(0, 5)) : h("p", { class: "empty" }, deckMessage(deck.runs, "No synchronization history is stored yet.")),
      ]),
    ]),
  ]);
}

function runTable(runs) {
  return h("div", { class: "table-wrap" }, h("table", null, [
    h("thead", null, h("tr", null, ["Finished", "Result", "Upserted", "Pages", "City publication"].map(function (name) {
      return h("th", null, name);
    }))),
    h("tbody", null, runs.map(function (run) {
      const ok = run.ok === 1 || run.ok === true;
      return h("tr", null, [
        h("td", null, formatTime(run.finished_at || run.started_at)),
        h("td", null, ok ? (run.upserted === 0 ? "Unchanged" : "Updated") : "Failed"),
        h("td", null, run.upserted == null ? "—" : String(run.upserted)),
        h("td", null, run.pages == null ? "—" : String(run.pages)),
        h("td", null, run.source_edited_at ? formatTime(run.source_edited_at) : "Not reported"),
      ]);
    })),
  ]));
}

function historyTable(rows) {
  return h("div", { class: "table-wrap" }, h("table", null, [
    h("thead", null, h("tr", null, ["When", "Kind", "Result", "Source"].map(function (name) {
      return h("th", null, name);
    }))),
    h("tbody", null, rows.map(function (item) {
      return h("tr", null, [
        h("td", null, formatTime(item.created_at)),
        h("td", null, item.kind || ""),
        h("td", null, item.message || ""),
        h("td", null, item.cached ? "Cached" : "Newly retrieved"),
      ]);
    })),
  ]));
}

function lookupBuckets(rows) {
  const counts = {};
  (rows || []).forEach(function (item) {
    const stamp = item && item.created_at ? String(item.created_at) : "";
    if (stamp.length < 10) return;
    const key = stamp.slice(0, 10);
    counts[key] = (counts[key] || 0) + 1;
  });
  return Object.keys(counts).sort().map(function (key) {
    return { label: key, count: counts[key], color: "#e15cff" };
  });
}

async function renderAnalytics(view, gen) {
  view.replaceChildren(pageHead("Analytics", "Charts from records already stored on this computer."), h("p", { class: "muted" }, "Loading stored counts…"));
  const deck = await loadCommandDeck();
  if (typeof current === "function" && !current(gen)) return;
  const incidents = deckValue(deck.incidents);
  const records = (incidents && incidents.records) || [];
  const shodan = deckValue(deck.shodan);
  const counts = (shodan && shodan.counts) || {};
  const history = ((deckValue(deck.history) || {}).history) || [];
  const runs = ((deckValue(deck.runs) || {}).runs) || [];
  const sampleNote = incidents
    ? "Incident charts use the newest " + records.length + " stored rows" + (incidents.total > records.length ? " of " + incidents.total + " matches" : "") + ". They are not the full city archive."
    : deckMessage(deck.incidents, "The incident sample is unavailable.");
  view.replaceChildren(h("section", { class: "page", "data-page": "analytics" }, [
    pageHead("Analytics", "Counts and trends from the local Detroit database, saved Shodan lookups, and stored check history."),
    h("p", { class: "footnote" }, sampleNote),
    h("div", { class: "chart-grid" }, [
      h("section", { class: "card" }, [
        h("h2", null, "Incident trend"),
        barChart(timeBuckets(records), "Stored Detroit calls by received time"),
      ]),
      h("section", { class: "card" }, [
        h("h2", null, "Classifications"),
        barChart(countLabeled(records, "category", "#b06cff"), "Stored incident categories"),
      ]),
      h("section", { class: "card" }, [
        h("h2", null, "Precinct distribution"),
        barChart(countLabeled(records, "precinct", "#3dffb0"), "Stored incidents by precinct"),
      ]),
      h("section", { class: "card" }, [
        h("h2", null, "Open ports"),
        h("p", { class: "footnote" }, "Ports on saved authorized addresses. No number is shown unless Shodan supplied it."),
        barChart((counts.port_breakdown || []).map(function (row) {
          return { label: String(row.port), count: row.count, color: "#ff8a3d" };
        }), "Open ports on saved addresses"),
      ]),
      h("section", { class: "card" }, [
        h("h2", null, "Detected services"),
        barChart((counts.service_breakdown || []).map(function (row) {
          return { label: row.name, count: row.count, color: "#7aa2ff" };
        }), "Services on saved addresses"),
      ]),
      h("section", { class: "card" }, [
        h("h2", null, "Lookup activity"),
        h("p", { class: "footnote" }, "Authorized lookups saved on this computer. This is not a stored history of exposure totals."),
        barChart(lookupBuckets(history), "Authorized lookups by day"),
      ]),
      h("section", { class: "card" }, [
        h("h2", null, "Synchronization changes"),
        h("p", { class: "footnote" }, "Rows upserted on each stored Detroit check. Zero means the check found no new publication or an empty window."),
        barChart(runs.slice().reverse().map(function (run) {
          return {
            label: (run.finished_at || run.started_at || "").slice(5, 16).replace("T", " "),
            count: run.upserted || 0,
            color: run.ok ? "#3ee0ff" : "#ff5d73",
          };
        }).slice(-12), "Rows upserted by stored Detroit checks"),
      ]),
      h("section", { class: "card" }, [
        h("h2", null, "System health"),
        h("p", null, deckValue(deck.health) ? deckValue(deck.health).detail : deckMessage(deck.health, "Health was not read.")),
        h("p", { class: "footnote" }, shodan ? shodanState(shodan).value + ". " + shodanState(shodan).help : deckMessage(deck.shodan, "Shodan status is unavailable.")),
        h("p", { class: "footnote" }, "Source publication changes when the city's dataLastEditDate changes. The dashboard check can be more frequent than that publication."),
      ]),
    ]),
  ]));
}

async function renderReports(view, gen) {
  view.replaceChildren(pageHead("Reports", "Local history. Nothing here is a generated intelligence product."), h("p", { class: "muted" }, "Loading stored history…"));
  const deck = await loadCommandDeck();
  if (typeof current === "function" && !current(gen)) return;
  const runs = ((deckValue(deck.runs) || {}).runs) || [];
  const history = ((deckValue(deck.history) || {}).history) || [];
  const jobs = deck.jobs && deck.jobs.ok && Array.isArray(deck.jobs.value) ? deck.jobs.value : null;
  view.replaceChildren(h("section", { class: "page", "data-page": "reports" }, [
    pageHead("Reports", "Stored Detroit checks, authorized Shodan lookup history, and saved investigations. These are records, not a new assessment."),
    h("section", { class: "card" }, [
      h("div", { class: "card-head" }, [h("h2", null, "Detroit synchronization history"), h("span", { class: "faint" }, runs.length + " stored")]),
      runs.length ? runTable(runs) : h("p", { class: "empty" }, deckMessage(deck.runs, "No Detroit checks are stored yet.")),
    ]),
    h("section", { class: "card" }, [
      h("div", { class: "card-head" }, [h("h2", null, "Shodan lookup history"), h("span", { class: "faint" }, history.length + " stored")]),
      h("p", { class: "footnote" }, "Cached means the previous answer was reused. Newly retrieved means Shodan was contacted for that lookup."),
      history.length ? historyTable(history) : h("p", { class: "empty" }, deckMessage(deck.history, "No authorized lookups are stored yet.")),
    ]),
    h("section", { class: "card" }, [
      h("div", { class: "card-head" }, [h("h2", null, "Saved investigations"), h("a", { href: "#/search" }, "Search")]),
      jobs ? (typeof renderJobTable === "function" ? renderJobTable(jobs.slice(0, 12)) : h("p", { class: "empty" }, jobs.length + " investigations were returned.")) : h("p", { class: "empty" }, deckMessage(deck.jobs, "The command center did not return saved investigations.")),
    ]),
  ]));
}

async function renderSettings(view, gen) {
  view.replaceChildren(pageHead("Settings", "Local configuration. Secrets stay on this computer."), h("p", { class: "muted" }, "Loading configuration status…"));
  const deck = await loadCommandDeck();
  if (typeof current === "function" && !current(gen)) return;
  const detroit = deckValue(deck.detroit);
  const shodan = deckValue(deck.shodan);
  const basemap = deckValue(deck.basemap);
  const streets = basemap && basemap.enabled === true ? "On" : "Off";
  view.replaceChildren(h("section", { class: "page", "data-page": "settings" }, [
    pageHead("Settings", "Status of the local configuration. This page does not show API keys."),
    h("div", { class: "layout-2" }, [
      h("section", { class: "card" }, [
        h("h2", null, "Shodan"),
        h("p", null, shodan ? shodanState(shodan).value : deckMessage(deck.shodan, "Shodan status is unavailable.")),
        h("p", { class: "footnote" }, shodan && shodan.message ? shodan.message : "The key is read from the process environment or the local .env file. An existing process value wins."),
        h("p", { class: "footnote" }, "Last new Shodan answer: " + (shodan && shodan.last_sync ? formatTime(shodan.last_sync) : "none yet") + ". Last lookup record: " + (shodan && shodan.counts && shodan.counts.last_lookup_at ? formatTime(shodan.counts.last_lookup_at) : "none yet") + "."),
        h("pre", null, "cd ~/osint-dashboard-v2\nbash scripts/configure-shodan-key.sh\npython3 server.py"),
        h("p", { class: "footnote" }, "The setup script hides the key while you type it. It refuses to replace an existing .env file. Do not paste the key into chat."),
      ]),
      h("section", { class: "card" }, [
        h("h2", null, "Detroit checks"),
        h("dl", null, [
          h("dt", null, "Check interval"),
          h("dd", null, detroit ? String(detroit.sync_interval_seconds) + " seconds" : "Unavailable"),
          h("dt", null, "Recent window"),
          h("dd", null, detroit && detroit.lookback_hours != null ? detroit.lookback_hours + " hours" : "Unavailable"),
          h("dt", null, "Reconciliation window"),
          h("dd", null, detroit && detroit.reconcile_hours ? detroit.reconcile_hours + " hours" : "Off"),
          h("dt", null, "Reconciliation spacing"),
          h("dd", null, detroit ? String(detroit.reconcile_interval_seconds) + " seconds" : "Unavailable"),
          h("dt", null, "Last checked"),
          h("dd", null, detroit && detroit.last_checked_at ? formatTime(detroit.last_checked_at) : "None"),
          h("dt", null, "Last success"),
          h("dd", null, detroit && detroit.last_success_at ? formatTime(detroit.last_success_at) : "None"),
          h("dt", null, "City publication"),
          h("dd", null, detroit && detroit.source_edited_at ? formatTime(detroit.source_edited_at) : "Not reported"),
          h("dt", null, "Satellite imagery"),
          h("dd", null, "On"),
          h("dt", null, "Street tiles"),
          h("dd", null, streets),
        ]),
        h("p", { class: "footnote" }, detroit && detroit.window_note ? detroit.window_note : "The five-minute check asks whether the published layer changed. It is not the city's publication schedule."),
        h("p", { class: "footnote" }, "The Detroit map uses Esri World Imagery. It can be dragged and zoomed without a tile key. Optional CARTO Voyager street tiles use CARTO_BASEMAP_KEY when it is set. This page does not display that key."),
      ]),
    ]),
  ]));
}
