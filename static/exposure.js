/* Internet exposure from Shodan's read-only API. Not a scan, and not a proof of exploitability. */

var exposureState = { assets: [], status: null, history: [] };

var REVIEW_CLASS = { info: "pill-info", warning: "pill-warning", critical: "pill-critical" };

function exposureBlank(value) {
  return value ? String(value) : "Not reported";
}

function exposureWhen(value) {
  if (!value) return "Not reported";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString();
}

function exposureReview(asset) {
  const level = asset && asset.review ? asset.review : "info";
  return h("span", { class: "pill " + (REVIEW_CLASS[level] || "pill-info") }, asset.review_label || "No review flag");
}

function exposureMatches(asset, filters) {
  if (filters.country && asset.country !== filters.country) return false;
  if (filters.review && asset.review !== filters.review) return false;
  if (filters.port && (asset.ports || []).indexOf(Number(filters.port)) === -1) return false;
  if (filters.service) {
    const wanted = filters.service.toLowerCase();
    const names = (asset.services || []).map(function (service) {
      return ((service.product || service.name || "") + "").toLowerCase();
    });
    if (names.indexOf(wanted) === -1) return false;
  }
  return true;
}

function exposureOptions(values, label) {
  return [h("option", { value: "" }, label)].concat(values.map(function (value) {
    return h("option", { value: String(value) }, String(value));
  }));
}

function exposureFilters(assets) {
  const countries = [];
  const ports = [];
  const services = [];
  assets.forEach(function (asset) {
    if (asset.country && countries.indexOf(asset.country) === -1) countries.push(asset.country);
    (asset.ports || []).forEach(function (port) {
      if (ports.indexOf(port) === -1) ports.push(port);
    });
    (asset.services || []).forEach(function (service) {
      const name = service.product || service.name;
      if (name && services.indexOf(name) === -1) services.push(name);
    });
  });
  ports.sort(function (left, right) { return left - right; });
  countries.sort();
  services.sort();
  return h("div", { class: "filters", id: "exposure-filters" }, [
    h("label", { class: "field" }, [
      h("span", null, "Country"),
      h("select", { name: "country" }, exposureOptions(countries, "All countries")),
    ]),
    h("label", { class: "field" }, [
      h("span", null, "Port"),
      h("select", { name: "port" }, exposureOptions(ports, "All ports")),
    ]),
    h("label", { class: "field" }, [
      h("span", null, "Service"),
      h("select", { name: "service" }, exposureOptions(services, "All services")),
    ]),
    h("label", { class: "field" }, [
      h("span", null, "Review status"),
      h("select", { name: "review" }, [
        h("option", { value: "" }, "All reviews"),
        h("option", { value: "info" }, "No review flag"),
        h("option", { value: "warning" }, "Review recommended"),
        h("option", { value: "critical" }, "Priority review"),
      ]),
    ]),
  ]);
}

function exposureServiceLine(asset) {
  const names = [];
  (asset.services || []).forEach(function (service) {
    const name = service.name || service.product;
    if (name && names.indexOf(name) === -1) names.push(name);
  });
  return names.length ? names.join(", ") : "Not reported";
}

function exposureDetail(asset) {
  return h("details", { class: "exposure-detail" }, [
    h("summary", null, asset.ip + " technical details"),
    h("dl", null, [
      h("dt", null, "IP address"), h("dd", { class: "mono" }, asset.ip),
      h("dt", null, "Hostnames"), h("dd", null, (asset.hostnames || []).join(", ") || "Not reported"),
      h("dt", null, "Country"), h("dd", null, exposureBlank(asset.country)),
      h("dt", null, "Approximate location"), h("dd", null, exposureBlank(asset.location)),
      h("dt", null, "ISP"), h("dd", null, exposureBlank(asset.isp)),
      h("dt", null, "Hosting provider"), h("dd", null, exposureBlank(asset.organization)),
      h("dt", null, "Network"), h("dd", null, exposureBlank(asset.asn)),
      h("dt", null, "Open ports"), h("dd", { class: "mono" }, (asset.ports || []).join(", ") || "Not reported"),
      h("dt", null, "Last seen"), h("dd", null, exposureWhen(asset.last_seen)),
      h("dt", null, "Security review"), h("dd", null, exposureReview(asset)),
    ]),
    h("h3", null, "Services reported by Shodan"),
    (asset.services || []).length
      ? h("div", { class: "table-wrap" }, h("table", null, [
          h("thead", null, h("tr", null, ["Port", "Service", "Product", "Version", "Review"].map(function (label) {
            return h("th", null, label);
          }))),
          h("tbody", null, asset.services.map(function (service) {
            return h("tr", null, [
              h("td", { class: "mono" }, service.port + "/" + service.transport),
              h("td", null, exposureBlank(service.name)),
              h("td", null, exposureBlank(service.product)),
              h("td", null, service.version || "Not reported"),
              h("td", null, service.version ? "Version reported — check whether it is current" : "No version reported"),
            ]);
          })),
        ]))
      : h("p", { class: "muted" }, "Shodan did not report a service banner for this address."),
    (asset.services || []).some(function (service) { return service.banner; })
      ? h("div", null, asset.services.filter(function (service) { return service.banner; }).map(function (service) {
          return h("div", null, [
            h("p", { class: "footnote" }, "Banner text reported by Shodan for port " + service.port + "."),
            h("pre", { class: "banner-text" }, service.banner),
          ]);
        }))
      : null,
    h("h3", null, "Vulnerability metadata"),
    h("p", { class: "footnote" }, "A CVE identifier here was supplied by Shodan. This dashboard did not test it, and the listing is not proof that it can be used."),
    (asset.vulnerabilities || []).length
      ? h("ul", null, asset.vulnerabilities.map(function (item) {
          const score = item.cvss == null ? "CVSS not supplied" : "CVSS " + item.cvss;
          return h("li", null, [
            h("strong", { class: "mono" }, item.cve),
            " — " + score + ". ",
            item.shodan_verified ? "Shodan marked its own check as verified. " : "Shodan did not mark this as verified. ",
            item.summary || "No description was supplied.",
          ]);
        }))
      : h("p", { class: "muted" }, "Shodan did not supply CVE metadata for this address."),
  ]);
}

function exposureResults(result) {
  const assets = (result && result.assets) || [];
  exposureState.assets = assets;
  const filters = { country: "", port: "", service: "", review: "" };
  const tableBody = h("tbody");
  const details = h("div");
  function paint() {
    const visible = assets.filter(function (asset) { return exposureMatches(asset, filters); });
    tableBody.replaceChildren.apply(tableBody, visible.map(function (asset) {
      return h("tr", null, [
        h("td", { class: "mono" }, asset.ip),
        h("td", null, (asset.hostnames || []).join(", ") || "Not reported"),
        h("td", null, exposureBlank(asset.country)),
        h("td", null, exposureBlank(asset.isp)),
        h("td", { class: "mono" }, (asset.ports || []).join(", ") || "Not reported"),
        h("td", null, exposureServiceLine(asset)),
        h("td", null, exposureWhen(asset.last_seen)),
        h("td", null, exposureReview(asset)),
      ]);
    }));
    details.replaceChildren.apply(details, visible.map(exposureDetail));
    if (!visible.length) {
      tableBody.replaceChildren(h("tr", null, h("td", { colspan: "8" }, "No rows match these filters.")));
    }
  }
  const filterRow = assets.length ? exposureFilters(assets) : null;
  if (filterRow) {
    filterRow.addEventListener("change", function (event) {
      const select = event.target;
      if (!select || !select.name || !Object.prototype.hasOwnProperty.call(filters, select.name)) return;
      filters[select.name] = select.value;
      paint();
    });
  }
  paint();
  return h("div", { class: "card" }, [
    h("div", { class: "card-head" }, [
      h("h2", null, "Reported addresses"),
      h("span", { class: "pill " + (result && result.cached ? "pill-info" : "pill-good") }, result && result.cached ? "Cached" : "Newly retrieved"),
    ]),
    result && result.notice ? h("p", { class: "footnote" }, result.notice) : null,
    result && result.truncated ? h("p", { class: "footnote" }, "Showing the first 25 addresses from this lookup. This is not an internet-wide survey.") : null,
    result && result.resolved_addresses && result.resolved_addresses.length
      ? h("p", { class: "footnote" }, "Addresses Shodan DNS returned for this name: " + result.resolved_addresses.join(", ") + ".")
      : null,
    filterRow,
    assets.length
      ? h("div", { class: "table-wrap" }, h("table", null, [
          h("thead", null, h("tr", null, ["IP address", "Hostname", "Country", "ISP", "Open ports", "Services", "Last seen", "Security review"].map(function (label) {
            return h("th", null, label);
          }))),
          tableBody,
        ]))
      : h("p", { class: "empty" }, "Shodan has no public record for that authorized target."),
    details,
  ]);
}

function exposureStat(value, label, help, tone) {
  return h("article", { class: "stat" }, [
    h("span", { class: "stat-value" + (tone ? " " + tone : "") }, value),
    h("span", { class: "stat-label" }, label),
    h("span", { class: "stat-help" }, help),
  ]);
}

function exposureSummary(status) {
  const counts = (status && status.counts) || {};
  const connection = !status || !status.configured ? "Not configured" : status.connected ? "Connected" : "Needs attention";
  const tone = !status || !status.configured ? "" : status.connected ? "pill-info" : "pill-warning";
  return h("section", { class: "stats", id: "exposure-summary" }, [
    exposureStat(counts.assets == null ? "—" : String(counts.assets), "Authorized assets", "Addresses saved from your confirmed lookups.", ""),
    exposureStat(counts.ip_addresses == null ? "—" : String(counts.ip_addresses), "Unique IP addresses", "Each saved address is counted once.", ""),
    exposureStat(counts.services == null ? "—" : String(counts.services), "Exposed services", "Service records Shodan attached to those addresses.", ""),
    exposureStat(counts.open_ports == null ? "—" : String(counts.open_ports), "Open ports", "Port listings Shodan returned for the saved addresses.", ""),
    exposureStat(counts.version_reviews == null ? "—" : String(counts.version_reviews), "Potentially outdated services", "Shodan reported a product version. This dashboard does not decide whether that version is current.", "pill-warning"),
    exposureStat(counts.vulnerability_reviews == null ? "—" : String(counts.vulnerability_reviews), "Vulnerability notes", "CVE identifiers supplied by Shodan. They were not tested here.", "pill-critical"),
    exposureStat(status && status.last_sync ? exposureWhen(status.last_sync) : "None yet", "Last synchronization", "The last time a lookup received a new answer from Shodan.", ""),
    exposureStat(connection, "API connection", status && status.message ? status.message : "Shodan status is not available.", tone),
    exposureStat(
      status && status.query_credits == null ? "—" : String(status.query_credits),
      "Query credits",
      "Shodan reported this balance. A lookup uses a query credit. This dashboard does not scan.",
      status && status.query_credits === 0 ? "pill-warning" : ""
    ),
  ]);
}

function exposureHistory(history, onLoad) {
  const rows = history || [];
  return h("div", { class: "card" }, [
    h("div", { class: "card-head" }, [
      h("h2", null, "Search history"),
      h("span", { class: "faint" }, rows.length + " saved"),
    ]),
    rows.length
      ? h("div", { class: "table-wrap" }, h("table", null, [
          h("thead", null, h("tr", null, ["When", "Kind", "Target", "Result", ""].map(function (label) {
            return h("th", null, label);
          }))),
          h("tbody", null, rows.map(function (item) {
            const kind = item.kind === "ip" ? "IP address" : item.kind === "domain" ? "Domain" : "Organization";
            return h("tr", null, [
              h("td", null, exposureWhen(item.created_at)),
              h("td", null, kind),
              h("td", { class: "mono" }, item.value),
              h("td", null, [
                h("span", { class: "pill " + (item.cached ? "pill-info" : "pill-good") }, item.cached ? "Cached" : "Newly retrieved"),
                h("div", null, item.message),
              ]),
              h("td", null, h("button", {
                type: "button",
                onclick: function () { onLoad(item); },
              }, "Load")),
            ]);
          })),
        ]))
      : h("p", { class: "empty" }, "No authorized lookups have been saved on this computer yet."),
  ]);
}

function exposureIllustration() {
  return h("div", { class: "card" }, [
    h("div", { class: "card-head" }, [
      h("h2", null, "How to read a result"),
      h("span", { class: "pill" }, "Illustration only"),
    ]),
    h("p", { class: "footnote" }, "This sample uses a documentation address. It is not a finding and it is not looked up."),
    h("dl", null, [
      h("dt", null, "IP address"), h("dd", { class: "mono" }, "203.0.113.10"),
      h("dt", null, "Hostname"), h("dd", null, "example.example"),
      h("dt", null, "Country"), h("dd", null, "United States"),
      h("dt", null, "ISP"), h("dd", null, "Example Hosting"),
      h("dt", null, "Open ports"), h("dd", { class: "mono" }, "80, 443"),
      h("dt", null, "Services"), h("dd", null, "HTTP, HTTPS"),
      h("dt", null, "Last seen"), h("dd", null, "Timestamp from Shodan"),
      h("dt", null, "Security review"), h("dd", null, "Review recommended"),
    ]),
  ]);
}

function exposureForm(onSubmit) {
  const input = h("input", {
    name: "value",
    type: "text",
    maxlength: "253",
    autocomplete: "off",
    spellcheck: "false",
    required: "required",
    placeholder: "example.com",
  });
  const kind = h("select", { name: "kind" }, [
    h("option", { value: "domain" }, "Domain"),
    h("option", { value: "ip" }, "IP address"),
    h("option", { value: "organization" }, "Organization name"),
  ]);
  const box = h("input", { type: "checkbox", name: "authorized" });
  const button = h("button", { class: "btn btn-primary", type: "submit", disabled: "disabled" }, "Look up exposure");
  box.addEventListener("change", function () {
    if (box.checked) button.removeAttribute("disabled");
    else button.setAttribute("disabled", "disabled");
  });
  kind.addEventListener("change", function () {
    input.placeholder = kind.value === "ip" ? "203.0.113.10" : kind.value === "organization" ? "Example Hosting" : "example.com";
  });
  const form = h("form", { class: "card", id: "exposure-form" }, [
    h("div", { class: "card-head" }, [
      h("h2", null, "Authorized lookup"),
      h("span", { class: "faint" }, "One target at a time"),
    ]),
    h("p", { class: "footnote" }, "The search is built only from a domain, one public IP address, or an organization name. This page cannot run an internet-wide search, and it does not scan the target."),
    h("div", { class: "filters" }, [
      h("label", { class: "field" }, [h("span", null, "Target type"), kind]),
      h("label", { class: "field grow" }, [h("span", null, "Target"), input]),
    ]),
    h("label", { class: "exposure-check" }, [
      box,
      h("span", null, "I own this target, or I am explicitly authorized to assess it."),
    ]),
    button,
  ]);
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (!box.checked) return;
    onSubmit({ kind: kind.value, value: input.value.trim() });
  });
  return form;
}

async function exposureRun(kind, value, slot, gen) {
  slot.replaceChildren(h("p", { class: "muted" }, "Asking Shodan through this server…"));
  const path = kind === "ip"
    ? "/shodan/lookup?ip=" + encodeURIComponent(value) + "&authorized=yes"
    : "/shodan/search?type=" + encodeURIComponent(kind) + "&value=" + encodeURIComponent(value) + "&authorized=yes";
  try {
    const result = await api(path);
    if (typeof current === "function" && !current(gen)) return;
    slot.replaceChildren(exposureResults(result));
    const status = await api("/shodan/status");
    const history = await api("/shodan/history");
    if (typeof current === "function" && !current(gen)) return;
    exposureState.status = status;
    exposureState.history = history.history || [];
    const summary = document.getElementById("exposure-summary");
    if (summary) summary.replaceWith(exposureSummary(status));
    const saved = document.getElementById("exposure-history");
    if (saved) saved.replaceWith(exposureHistoryPanel(gen, slot));
  } catch (err) {
    if (typeof current === "function" && !current(gen)) return;
    slot.replaceChildren(errorBlock(err.message || "The lookup could not be completed."));
  }
}

function exposureBreakdown(status) {
  if (typeof barChart !== "function") return null;
  const counts = (status && status.counts) || {};
  return h("div", { class: "chart-grid" }, [
    h("section", { class: "card" }, [
      h("div", { class: "card-head" }, [h("h2", null, "Open port distribution"), h("span", { class: "faint" }, "Saved addresses")]),
      h("p", { class: "footnote" }, "Counts come from addresses already saved from authorized lookups."),
      barChart((counts.port_breakdown || []).map(function (row) {
        return { label: String(row.port), count: row.count, color: "#ff8a3d" };
      }), "Open ports on saved addresses"),
    ]),
    h("section", { class: "card" }, [
      h("div", { class: "card-head" }, [h("h2", null, "Service breakdown"), h("span", { class: "faint" }, "Saved addresses")]),
      barChart((counts.service_breakdown || []).map(function (row) {
        return { label: row.name, count: row.count, color: "#7aa2ff" };
      }), "Services on saved addresses"),
    ]),
    h("section", { class: "card" }, [
      h("div", { class: "card-head" }, [h("h2", null, "Saved locations"), h("span", { class: "faint" }, "Not a threat map")]),
      h("p", { class: "footnote" }, "Countries Shodan attached to saved authorized addresses. There is no separate global threat feed."),
      barChart((counts.country_breakdown || []).map(function (row) {
        return { label: row.name, count: row.count, color: "#b06cff" };
      }), "Countries on saved authorized addresses"),
    ]),
  ]);
}

function exposureAuthorized() {
  const box = document.querySelector("#exposure-form input[name='authorized']");
  return !!(box && box.checked);
}

function exposureHistoryPanel(gen, slot) {
  const panel = exposureHistory(exposureState.history, function (item) {
    if (!exposureAuthorized()) {
      slot.replaceChildren(errorBlock("Confirm that you own this target or are authorized to assess it."));
      return;
    }
    exposureRun(item.kind, item.value, slot, gen);
  });
  panel.id = "exposure-history";
  return panel;
}

async function renderExposure(view, gen) {
  view.replaceChildren(
    pageHead(
      "Shodan Intelligence",
      "Review internet-facing addresses for an organization, domain, or IP address you own or are authorized to assess."
    ),
    h("p", { class: "footnote" }, "Results come from Shodan. They are not independently verified findings, and this dashboard does not claim that a listed vulnerability can be used."),
    h("p", { class: "muted" }, "Loading exposure status…")
  );
  let status = { configured: false, connected: false, message: "Loading…", counts: {}, last_sync: null };
  let history = [];
  try {
    const loaded = await Promise.all([api("/shodan/status"), api("/shodan/history")]);
    if (typeof current === "function" && !current(gen)) return;
    status = loaded[0];
    history = loaded[1].history || [];
  } catch (err) {
    if (typeof current === "function" && !current(gen)) return;
    view.replaceChildren(
      pageHead(
        "Shodan Intelligence",
        "Review internet-facing addresses for an organization, domain, or IP address you own or are authorized to assess."
      ),
      errorBlock(err.message || "Exposure status could not be loaded.")
    );
    return;
  }
  exposureState.status = status;
  exposureState.history = history;
  const slot = h("div", { id: "exposure-results" });
  const summary = exposureSummary(status);
  view.replaceChildren(
    pageHead(
      "Shodan Intelligence",
      "Authorized exposure lookups. Results are Shodan's report for a target you confirm. This is not an internet-wide scan."
    ),
    h("p", { class: "footnote" }, "Results come from Shodan. They are not independently verified findings, and this dashboard does not claim that a listed vulnerability can be used. Internet Exposure remains the same local page."),
    status.configured ? null : h("div", { class: "banner", role: "status" }, [
      h("p", null, status.message),
      h("p", null, "Save the key once with the local setup script. Do not paste it into a source file or into chat."),
      h("pre", null, "cd ~/osint-dashboard-v2\nbash scripts/configure-shodan-key.sh\npython3 server.py"),
    ]),
    summary,
    exposureBreakdown(status),
    exposureForm(function (request) {
      exposureRun(request.kind, request.value, slot, gen);
    }),
    slot,
    exposureHistoryPanel(gen, slot),
    exposureIllustration(),
  );
}
