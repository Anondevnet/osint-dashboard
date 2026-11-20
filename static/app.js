"use strict";

const CONFIDENCE = {
  CONFIRMED: {
    label: "Confirmed",
    explain: "An investigator confirmed this. It is the strongest label in this system.",
  },
  HIGH: {
    label: "High",
    explain: "Several independent sources agree. Review the evidence before you rely on it.",
  },
  MEDIUM: {
    label: "Medium",
    explain: "There is some support, but it is not proven.",
  },
  LOW: {
    label: "Low",
    explain: "A weak or single automated signal. Treat it as a lead, not a fact.",
  },
};

const SERVICE = {
  OBSERVED: {
    label: "Seen directly",
    explain: "A tool reported this from its own result.",
  },
  INFERRED: {
    label: "Inferred",
    explain: "The system inferred this from nearby facts. It is weaker than a direct observation.",
  },
  CORRELATED: {
    label: "Corroborated",
    explain: "More than one independent tool points to the same thing.",
  },
  USER_CONFIRMED: {
    label: "Investigator confirmed",
    explain: "An investigator explicitly accepted this.",
  },
  UNRESOLVED: {
    label: "Unresolved",
    explain: "The evidence is not strong enough to decide, or sources still disagree.",
  },
  CONTRADICTED: {
    label: "Conflict",
    explain: "This conflicts with other evidence. Do not treat it as settled.",
  },
};

const ENTITY = {
  person: "person",
  username: "username",
  email: "email address",
  phone: "phone number",
  phone_public: "public phone number",
  domain: "domain",
  subdomain: "subdomain",
  ip: "IP address",
  url: "web address",
  organization: "organization",
  social_account: "social account",
  technology: "technology",
  location: "location",
  public_person: "confirmed public person",
  public_profile: "public profile",
  asn: "network",
  hosting_provider: "hosting provider",
  cryptocurrency_address: "crypto address",
  identity_candidate: "possible identity",
};

const RELATION = {
  HAS_USERNAME: "uses the username",
  HAS_EMAIL: "uses the email",
  HAS_PHONE: "uses the phone number",
  OWNS_DOMAIN: "owns the domain",
  HAS_SUBDOMAIN: "has the subdomain",
  RESOLVES_TO: "resolves to",
  LINKED_ACCOUNT: "matches a public account at",
  MENTIONED_BY: "is mentioned by",
  ASSOCIATED_WITH: "is associated with",
  HOSTS: "hosts",
  USES_TECHNOLOGY: "uses",
  HAS_PUBLIC_PROFILE: "has a public profile at",
  HAS_PUBLIC_CONTACT: "publishes the contact",
  HAS_PUBLIC_CRYPTO_ADDRESS: "publishes the crypto address",
  HAS_MX_RECORD: "has the mail server",
  HAS_NAMESERVER: "has the nameserver",
  BELONGS_TO_ASN: "belongs to the network",
  HOSTED_BY: "is hosted by",
  IDENTITY_CANDIDATE: "has a possible identity of",
  CONTRADICTS: "conflicts with",
};

const TARGET_TYPES = [
  ["domain", "Domain"],
  ["email", "Email"],
  ["username", "Username"],
  ["phone", "Phone"],
  ["person", "Person"],
  ["url", "URL"],
];

const JOB_STATUS = {
  queued: ["Waiting", "The investigation is saved and waiting to start."],
  running: ["In progress", "The command center still marks this investigation as running."],
  completed: ["Finished", "The command center marked this investigation finished."],
  failed: ["Failed", "The investigation stopped because of an error."],
  cancelled: ["Cancelled", "The investigation was cancelled."],
  timeout: ["Timed out", "The investigation ran out of time."],
};

const TOOL_RUN = {
  completed: "Finished",
  failed: "Failed",
  timeout: "Timed out",
  cancelled: "Cancelled",
  unavailable: "Not installed",
  running: "Running",
  skipped: "Skipped",
  not_started: "Not started",
};

const STAGES = [
  ["DISCOVERY", "Discovery", "Selected tools look up the target."],
  ["INFRASTRUCTURE", "Infrastructure", "Public DNS, network ownership, and the public homepage."],
  ["IDENTITY", "Identity", "Public accounts are organized. This does not prove who a person is."],
  ["CORRELATION", "Correlation", "Results from different tools are compared."],
  ["REPORTING", "Report", "A written report, when one has been generated."],
];

const STAGE_STATE = {
  pending: "Not started",
  running: "Working",
  completed: "Done",
  failed: "Failed",
  skipped: "Skipped",
  cancelled: "Cancelled",
};

const CAPABILITY = {
  subdomain_discovery: "Finds subdomains from public sources.",
  host_discovery: "Finds host names.",
  asset_discovery: "Finds related public assets.",
  username_account_discovery: "Finds public accounts for a username.",
  email_account_discovery: "Checks where an email address is registered.",
  person_account_discovery: "Looks for public accounts related to a person.",
  identity_enumeration: "Collects public identity clues.",
  domain_recon: "Maps a domain from public sources.",
  phone_account_discovery: "Checks which sites a phone number is registered on.",
  phone_number_details: "Reads a phone number's country and formats.",
  archived_url_discovery: "Lists addresses that web archives saved for a site.",
  site_crawl: "Lightly crawls the site's own pages (Authorized Active mode only).",
};

const TIER = {
  tier_1: "First-party or official source",
  tier_2: "Established public platform",
  tier_3: "Index or aggregator",
  tier_4: "Unverified source",
};

let renderGen = 0;
let pollTimer = null;
let searchTimer = null;

function h(tag, attrs, children) {
  const node = document.createElement(tag);
  const props = attrs || {};
  Object.keys(props).forEach(function (key) {
    const value = props[key];
    if (value == null || value === false) return;
    if (key === "class") node.className = String(value);
    else if (key.indexOf("on") === 0 && typeof value === "function") node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : String(value));
  });
  append(node, children);
  return node;
}

function append(node, children) {
  if (children == null) return;
  const list = Array.isArray(children) ? children : [children];
  list.forEach(function (child) {
    if (child == null || child === false) return;
    if (Array.isArray(child)) append(node, child);
    else node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  });
}

function el(id) {
  return document.getElementById(id);
}

function asList(value) {
  if (Array.isArray(value)) return value;
  if (typeof value === "string" && value) {
    try {
      const parsed = JSON.parse(value);
      if (Array.isArray(parsed)) return parsed;
    } catch (err) {
      return [value];
    }
  }
  return [];
}

function textOf(value) {
  if (value == null || value === "") return "";
  return String(value);
}

function formatNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString() : "—";
}

function formatTime(value) {
  if (!value) return "Not recorded";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

// Date and time as separate unbreakable pieces, so a narrow column wraps
// between them instead of clipping the year ("Oct 6, 202").
function dateTimeParts(value, suffix) {
  const date = value ? new Date(value) : null;
  if (!date || Number.isNaN(date.getTime())) return [h("span", { class: "nowrap" }, formatTime(value))];
  const parts = [
    h("span", { class: "nowrap" }, date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })),
    " ",
    h("span", { class: "nowrap faint" }, date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })),
  ];
  if (suffix) parts.push(" ", h("span", { class: "nowrap" }, "· " + suffix));
  return parts;
}

function formatAge(value) {
  const then = Date.parse(value || "");
  if (!then) return "";
  const hours = Math.floor((Date.now() - then) / 3600000);
  if (hours < 1) return "under an hour";
  if (hours < 48) return hours + " hours";
  return Math.floor(hours / 24) + " days";
}

function jobIsStaleRun(job) {
  if (textOf(job && job.status) !== "running") return false;
  const then = Date.parse((job && (job.started_at || job.created_at)) || "");
  if (!then) return false;
  return Date.now() - then >= 24 * 60 * 60 * 1000;
}

function staleFirst(jobs) {
  const stale = [];
  const rest = [];
  (jobs || []).forEach(function (job) {
    (jobIsStaleRun(job) ? stale : rest).push(job);
  });
  return stale.concat(rest);
}

function labelOf(map, code, fallback) {
  const key = textOf(code);
  return map[key] || fallback || key || "Unknown";
}

function entityLabel(type) {
  return labelOf(ENTITY, type, type ? String(type).replaceAll("_", " ") : "record");
}

function relationLabel(type) {
  const key = textOf(type);
  return RELATION[key] || (key ? key.toLowerCase().replaceAll("_", " ") : "");
}

function confidenceInfo(code) {
  return CONFIDENCE[textOf(code).toUpperCase()] || {
    label: code ? String(code) : "Not labeled",
    explain: "The command center did not record a standard confidence label.",
  };
}

function serviceInfo(code) {
  return SERVICE[textOf(code).toUpperCase()] || {
    label: code ? String(code) : "Not labeled",
    explain: "The command center did not record how this was established.",
  };
}

function pill(kind, code) {
  const info = kind === "confidence" ? confidenceInfo(code) : serviceInfo(code);
  const slug = textOf(code || "unknown").toLowerCase();
  return h("span", { class: "pill pill-" + kind + " pill-" + slug, title: info.explain }, info.label);
}

function statusPill(code, job) {
  if (job && jobIsStaleRun(job)) {
    const age = formatAge(job.started_at || job.created_at);
    return h("span", {
      class: "pill pill-running",
      title: "The command center has marked this running for more than a day. This dashboard cannot change that status.",
    }, age ? "Still marked running · " + age : "Still marked running");
  }
  const pair = JOB_STATUS[textOf(code)] || [code || "Unknown", "The command center stored this status."];
  const slug = textOf(code || "unknown").toLowerCase();
  return h("span", { class: "pill pill-" + slug, title: pair[1] }, pair[0]);
}

function explainFinding(row) {
  const tool = textOf(row.tool) || "A tool";
  const kind = entityLabel(row.entity_type);
  const value = textOf(row.value) || "an unnamed value";
  let sentence = tool + " recorded a " + kind + ": " + value + ".";
  const relation = relationLabel(row.relationship_type);
  if (relation) {
    const destKind = entityLabel(row.dst_type);
    const dest = textOf(row.dst_value);
    sentence += dest ? " It " + relation + " " + destKind + " " + dest + "." : " It " + relation + ".";
  }
  if (row.target) sentence += " Investigated target: " + row.target + ".";
  return sentence;
}

function findingTitle(row) {
  const kind = entityLabel(row.entity_type);
  const value = textOf(row.value) || "Unnamed value";
  return kind.charAt(0).toUpperCase() + kind.slice(1) + ": " + value;
}

function findingDetail(row) {
  const relation = relationLabel(row.relationship_type);
  if (!relation) return textOf(row.target) ? "Target " + row.target : "";
  const dest = textOf(row.dst_value);
  const destKind = entityLabel(row.dst_type);
  return "It " + relation + (dest ? " " + destKind + " " + dest : "") + ".";
}

function messageFrom(data, fallback) {
  if (!data) return fallback;
  if (typeof data.message === "string") return data.message;
  if (typeof data.detail === "string") return data.detail;
  if (data.detail && typeof data.detail.message === "string") return data.detail.message;
  return fallback;
}

async function api(path) {
  let response;
  try {
    response = await fetch("/api" + path, { headers: { Accept: "application/json" }, cache: "no-store" });
  } catch (err) {
    const error = new Error("The dashboard could not reach its own server.");
    error.status = 0;
    throw error;
  }
  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch (err) {
      const error = new Error("The command center returned a response this dashboard could not read.");
      error.status = response.status;
      throw error;
    }
  }
  if (!response.ok) {
    const error = new Error(messageFrom(data, "Request failed (" + response.status + ")."));
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

function queryString(params) {
  const search = new URLSearchParams();
  Object.keys(params).forEach(function (key) {
    const value = params[key];
    if (value != null && value !== "") search.set(key, String(value));
  });
  const text = search.toString();
  return text ? "?" + text : "";
}

function offlineBanner() {
  return h("div", { class: "banner", role: "status" }, [
    h("p", null, "The command center is not running, so there is no saved data to show."),
    h("p", null, "Start it in another terminal, then reload this page:"),
    h("pre", null, "cd ~/osint-dashboard\npython3 -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000"),
  ]);
}

function errorBlock(message) {
  return h("div", { class: "banner", role: "alert" }, message);
}

function isOffline(err) {
  return err && (err.status === 502 || err.status === 504 || err.status === 0);
}

function pageHead(title, lede) {
  return h("header", { class: "page-head" }, [
    h("h1", null, title),
    lede ? h("p", { class: "lede" }, lede) : null,
  ]);
}

function legend(kind) {
  const source = kind === "confidence" ? CONFIDENCE : SERVICE;
  return h("div", { class: "legend" }, Object.keys(source).map(function (code) {
    const info = source[code];
    return h("p", null, [h("strong", null, info.label + ". "), info.explain]);
  }));
}

function openDialog(title, body) {
  el("dialog-title").textContent = title;
  el("dialog-body").replaceChildren(body);
  const dialog = el("detail-dialog");
  if (!dialog.open) dialog.showModal();
}

function stopPoll() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = null;
}

function current(gen) {
  return gen === renderGen;
}

function stageMap(stages) {
  const map = {};
  asList(stages).forEach(function (stage) {
    if (stage && stage.stage) map[stage.stage] = stage;
  });
  return map;
}

function progressDots(job) {
  const map = stageMap(job.stages);
  const counts = {};
  const dots = STAGES.map(function (item) {
    const status = (map[item[0]] && map[item[0]].status) || "pending";
    const label = STAGE_STATE[status] || status;
    counts[label] = (counts[label] || 0) + 1;
    return h("span", {
      class: "dot is-" + status,
      title: item[1] + ": " + label,
    });
  });
  const summary = Object.keys(counts).map(function (label) {
    return counts[label] + " " + label.toLowerCase();
  }).join(", ");
  return h("div", null, [
    h("span", { class: "dots", "aria-hidden": "true" }, dots),
    h("div", { class: "faint" }, summary),
  ]);
}

function toolRows(job) {
  const detailed = asList(job.tool_results);
  const source = detailed.length ? detailed : asList(job.results);
  const rows = source.map(function (row) {
    return {
      tool: row.tool || "unknown",
      status: row.status || "unknown",
      finding_count: typeof row.finding_count === "number" ? row.finding_count : null,
      error: row.error || "",
      duration: row.duration_seconds,
      exit_code: row.exit_code,
    };
  });
  const seen = {};
  rows.forEach(function (row) { seen[row.tool] = true; });
  asList(job.requested_tools).forEach(function (name) {
    if (!seen[name]) rows.push({ tool: name, status: "not_started", finding_count: null, error: "" });
  });
  return rows;
}

function reportedFindings(job) {
  let sum = 0;
  let any = false;
  toolRows(job).forEach(function (row) {
    if (typeof row.finding_count === "number") {
      sum += row.finding_count;
      any = true;
    }
  });
  return any ? sum : null;
}

function targetTypeLabel(value) {
  const found = TARGET_TYPES.find(function (item) { return item[0] === value; });
  return found ? found[1] : (value || "Unknown");
}

async function refreshHealth() {
  const node = el("backend-status");
  if (!node) return;
  try {
    const data = await api("/health");
    const ok = data && data.status === "ok";
    node.textContent = ok ? "Command center connected" : "Command center responded unexpectedly";
    node.className = ok ? "sidebar-note is-ok" : "sidebar-note is-bad";
  } catch (err) {
    node.textContent = "Command center offline";
    node.className = "sidebar-note is-bad";
  }
}

function jobLink(id, label) {
  return h("a", { href: "#/investigation/" + encodeURIComponent(id) }, label);
}

function renderJobTable(jobs) {
  if (!jobs.length) return h("p", { class: "empty" }, "No saved investigations match.");
  const stale = jobs.filter(jobIsStaleRun).length;
  return h("div", { class: "table-wrap" }, [
    stale ? h("p", { class: "footnote" }, stale + (stale === 1 ? " investigation is" : " investigations are") + " still marked running after more than a day. This dashboard only reads that status.") : null,
    h("table", null, [
      h("thead", null, h("tr", null, ["Target", "Type", "Status", "Progress", "Findings", "Started"].map(function (name) {
        return h("th", { scope: "col" }, name);
      }))),
      h("tbody", null, jobs.map(function (job) {
        const count = reportedFindings(job);
        const startedAt = job.started_at || job.created_at;
        return h("tr", null, [
          h("td", null, jobLink(job.id, textOf(job.target) || "(no target)")),
          h("td", null, targetTypeLabel(job.target_type)),
          h("td", null, statusPill(job.status, job)),
          h("td", null, progressDots(job)),
          h("td", { class: "nowrap" }, count == null ? "—" : formatNumber(count)),
          h("td", { class: "date-cell" }, dateTimeParts(startedAt, jobIsStaleRun(job) ? formatAge(startedAt) : "")),
        ]);
      })),
    ]),
  ]);
}

async function renderOverview(view, gen, quiet) {
  if (!quiet) view.replaceChildren(h("p", { class: "muted" }, "Loading saved counts…"));
  let stats;
  let jobs;
  let tools;
  let deck = null;
  try {
    const results = await Promise.all([
      api("/stats"),
      api("/jobs"),
      api("/tools"),
      typeof loadCommandDeck === "function" ? loadCommandDeck() : Promise.resolve(null),
    ]);
    stats = results[0];
    jobs = results[1];
    tools = results[2];
    deck = results[3];
  } catch (err) {
    if (!current(gen)) return;
    view.replaceChildren(pageHead("Overview", "Saved investigations from the command center."), isOffline(err) ? offlineBanner() : errorBlock(err.message));
    return;
  }
  if (!current(gen)) return;
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  const jobList = Array.isArray(jobs) ? jobs : [];
  const toolList = Array.isArray(tools) ? tools : [];
  const ready = toolList.filter(function (tool) { return tool.available; }).length;
  const running = jobList.some(function (job) {
    return (job.status === "running" || job.status === "queued") && !jobIsStaleRun(job);
  });
  const cards = [
    ["jobs", "Investigations", "Saved runs. Each one is a target the command center already looked up."],
    ["findings", "Findings", "Individual facts recorded by tools."],
    ["entities", "Entities", "Distinct things those facts mention, such as a domain or a profile."],
    ["evidence", "Evidence", "The tool output stored behind the findings."],
  ];
  const deckNode = typeof renderCommandDeck === "function" ? renderCommandDeck(deck) : null;
  view.replaceChildren(h("section", { class: "page", "data-page": "overview" }, [
    h("header", { class: "page-head command-hero" }, [
      h("p", { class: "eyebrow" }, "Local operations"),
      h("h1", null, "OSINT COMMAND CENTER"),
      h("p", { class: "lede" }, "Saved investigations, published Detroit calls, and authorized Shodan lookups. This page does not start a search, and it does not invent a live feed."),
    ]),
    deckNode,
    h("p", { class: "lede" }, "A finding is one fact a tool recorded. Evidence is the tool output behind that fact. An entity is a thing those facts mention."),
    h("div", { class: "stats" }, cards.map(function (card) {
      return h("article", { class: "stat" }, [
        h("span", { class: "stat-value", "data-stat": card[0] }, formatNumber(stats && stats[card[0]])),
        h("span", { class: "stat-label" }, card[1]),
        h("span", { class: "stat-help" }, card[2]),
      ]);
    })),
    h("p", { class: "footnote" }, "Links between records: " + formatNumber(stats && stats.relationships) + "."),
    h("div", { class: "layout-2" }, [
      h("section", { class: "card" }, [
        h("div", { class: "card-head" }, [
          h("h2", null, "Recent investigations"),
          h("a", { href: "#/search" }, "Search all"),
        ]),
        renderJobTable(staleFirst(jobList).slice(0, 12)),
      ]),
      h("section", { class: "card" }, [
        h("div", { class: "card-head" }, [
          h("h2", null, "Tool status"),
          h("span", { class: "muted" }, ready + " of " + toolList.length + " installed"),
        ]),
        toolList.length ? toolList.map(function (tool) { return toolCard(tool); }) : h("p", { class: "empty" }, "No tools were reported."),
      ]),
    ]),
  ]));
  if (running) {
    pollTimer = setInterval(function () {
      if (current(gen)) renderOverview(view, gen, true);
    }, 15000);
  }
}

function toolCard(tool) {
  const state = tool.available ? "Installed" : "Not installed";
  const slug = tool.available ? "good" : "bad";
  const caps = asList(tool.capabilities).map(function (item) {
    return CAPABILITY[item] || String(item).replaceAll("_", " ");
  });
  return h("article", { class: "tool" }, [
    h("div", null, [
      h("h3", null, tool.name || "Tool"),
      h("p", null, tool.description || "No description recorded."),
      caps.length ? h("p", null, caps.join(" ")) : null,
    ]),
    h("span", { class: "pill pill-" + slug, title: tool.version ? "Version " + tool.version : "" }, state),
  ]);
}

function matchesJob(job, query, type, status) {
  if (type && job.target_type !== type) return false;
  if (status && job.status !== status) return false;
  if (!query) return true;
  const haystack = [job.id, job.target, job.target_type, asList(job.requested_tools).join(" ")].join(" ").toLowerCase();
  return haystack.indexOf(query.toLowerCase()) !== -1;
}

async function renderSearch(view, gen) {
  const results = h("div", null, h("p", { class: "muted" }, "Loading investigations…"));
  const entities = h("div", null);
  const toolBox = h("div", { id: "tool-preview" });
  const typeSelect = h("select", { id: "search-type", "aria-label": "Target type" }, [
    h("option", { value: "" }, "All types"),
  ].concat(TARGET_TYPES.map(function (item) {
    return h("option", { value: item[0] }, item[1]);
  })));
  const statusSelect = h("select", { id: "search-status", "aria-label": "Status" }, [
    h("option", { value: "" }, "Any status"),
    h("option", { value: "running" }, "In progress"),
    h("option", { value: "completed" }, "Finished"),
    h("option", { value: "failed" }, "Failed"),
    h("option", { value: "queued" }, "Waiting"),
    h("option", { value: "cancelled" }, "Cancelled"),
  ]);
  const input = h("input", {
    id: "search-input",
    type: "search",
    placeholder: "Target, investigation id, or saved record",
    autocomplete: "off",
  });
  const previewType = h("select", { id: "preview-type", "aria-label": "Detected type (change if wrong)" },
    [h("option", { value: "auto" }, "Auto-detect")].concat(TARGET_TYPES.map(function (item) {
      return h("option", { value: item[0] }, item[1]);
    })));
  const smartChipBox = h("div", { class: "smart-detect", id: "smart-chip", "aria-live": "polite" },
    h("span", { class: "muted" }, "Type in the search box above. The type and the tools are picked for you."));
  const smartAlts = h("div", { class: "smart-alts", id: "smart-alts" });
  const alsoBox = h("input", { type: "checkbox", id: "smart-also-username" });
  const alsoText = h("span", null, "Also check as a username");
  const alsoWrap = h("label", { class: "smart-also", hidden: true }, [alsoBox, " ", alsoText]);
  const smartPlanBox = h("div", { class: "smart-plan", id: "smart-plan" });
  const startLink = h("a", { class: "btn-link", id: "smart-start", hidden: true, target: "_blank", rel: "noopener noreferrer" }, "Open this in the command center to start it \u2197");
  let smartType = "";
  let smartSeq = 0;
  let smartTimer = null;
  let jobs = [];
  let tools = [];

  function chipFor(det, overridden) {
    if (!det || !det.type) return [h("span", { class: "smart-chip smart-chip-none" }, (det && det.reason) || "Type something to search.")];
    return [
      h("span", { class: "smart-chip" }, ["Detected: ", h("b", null, targetTypeLabel(det.type))]),
      h("span", { class: "smart-why" }, (overridden ? "you chose this type" : det.reason) + " \u2192 " + det.value + (det.location ? " \u00b7 place: " + det.location : "")),
    ];
  }

  function drawAlts(det) {
    smartAlts.replaceChildren();
    const alts = (det && det.alternatives) || [];
    if (!alts.length) return;
    smartAlts.append(h("span", { class: "faint" }, "Or search it as: "));
    alts.forEach(function (alt) {
      smartAlts.append(h("button", { type: "button", class: "smart-alt", title: alt.reason, onclick: function () {
        previewType.value = alt.type;
        updateSmart(true);
        drawTools();
      } }, targetTypeLabel(alt.type) + ": " + alt.value), " ");
    });
  }

  function drawAlso(det) {
    const offer = det && det.type === "person" && det.also_username;
    alsoWrap.hidden = !offer;
    if (offer) alsoText.textContent = "Also check \u201c" + det.also_username + "\u201d as a username (off by default)";
    else alsoBox.checked = false;
  }

  function drawPlan(plan) {
    smartPlanBox.replaceChildren();
    if (!plan || !plan.jobs || !plan.jobs.length) return;
    smartPlanBox.append(h("h3", null, "Tools that will run"));
    plan.jobs.forEach(function (job, i) {
      smartPlanBox.append(h("div", { class: "smart-job" }, [
        h("p", { class: "smart-job-head" }, i === 0 ? targetTypeLabel(job.target_type) + ": " + job.target : "Plus " + (job.why || job.target_type)),
        h("ul", { class: "smart-tools" }, job.tools.map(function (t) {
          return h("li", { class: "smart-tool " + (t.picked ? "picked" : "not-picked") }, [
            h("span", { class: "smart-tool-name" }, (t.picked ? "\u2713 " : "\u2013 ") + RESULTS.toolName(t.tool)),
            h("span", { class: "smart-tool-why" }, t.picked ? t.reason : (t.skip_reason || t.reason)),
          ]);
        })),
      ]));
    });
    if (plan.note) smartPlanBox.append(h("p", { class: "footnote" }, plan.note));
  }

  function updateSmart(now) {
    const q = input.value;
    const override = previewType.value;
    if (override === "auto" && window.OsintDetect) {
      const local = window.OsintDetect.detect(q);
      smartChipBox.replaceChildren.apply(smartChipBox, chipFor(local, false));
      drawAlts(local);
      drawAlso(local);
    }
    if (smartTimer) clearTimeout(smartTimer);
    smartTimer = setTimeout(async function () {
      const seq = ++smartSeq;
      if (!q.trim()) {
        smartType = "";
        drawPlan(null);
        startLink.hidden = true;
        if (override === "auto") drawTools();
        return;
      }
      try {
        const r = await api("/detect" + queryString({ q: q, type: override === "auto" ? "" : override, also_username: alsoBox.checked ? "true" : "" }));
        if (!current(gen) || seq !== smartSeq) return;
        const det = r.detection || {};
        smartType = det.type || "";
        smartChipBox.replaceChildren.apply(smartChipBox, chipFor(det, override !== "auto"));
        drawAlts(override === "auto" ? det : null);
        drawAlso(det);
        drawPlan(r.plan);
        startLink.hidden = !det.type;
        if (det.type) {
          startLink.setAttribute("href", "http://127.0.0.1:8000/?target=" + encodeURIComponent(q.trim()) +
            (override === "auto" ? "" : "&type=" + encodeURIComponent(override)) + "#investigations");
        }
        if (override === "auto") drawTools();
      } catch (err) {
        if (!current(gen) || seq !== smartSeq) return;
        smartPlanBox.replaceChildren(errorBlock(err.message));
      }
    }, now ? 0 : 250);
  }

  function drawTools() {
    const selected = previewType.value === "auto" ? smartType : previewType.value;
    if (!selected) {
      toolBox.replaceChildren(h("p", { class: "empty" }, "Type a target, or pick a type, to list every tool for it."));
      return;
    }
    const matching = tools.filter(function (tool) {
      return asList(tool.supported_target_types).indexOf(selected) !== -1;
    });
    toolBox.replaceChildren();
    if (!matching.length) {
      toolBox.append(h("p", { class: "empty" }, "No command-center tool lists this target type."));
      return;
    }
    matching.forEach(function (tool) { toolBox.append(toolCard(tool)); });
  }

  function drawJobs() {
    const filtered = jobs.filter(function (job) {
      return matchesJob(job, input.value.trim(), typeSelect.value, statusSelect.value);
    });
    results.replaceChildren(
      h("p", { class: "footnote" }, "Showing " + filtered.length + " of " + jobs.length + " saved investigations."),
      renderJobTable(filtered)
    );
  }

  async function drawEntities() {
    const query = input.value.trim();
    if (query.length < 2) {
      entities.replaceChildren();
      return;
    }
    entities.replaceChildren(h("p", { class: "muted" }, "Searching saved records…"));
    try {
      const rows = await api("/entities" + queryString({ q: query, limit: 20 }));
      if (!current(gen) || input.value.trim() !== query) return;
      const list = Array.isArray(rows) ? rows : [];
      entities.replaceChildren(
        h("h2", null, "Saved records"),
        list.length
          ? h("div", { class: "table-wrap" }, h("table", null, [
            h("thead", null, h("tr", null, ["Record", "Confidence", "Last seen", ""].map(function (name) {
              return h("th", { scope: "col" }, name);
            }))),
            h("tbody", null, list.map(function (row) {
              return h("tr", null, [
                h("td", null, [
                  h("div", null, entityLabel(row.type)),
                  h("div", { class: "clip", title: textOf(row.value) }, textOf(row.value)),
                ]),
                h("td", null, pill("confidence", row.confidence)),
                h("td", { class: "nowrap" }, formatTime(row.last_seen)),
                h("td", null, h("button", { type: "button", onclick: function () { showEntity(row.id); } }, "Explain")),
              ]);
            })),
          ]))
          : h("p", { class: "empty" }, "No saved record matches that text.")
      );
    } catch (err) {
      if (!current(gen)) return;
      entities.replaceChildren(errorBlock(err.message));
    }
  }

  function onFilter() {
    drawJobs();
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(drawEntities, 300);
  }

  view.replaceChildren(h("section", { class: "page", "data-page": "search" }, [
    pageHead("Search", "Look up an investigation that already ran. Typing here does not start a scan."),
    h("form", { class: "filters", onsubmit: function (event) { event.preventDefault(); onFilter(); } }, [
      h("label", { class: "field grow" }, [h("span", null, "Search"), input]),
      h("label", { class: "field" }, [h("span", null, "Type"), typeSelect]),
      h("label", { class: "field" }, [h("span", null, "Status"), statusSelect]),
      h("button", { class: "btn-primary", type: "submit" }, "Search"),
    ]),
    h("div", { class: "layout-2" }, [
      h("section", { class: "card stack" }, [results, entities]),
      h("section", { class: "card", "data-section": "smart-search" }, [
        h("h2", null, "New investigation"),
        h("p", { class: "muted" }, "Type what you want to look up in the search box. The dashboard works out what it is and picks the best passive tools. It cannot start them itself; use the link to start it in the command center."),
        smartChipBox,
        smartAlts,
        h("label", { class: "field" }, [h("span", null, "Detected type (change if wrong)"), previewType]),
        alsoWrap,
        smartPlanBox,
        startLink,
        h("details", { class: "advanced-tools" }, [
          h("summary", null, "Advanced: every tool for this type"),
          toolBox,
        ]),
      ]),
    ]),
  ]));
  typeSelect.addEventListener("change", onFilter);
  statusSelect.addEventListener("change", onFilter);
  input.addEventListener("input", onFilter);
  input.addEventListener("input", function () { updateSmart(false); });
  previewType.addEventListener("change", function () { drawTools(); updateSmart(true); });
  alsoBox.addEventListener("change", function () { updateSmart(true); });

  try {
    const loaded = await Promise.all([api("/jobs"), api("/tools")]);
    if (!current(gen)) return;
    jobs = Array.isArray(loaded[0]) ? loaded[0] : [];
    tools = Array.isArray(loaded[1]) ? loaded[1] : [];
    drawJobs();
    drawTools();
  } catch (err) {
    if (!current(gen)) return;
    results.replaceChildren(isOffline(err) ? offlineBanner() : errorBlock(err.message));
  }
}

async function showEntity(id) {
  openDialog("Saved record", h("p", { class: "muted" }, "Loading…"));
  try {
    const loaded = await Promise.all([
      api("/entities/" + encodeURIComponent(id)),
      api("/entities/" + encodeURIComponent(id) + "/relationships"),
      api("/entities/" + encodeURIComponent(id) + "/evidence"),
    ]);
    const entity = loaded[0];
    const links = Array.isArray(loaded[1]) ? loaded[1].slice(0, 8) : [];
    const proof = Array.isArray(loaded[2]) ? loaded[2].slice(0, 5) : [];
    const info = confidenceInfo(entity.confidence);
    openDialog(entityLabel(entity.type), h("div", { class: "stack" }, [
      h("p", { class: "statement" }, textOf(entity.display_name || entity.value)),
      h("p", null, [pill("confidence", entity.confidence), " ", info.explain]),
      h("dl", null, [
        h("dt", null, "Kind"), h("dd", null, entityLabel(entity.type)),
        h("dt", null, "Value"), h("dd", null, textOf(entity.value)),
        h("dt", null, "First seen"), h("dd", null, formatTime(entity.first_seen)),
        h("dt", null, "Last seen"), h("dd", null, formatTime(entity.last_seen)),
      ]),
      h("h3", null, "Connections"),
      links.length
        ? h("ul", null, links.map(function (link) {
          const other = link.direction === "outbound" ? link.dst_value : link.src_value;
          const otherType = link.direction === "outbound" ? link.dst_type : link.src_type;
          return h("li", null, relationLabel(link.type) + " " + entityLabel(otherType) + " " + textOf(other) + ".");
        }))
        : h("p", { class: "empty" }, "No connections stored for this record."),
      h("h3", null, "Evidence behind it"),
      proof.length
        ? h("ul", null, proof.map(function (item) {
          return h("li", null, [
            textOf(item.tool) + " · ",
            h("a", { href: "#/evidence?id=" + encodeURIComponent(item.id), onclick: function () { el("detail-dialog").close(); } }, "Open evidence " + item.id),
          ]);
        }))
        : h("p", { class: "empty" }, "No evidence record is linked."),
    ]));
  } catch (err) {
    openDialog("Saved record", errorBlock(err.message));
  }
}

function showFinding(row) {
  const info = confidenceInfo(row.confidence);
  const how = serviceInfo(row.service_level);
  openDialog("Finding", h("div", { class: "stack" }, [
    h("p", { class: "statement" }, explainFinding(row)),
    h("p", null, [pill("confidence", row.confidence), " ", info.explain]),
    h("p", null, [pill("service", row.service_level), " ", how.explain]),
    h("dl", null, [
      h("dt", null, "Tool"), h("dd", null, textOf(row.tool) || "—"),
      h("dt", null, "Recorded"), h("dd", null, formatTime(row.created_at)),
      h("dt", null, "Investigation"), h("dd", null, row.job_id ? jobLink(row.job_id, row.job_id) : "—"),
      h("dt", null, "Evidence"), h("dd", null, row.evidence_id
        ? h("a", { href: "#/evidence?id=" + encodeURIComponent(row.evidence_id), onclick: function () { el("detail-dialog").close(); } }, "Record " + row.evidence_id)
        : "None linked"),
    ]),
    row.source_url ? h("p", null, ["Source: ", h("span", { class: "mono" }, row.source_url)]) : null,
  ]));
}

async function renderFindings(view, gen, params) {
  const jobId = params.get("job") || "";
  const table = h("div", null, h("p", { class: "muted" }, "Loading findings\u2026"));
  const summary = h("div", null);
  const confidence = h("select", { "aria-label": "Confidence" }, [h("option", { value: "" }, "Any confidence")].concat(
    Object.keys(CONFIDENCE).map(function (code) { return h("option", { value: code }, CONFIDENCE[code].label); })
  ));
  const kind = h("select", { "aria-label": "Result type" }, [h("option", { value: "" }, "Any kind of result")].concat(
    Object.keys(RESULTS.CATEGORIES).map(function (key) {
      return h("option", { value: key }, RESULTS.CATEGORIES[key].label);
    })
  ));
  const limit = h("select", { "aria-label": "How many findings to load" }, ["200", "500", "2000", "5000"].map(function (value) {
    return h("option", { value: value }, "Newest " + Number(value).toLocaleString());
  }));
  limit.value = jobId ? "5000" : "500";
  const jobField = h("input", { type: "text", value: jobId, placeholder: "Investigation id", "aria-label": "Investigation id" });
  let rows = [];
  let job = null;

  function visible() {
    return rows.filter(function (row) {
      if (confidence.value && textOf(row.confidence).toUpperCase() !== confidence.value) return false;
      if (kind.value && RESULTS.categoryOf(row) !== kind.value) return false;
      return true;
    });
  }

  function draw() {
    const shown = visible();
    summary.replaceChildren(job ? resultsSummary(job, rows) : h("p", { class: "footnote" },
      "Loaded the newest " + formatNumber(rows.length) + " findings across all investigations. Enter an investigation id for a written summary."));
    if (!rows.length) {
      table.replaceChildren(h("p", { class: "empty" }, job ? RESULTS.emptyMessage(job) : "No findings are saved yet. Findings appear after an investigation's tools report results."));
      return;
    }
    if (!shown.length) {
      table.replaceChildren(h("p", { class: "empty" }, "No finding matches these filters. Try \u201cAny confidence\u201d or \u201cAny kind of result\u201d."));
      return;
    }
    table.replaceChildren(resultsBrowser(shown, { showJobs: !job }));
  }

  async function load() {
    table.replaceChildren(h("p", { class: "muted" }, "Loading findings\u2026"));
    const id = jobField.value.trim();
    try {
      const loaded = await Promise.all([
        api("/findings" + queryString({ job_id: id, limit: limit.value })),
        id ? api("/jobs/" + encodeURIComponent(id)).catch(function (err) { return err && err.status === 404 ? { missing: true } : null; }) : Promise.resolve(null),
      ]);
      if (!current(gen)) return;
      rows = Array.isArray(loaded[0]) ? loaded[0] : [];
      job = loaded[1] && !loaded[1].missing ? loaded[1] : null;
      if (loaded[1] && loaded[1].missing && !rows.length) {
        summary.replaceChildren();
        table.replaceChildren(h("p", { class: "empty" }, "No investigation has the id \u201c" + id + "\u201d. Check the id, or clear the Investigation field to see findings from every investigation."));
        return;
      }
      await loadLearningContext(id, job && job.target_type);
      if (!current(gen)) return;
      draw();
    } catch (err) {
      if (!current(gen)) return;
      summary.replaceChildren();
      table.replaceChildren(isOffline(err) ? offlineBanner() : errorBlock(RESULTS.emptyMessage(null, err.message)));
    }
  }

  view.replaceChildren(h("section", { class: "page", "data-page": "findings" }, [
    pageHead("Findings", "Each result is a fact a tool saved, grouped by what it is. Repeats are shown once. Open \u201cRaw tool output\u201d to see exactly what the tool returned."),
    h("div", { class: "filters" }, [
      h("label", { class: "field" }, [h("span", null, "Confidence"), confidence]),
      h("label", { class: "field" }, [h("span", null, "Kind"), kind]),
      h("label", { class: "field" }, [h("span", null, "Investigation"), jobField]),
      h("label", { class: "field" }, [h("span", null, "Load"), limit]),
      h("button", { type: "button", class: "btn-primary", onclick: load }, "Reload"),
    ]),
    summary,
    table,
  ]));
  confidence.addEventListener("change", draw);
  kind.addEventListener("change", draw);
  load();
}

function evidenceQuery(offset, tool, level, jobId) {
  return "/evidence" + queryString({
    limit: 40,
    offset: offset,
    tool: tool,
    service_level: level,
    job_id: jobId,
  });
}

async function showEvidence(id) {
  openDialog("Evidence " + id, h("p", { class: "muted" }, "Loading the tool record…"));
  try {
    const detail = await api("/evidence/" + encodeURIComponent(id));
    const info = confidenceInfo(detail.confidence);
    const how = serviceInfo(detail.service_level);
    const findings = Array.isArray(detail.findings) ? detail.findings.slice(0, 8) : [];
    const why = Array.isArray(detail.why_linked) ? detail.why_linked.slice(0, 4) : [];
    const plain = RESULTS.describeEvidence(detail);
    openDialog("Evidence " + id, h("div", { class: "stack" }, [
      h("p", { class: "statement" }, plain.what),
      h("p", null, [resultBadge(plain.badge), " ", plain.badge.why, plain.link ? [" ", externalLink(plain.link, "Open page \u2197")] : null]),
      h("p", null, [pill("confidence", detail.confidence), " ", info.explain]),
      h("p", null, [pill("service", detail.service_level), " ", how.explain]),
      h("dl", null, [
        h("dt", null, "When"), h("dd", null, RESULTS.readableDate(detail.timestamp)),
        h("dt", null, "Exit code"), h("dd", null, detail.exit_code == null ? "Not recorded" : String(detail.exit_code)),
        h("dt", null, "Problem"), h("dd", null, textOf(detail.error) || "None recorded"),
        h("dt", null, "Source"), h("dd", null, textOf(detail.source_url) || "None recorded"),
      ]),
      rawDetails("Raw tool output", function () { return rawEvidenceText(detail); }),
      h("h3", null, "Findings that cite this record"),
      findings.length
        ? h("ul", null, RESULTS.buildItems(findings, "type").map(function (item) { return h("li", null, [resultBadge(item.badge), " ", item.explanation]); }))
        : h("p", { class: "empty" }, "No finding cites this record."),
      why.length ? h("h3", null, "Why it was linked") : null,
      why.map(function (item) {
        return h("p", { class: "callout" }, textOf(item.why_linked || item.summary));
      }),
    ]));
  } catch (err) {
    openDialog("Evidence", errorBlock(err.message));
  }
}

async function renderEvidence(view, gen, params) {
  let offset = 0;
  const table = h("div", null);
  const pager = h("div", { class: "pager" });
  const tool = h("input", { type: "text", placeholder: "sherlock", "aria-label": "Tool name" });
  const level = h("select", { "aria-label": "How it was recorded" }, [h("option", { value: "" }, "Any")].concat(
    Object.keys(SERVICE).map(function (code) { return h("option", { value: code }, SERVICE[code].label); })
  ));
  const jobField = h("input", { type: "text", value: params.get("job") || "", placeholder: "Investigation id", "aria-label": "Investigation id" });

  async function load() {
    table.replaceChildren(h("p", { class: "muted" }, "Loading evidence…"));
    try {
      const rows = await api(evidenceQuery(offset, tool.value.trim(), level.value, jobField.value.trim()));
      if (!current(gen)) return;
      const list = Array.isArray(rows) ? rows : [];
      draw(list);
      if (params.get("id") && offset === 0) {
        params.delete("id");
      }
    } catch (err) {
      if (!current(gen)) return;
      table.replaceChildren(isOffline(err) ? offlineBanner() : errorBlock(err.message));
      pager.replaceChildren();
    }
  }

  function draw(list) {
    const prev = h("button", { type: "button", disabled: offset === 0, onclick: function () {
      offset = Math.max(0, offset - 40);
      load();
    } }, "Previous");
    const next = h("button", { type: "button", disabled: list.length < 40, onclick: function () {
      offset += 40;
      load();
    } }, "Next");
    pager.replaceChildren(prev, h("span", { class: "muted" }, "Records " + (offset + 1) + "–" + (offset + list.length)), next);
    if (!list.length) {
      table.replaceChildren(h("p", { class: "empty" }, offset > 0 ? "There are no more evidence records on later pages." : "No evidence matches these filters. Evidence is saved when a tool reports output for an investigation; clear the filters or check the investigation id."));
      return;
    }
    const entries = RESULTS.dedupeEvidence(list);
    const hidden = list.length - entries.length;
    table.replaceChildren(h("div", null, [
      hidden > 0 ? h("p", { class: "footnote" }, formatNumber(hidden) + (hidden === 1 ? " identical record is" : " identical records are") + " shown once on this page. Stored data is unchanged.") : null,
      badgeLegend(),
      h("ul", { class: "result-list evidence-list" }, entries.map(evidenceCard)),
    ]));
  }

  view.replaceChildren(h("section", { class: "page", "data-page": "evidence" }, [
    pageHead("Evidence", "Evidence is the record of what a tool returned. Each card says in plain words what the record shows; the raw output is folded underneath."),
    h("div", { class: "filters" }, [
      h("label", { class: "field" }, [h("span", null, "Tool"), tool]),
      h("label", { class: "field" }, [h("span", null, "How we know"), level]),
      h("label", { class: "field" }, [h("span", null, "Investigation"), jobField]),
      h("button", { type: "button", class: "btn-primary", onclick: function () { offset = 0; load(); } }, "Apply"),
    ]),
    table,
    pager,
  ]));
  const focusId = params.get("id");
  await load();
  if (focusId && current(gen)) showEvidence(focusId);
}

/* ---------- Readable results (findings and evidence) ---------- */

const RESULTS = window.OsintResults;

const BADGE_LEGEND = [
  ["confirmed", "Confirmed", "An investigator confirmed it."],
  ["likely", "Likely", "Rated high or corroborated by the command center, or reported separately by two or more tools."],
  ["unverified", "Unverified", "Reported by one automated tool. Treat it as a lead."],
  ["conflict", "Conflict", "Disagrees with other evidence."],
];

function resultBadge(badge) {
  return h("span", { class: "badge badge-" + badge.key, title: badge.why }, badge.label);
}

function externalLink(url, label) {
  const safe = RESULTS.safeUrl(url);
  if (!safe) return null;
  return h("a", { class: "ext", href: safe, target: "_blank", rel: "noopener noreferrer" }, label || "Open page \u2197");
}

function badgeLegend() {
  return h("ul", { class: "badge-legend", "aria-label": "What the badges mean" }, BADGE_LEGEND.map(function (item) {
    return h("li", null, [resultBadge({ key: item[0], label: item[1], why: item[2] }), " ", h("span", { class: "muted" }, item[2])]);
  }));
}

function rawFindingText(rows) {
  return rows.map(function (row) {
    const head = "# " + RESULTS.toolName(row.tool) + " \u00b7 finding " + row.id + (row.evidence_id != null ? " \u00b7 evidence " + row.evidence_id : "");
    const body = RESULTS.prettyRaw(row.raw_json) || "(no raw output stored)";
    return head + "\n" + body;
  }).join("\n\n");
}

function rawDetails(label, makeText) {
  const pre = h("pre", { class: "raw-pre" });
  const details = h("details", { class: "raw" }, [h("summary", null, label), pre]);
  details.addEventListener("toggle", function () {
    if (details.open && !pre.textContent) pre.textContent = makeText();
  });
  return details;
}

// ---- Learning loop: verdicts and learned tool precision ----
async function loadLearningContext(jobId, jobType) {
  RESULTS.setJobType(jobType || "");
  try {
    const loaded = await Promise.all([
      api("/learning"),
      api("/learning/verdicts" + queryString({ job_id: jobId || "", limit: 5000 })),
    ]);
    RESULTS.setLearning(loaded[0] && loaded[0].tools);
    RESULTS.setVerdicts(loaded[1]);
  } catch (err) {
    // Older command center without the learning endpoints: badges fall back
    // to the saved confidence only.
    RESULTS.setLearning([]);
    RESULTS.setVerdicts({});
  }
}

async function postFeedback(path, body) {
  const response = await fetch("/api" + path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json", "X-OSINT-Feedback": "1" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (err) { data = null; }
  if (!response.ok) throw new Error(messageFrom(data, "Saving failed (" + response.status + ")."));
  return data;
}

const VERDICT_BUTTONS = [["correct", "Correct"], ["wrong", "Wrong"], ["unsure", "Not sure"]];

function feedbackRow(item, rerender) {
  const ids = item.rows.map(function (r) { return Number(r.id); }).filter(function (n) { return n > 0; });
  const status = h("span", { class: "feedback-status faint", role: "status" });
  const current = RESULTS.verdictOf(item.rows);
  const buttons = VERDICT_BUTTONS.map(function (pair) {
    const pressed = current === pair[0];
    return h("button", {
      type: "button",
      class: "feedback-btn feedback-" + pair[0] + (pressed ? " is-on" : ""),
      "aria-pressed": pressed ? "true" : "false",
      onclick: async function () {
        const verdict = pressed ? "clear" : pair[0];
        status.textContent = "Saving…";
        try {
          await postFeedback("/findings/verdicts", { finding_ids: ids.slice(0, 500), verdict: verdict });
          RESULTS.setVerdict(ids, verdict);
          rerender();
        } catch (err) {
          status.textContent = err.message;
        }
      },
    }, pair[1]);
  });
  if (!ids.length) return h("span", { hidden: true });
  return h("p", { class: "feedback-row" }, [h("span", { class: "faint" }, "Is this right? ")].concat(buttons, [" ", status]));
}

function resultItem(item, opts) {
  const meta = ["Source: " + item.tools.map(RESULTS.toolName).join(", "), RESULTS.readableDate(item.lastSeen || item.firstSeen)];
  if (item.copies > 1) meta.push("saved " + item.copies + " times, shown once");
  const extras = [];
  const link = externalLink(item.link, "Open page \u2197");
  if (link) extras.push(link);
  if (item.evidenceIds.length) {
    extras.push(h("a", { href: "#/evidence?id=" + encodeURIComponent(item.evidenceIds[0]) },
      item.evidenceIds.length > 1 ? "Evidence (" + item.evidenceIds.length + ")" : "Evidence"));
  }
  if (opts && opts.showJobs && item.jobIds.length) {
    extras.push(jobLink(item.jobIds[0], item.jobIds.length > 1 ? "Investigations (" + item.jobIds.length + ")" : "Investigation"));
  }
  const node = h("li", { class: "result-item" });
  function rerender() {
    const fresh = RESULTS.buildItems(item.rows)[0];
    if (fresh) {
      fresh.category = item.category;
      node.replaceWith(resultItem(Object.assign({}, item, { badge: fresh.badge }), opts));
    }
  }
  append(node, [
    h("div", { class: "result-head" }, [resultBadge(item.badge), h("span", { class: "result-title" }, item.title)]),
    h("p", { class: "result-why" }, item.explanation),
    h("p", { class: "result-meta" }, [meta.join(" \u00b7 ")].concat(extras.map(function (node) { return [" \u00b7 ", node]; }))),
    h("p", { class: "result-badge-why faint" }, item.badge.label + ": " + item.badge.why),
    feedbackRow(item, rerender),
    searchLinkDetails(item.searchLinks),
    rawDetails("Raw tool output (" + item.rows.length + ")", function () { return rawFindingText(item.rows); }),
  ]);
  return node;
}

// PhoneInfoga builds Google search links without running them. Show them
// folded, grouped, and clearly marked as searches to run by hand.
function searchLinkDetails(links) {
  if (!links || !links.length) return h("span", { hidden: true });
  const box = h("div", { class: "search-links" });
  const details = h("details", { class: "raw-details" }, [
    h("summary", {}, "Search links to try by hand (" + formatNumber(links.length) + ")"),
    box,
  ]);
  let drawn = false;
  details.addEventListener("toggle", function () {
    if (!details.open || drawn) return;
    drawn = true;
    box.append(h("p", { class: "faint" }, "PhoneInfoga built these Google searches but did not run them. Opening one runs the search from your browser."));
    const groups = {};
    links.forEach(function (l) { (groups[l.group] = groups[l.group] || []).push(l); });
    Object.keys(groups).forEach(function (name) {
      const ul = h("ul", { class: "search-link-list" });
      groups[name].forEach(function (l, i) {
        const label = name + " search " + (i + 1) + ": " + (decodeSiteHint(l.url) || "Google");
        const a = externalLink(l.url, label);
        if (a) ul.append(h("li", {}, a));
      });
      box.append(h("p", { class: "search-link-group" }, name + " (" + groups[name].length + ")"), ul);
    });
  });
  return details;
}

function decodeSiteHint(url) {
  try {
    const q = new URL(url).searchParams.get("q") || "";
    const m = /site:([^\s]+)/.exec(q);
    return m ? m[1] : "";
  } catch (err) {
    return "";
  }
}

function resultGroup(group, opts) {
  const list = h("ul", { class: "result-list" });
  const more = h("div", { class: "result-more" });
  const pageSize = (opts && opts.pageSize) || 20;
  let shown = 0;
  function showMore(count) {
    group.items.slice(shown, shown + count).forEach(function (item) { list.append(resultItem(item, opts)); });
    shown = Math.min(group.items.length, shown + count);
    more.replaceChildren();
    if (shown < group.items.length) {
      more.append(
        h("span", { class: "faint" }, "Showing " + formatNumber(shown) + " of " + formatNumber(group.items.length) + ". "),
        h("button", { type: "button", onclick: function () { showMore(50); } }, "Show 50 more"),
        " ",
        h("button", { type: "button", onclick: function () { showMore(group.items.length); } }, "Show all " + formatNumber(group.items.length))
      );
    }
  }
  showMore(pageSize);
  return h("details", { class: "result-group", open: opts && opts.open === false ? null : true, "data-group": group.key }, [
    h("summary", null, [
      h("span", { class: "group-title" }, group.title),
      group.blurb ? h("span", { class: "group-blurb" }, group.blurb) : null,
    ]),
    list,
    more,
  ]);
}

function resultsSummary(job, rows) {
  const s = RESULTS.summarize(job, rows);
  return h("div", { class: "results-summary" }, [
    h("p", { class: "statement" }, s.sentence),
    s.tools.length ? h("div", null, [
      h("h3", null, "Tools that ran"),
      h("ul", { class: "tool-chips" }, s.tools.map(function (tool) {
        const state = tool.statusLabel || TOOL_RUN[tool.status] || tool.status;
        return h("li", null, [
          h("strong", null, tool.name),
          tool.role ? h("span", { class: "muted" }, " \u2014 " + tool.role) : null,
          h("span", { class: "faint" }, " \u00b7 " + state + (tool.findings == null ? "" : " \u00b7 " + formatNumber(tool.findings) + (tool.findings === 1 ? " finding" : " findings"))),
        ]);
      })),
    ]) : null,
    s.categories.length ? h("div", null, [
      h("h3", null, "What was found"),
      h("ul", { class: "category-list" }, s.categories.map(function (cat) {
        return h("li", null, h("a", { href: "#", "data-jump": cat.key, onclick: function (event) {
          event.preventDefault();
          const target = document.querySelector('[data-group="' + cat.key + '"]');
          if (target) { target.open = true; target.scrollIntoView({ block: "start" }); }
        } }, cat.title));
      })),
    ]) : null,
    s.notable.length ? h("div", { class: "callout" }, [
      h("h3", null, "Worth knowing"),
      h("ul", null, s.notable.map(function (note) { return h("li", null, note); })),
    ]) : null,
  ]);
}

// Shared grouped list used by the investigation page and the Findings page.
function resultsBrowser(rows, opts) {
  const groupBy = h("select", { "aria-label": "Group results by" }, [
    h("option", { value: "type" }, "Group by what was found"),
    h("option", { value: "tool" }, "Group by tool"),
  ]);
  const filter = h("input", { type: "search", placeholder: "Filter these results", "aria-label": "Filter results" });
  const count = h("p", { class: "footnote" });
  const body = h("div", { class: "result-groups" });
  function draw() {
    const needle = filter.value.trim().toLowerCase();
    let items = RESULTS.buildItems(rows, groupBy.value);
    if (needle) {
      items = items.filter(function (item) {
        return (item.title + " " + item.explanation + " " + item.tools.join(" ") + " " + item.link).toLowerCase().indexOf(needle) !== -1;
      });
    }
    const groups = RESULTS.groupItems(items, groupBy.value);
    count.textContent = "Showing " + formatNumber(items.length) + " distinct " + (items.length === 1 ? "result" : "results") +
      " from " + formatNumber(rows.length) + " saved " + (rows.length === 1 ? "finding" : "findings") + ". Repeats are shown once; stored data is unchanged.";
    body.replaceChildren();
    if (!groups.length) {
      body.append(h("p", { class: "empty" }, needle ? "No result matches \u201c" + filter.value.trim() + "\u201d." : (opts.emptyText || "No findings to show.")));
      return;
    }
    groups.forEach(function (group) { body.append(resultGroup(group, opts)); });
  }
  groupBy.addEventListener("change", draw);
  filter.addEventListener("input", draw);
  draw();
  return h("div", { class: "results-browser" }, [
    h("div", { class: "filters" }, [
      h("label", { class: "field grow" }, [h("span", null, "Filter"), filter]),
      h("label", { class: "field" }, [h("span", null, "Group"), groupBy]),
    ]),
    count,
    badgeLegend(),
    body,
  ]);
}

async function paintResults(node, job, gen) {
  node.replaceChildren(h("p", { class: "muted" }, "Loading results\u2026"));
  let rows;
  try {
    const data = await api("/findings" + queryString({ job_id: job.id, limit: 5000 }));
    rows = Array.isArray(data) ? data : [];
  } catch (err) {
    if (!current(gen)) return;
    node.replaceChildren(isOffline(err) ? offlineBanner() : errorBlock(RESULTS.emptyMessage(job, err.message)));
    return;
  }
  await loadLearningContext(job.id, job.target_type);
  if (!current(gen)) return;
  if (!rows.length) {
    node.replaceChildren(resultsSummary(job, rows), h("p", { class: "empty" }, RESULTS.emptyMessage(job)));
    return;
  }
  node.replaceChildren(h("div", null, [
    resultsSummary(job, rows),
    rows.length >= 5000 ? h("p", { class: "footnote" }, "Only the newest 5,000 findings are loaded.") : null,
    resultsBrowser(rows, { emptyText: RESULTS.emptyMessage(job) }),
  ]));
}

function evidenceCard(entry) {
  const row = entry.row;
  const info = RESULTS.describeEvidence(row);
  const meta = [RESULTS.readableDate(row.timestamp)];
  if (info.exitText) meta.push(info.exitText);
  if (entry.copies.length > 1) meta.push(entry.copies.length + " identical records shown once");
  const extras = [];
  const link = externalLink(info.link, "Open page \u2197");
  if (link) extras.push(link);
  return h("li", { class: "result-item" }, [
    h("div", { class: "result-head" }, [resultBadge(info.badge), h("span", { class: "result-title" }, info.title)]),
    h("p", { class: "result-why" }, info.what),
    h("p", { class: "result-meta" }, [meta.join(" \u00b7 ")].concat(extras.map(function (node) { return [" \u00b7 ", node]; })).concat([
      " \u00b7 ",
      h("button", { type: "button", class: "linkish", onclick: function () { showEvidence(row.id); } }, "Full record #" + row.id),
    ])),
    rawDetails("Raw tool output", function () { return rawEvidenceText(row); }),
  ]);
}

function rawEvidenceText(row) {
  const parts = [];
  if (row.command) parts.push("# Command\n" + row.command);
  if (row.parsed_json) parts.push("# Parsed output\n" + RESULTS.prettyRaw(row.parsed_json));
  if (row.stdout_excerpt) parts.push("# Output excerpt\n" + row.stdout_excerpt);
  if (row.stderr_excerpt) parts.push("# Error excerpt\n" + row.stderr_excerpt);
  if (row.error) parts.push("# Problem\n" + row.error);
  return parts.join("\n\n") || "(no raw output stored)";
}

function assessmentStatus(code) {
  if (code === "USER_CONFIRMED") return "An investigator confirmed the leading identity.";
  if (code === "UNRESOLVED") return "The identity is not settled.";
  if (SERVICE[code]) return SERVICE[code].explain;
  return code ? "Status recorded by the command center: " + code + "." : "";
}

function paintSummary(node, summary) {
  if (!summary) {
    node.replaceChildren(h("p", { class: "empty" }, "No plain-language summary is stored for this investigation."));
    return;
  }
  const info = confidenceInfo(summary.overall_confidence);
  const inner = summary.summary || {};
  const candidates = asList(inner.identity_candidates).slice(0, 5);
  const contradictions = asList(inner.contradictions).slice(0, 4);
  const recommendations = asList(summary.recommendations).slice(0, 6);
  const strong = asList(inner.strong_evidence).slice(0, 4);
  const temporal = summary.temporal || {};
  const notes = asList(temporal.notes).concat(asList(temporal.conflicts)).slice(0, 4);
  const tiers = summary.source_quality || {};
  node.replaceChildren(h("div", { class: "stack" }, [
    h("p", { class: "muted" }, "This explanation is computed from saved records. No tool is run when you open it."),
    h("p", { class: "statement" }, textOf(summary.statement) || "No summary sentence was recorded."),
    h("p", null, [pill("confidence", summary.overall_confidence), " ", info.explain]),
    summary.why ? h("p", { class: "callout" }, summary.why) : null,
    assessmentStatus(summary.status) ? h("p", { class: "muted" }, assessmentStatus(summary.status)) : null,
    candidates.length ? h("h3", null, "Possible identities") : null,
    candidates.map(function (item) {
      return h("p", null, [
        h("strong", null, textOf(item.name) || "Unnamed"),
        " · ",
        pill("confidence", item.confidence),
        item.why ? h("span", { class: "muted" }, " " + item.why) : null,
      ]);
    }),
    strong.length ? h("h3", null, "Strongest points") : null,
    strong.map(function (item) { return h("p", null, textOf(item.explanation)); }),
    contradictions.length ? h("h3", null, "Disagreements") : null,
    contradictions.map(function (item) {
      return h("p", null, textOf(item.explanation) || "Sources disagree.");
    }),
    notes.length ? h("h3", null, "Timing") : null,
    notes.map(function (item) { return h("p", null, textOf(item)); }),
    Object.keys(tiers).length ? h("h3", null, "Source quality") : null,
    Object.keys(TIER).map(function (key) {
      if (!tiers[key]) return null;
      return h("p", { class: "muted" }, TIER[key] + ": " + tiers[key]);
    }),
    recommendations.length ? h("h3", null, "What to review next") : null,
    recommendations.length ? h("p", { class: "muted" }, "These are suggestions only. This dashboard does not run them.") : null,
    recommendations.map(function (item) {
      return h("p", null, [
        textOf(item.check_text || item.check),
        item.why ? h("span", { class: "muted" }, " " + item.why) : null,
      ]);
    }),
  ]));
}

async function renderInvestigation(view, gen, id) {
  if (!/^[A-Za-z0-9_-]{1,80}$/.test(id)) {
    view.replaceChildren(errorBlock("That investigation id is not valid."));
    return;
  }
  const summaryNode = h("div", null, h("p", { class: "muted" }, "Loading the plain-language summary…"));
  const progressNode = h("div", null);
  const toolsNode = h("div", null);
  const metaNode = h("div", null);
  const resultsNode = h("div", null, h("p", { class: "muted" }, "Loading results\u2026"));
  let resultsPainted = "";
  view.replaceChildren(h("article", { class: "page", "data-page": "investigation" }, [
    h("a", { class: "back", href: "#/search" }, "Back to search"),
    metaNode,
    h("section", { class: "card", "data-section": "results" }, [h("h2", null, "Results"), resultsNode]),
    h("section", { class: "card" }, [h("h2", null, "Identity assessment"), summaryNode]),
    h("section", { class: "card" }, [h("h2", null, "Progress"), progressNode]),
    h("section", { class: "card" }, [
      h("div", { class: "card-head" }, [
        h("h2", null, "Tool status"),
        h("span", { class: "toolbar" }, [
          h("a", { href: "#/findings?job=" + encodeURIComponent(id) }, "Findings"),
          h("a", { href: "#/evidence?job=" + encodeURIComponent(id) }, "Evidence"),
        ]),
      ]),
      toolsNode,
    ]),
  ]));

  function paint(job) {
    const pair = JOB_STATUS[job.status] || [job.status || "Unknown", ""];
    const errors = asList(job.errors);
    metaNode.replaceChildren(
      h("h1", null, textOf(job.target) || "Investigation"),
      h("p", { class: "lede" }, [
        targetTypeLabel(job.target_type),
        " · ",
        statusPill(job.status, job),
        " · ",
        job.passive === 0 || job.passive === false || job.passive === "0"
          ? "Active mode was requested"
          : "Passive lookup",
      ]),
      h("p", { class: "muted" }, jobIsStaleRun(job)
        ? "The command center has marked this running for " + formatAge(job.started_at || job.created_at) + ". This dashboard cannot change that status."
        : pair[1]),
      h("p", { class: "footnote" }, "Started " + RESULTS.readableDate(job.started_at) + " · Finished " + (job.completed_at ? RESULTS.readableDate(job.completed_at) : "not yet")),
      errors.length ? h("div", null, [
        h("h2", null, "Problems recorded"),
        h("ul", null, errors.slice(0, 8).map(function (item) { return h("li", null, textOf(item)); })),
      ]) : h("span", { hidden: true })
    );
    const resultsState = job.status + "|" + reportedFindings(job);
    if (resultsState !== resultsPainted) {
      resultsPainted = resultsState;
      paintResults(resultsNode, job, gen);
    }
    const map = stageMap(job.stages);
    progressNode.replaceChildren(h("ol", { class: "steps" }, STAGES.map(function (item) {
      const stage = map[item[0]];
      const status = (stage && stage.status) || "pending";
      return h("li", { class: "step" }, [
        h("span", { class: "step-name" }, item[1]),
        h("span", { class: "pill pill-" + status }, STAGE_STATE[status] || status),
        h("span", { class: "muted" }, item[2]),
      ]);
    })));
    const rows = toolRows(job);
    const reported = reportedFindings(job);
    toolsNode.replaceChildren(
      h("p", { class: "footnote" }, reported == null
        ? "No per-tool finding counts are stored yet."
        : "Tools reported " + formatNumber(reported) + " findings. That can differ from the findings list when the same fact is saved once."),
      rows.length
        ? h("div", { class: "table-wrap" }, h("table", null, [
          h("thead", null, h("tr", null, ["Tool", "Status", "Findings", "Time", "Problem"].map(function (name) {
            return h("th", { scope: "col" }, name);
          }))),
          h("tbody", null, rows.map(function (row) {
            const seconds = typeof row.duration === "number" ? row.duration.toFixed(1) + "s" : "—";
            return h("tr", null, [
              h("td", null, [h("div", null, RESULTS.toolName(row.tool)), RESULTS.toolRole(row.tool) ? h("div", { class: "faint" }, RESULTS.toolRole(row.tool)) : null]),
              h("td", null, TOOL_RUN[row.status] || row.status),
              h("td", null, row.finding_count == null ? "—" : formatNumber(row.finding_count)),
              h("td", null, seconds),
              h("td", null, textOf(row.error) || "None"),
            ]);
          })),
        ]))
        : h("p", { class: "empty" }, "No tool has reported a result for this investigation yet.")
    );
  }

  let job;
  try {
    job = await api("/jobs/" + encodeURIComponent(id));
  } catch (err) {
    if (!current(gen)) return;
    view.replaceChildren(h("a", { class: "back", href: "#/search" }, "Back to search"), isOffline(err) ? offlineBanner() : errorBlock(err.message));
    return;
  }
  if (!current(gen)) return;
  paint(job);
  if (job.status === "running" || job.status === "queued") {
    pollTimer = setInterval(async function () {
      if (!current(gen)) return;
      try {
        const next = await api("/jobs/" + encodeURIComponent(id));
        if (!current(gen)) return;
        const changed = next.status !== job.status;
        job = next;
        paint(job);
        if (changed && next.status !== "running" && next.status !== "queued") {
          const fresh = await api("/investigations/" + encodeURIComponent(id) + "/summary");
          if (current(gen)) paintSummary(summaryNode, fresh);
        }
      } catch (err) {
        return;
      }
    }, 8000);
  }
  try {
    const summary = await api("/investigations/" + encodeURIComponent(id) + "/summary");
    if (!current(gen)) return;
    paintSummary(summaryNode, summary);
  } catch (err) {
    if (!current(gen)) return;
    summaryNode.replaceChildren(errorBlock(err.message));
  }
}

// ---- What the dashboard has learned ----
function pct(value) {
  return value == null || value === "" ? "\u2014" : Math.round(Number(value) * 100) + "%";
}

const LEARN_STATUS = {
  ok: "In use",
  failing: "Skipped: keeps failing",
  false_positive_heavy: "Skipped: often wrong",
};

async function renderLearning(view, gen) {
  const body = h("div", null, h("p", { class: "muted" }, "Loading what the dashboard has learned\u2026"));
  const message = h("p", { class: "feedback-status", role: "status" });
  const exportBtn = h("button", { type: "button", id: "learning-export" }, "Export (JSON)");
  const resetBtn = h("button", { type: "button", class: "danger", id: "learning-reset" }, "Reset learning");
  view.replaceChildren(h("section", { class: "page", "data-page": "learning" }, [
    pageHead("What the dashboard has learned",
      "Every finished investigation and every Correct / Wrong / Not sure vote updates these numbers. Smart search uses them to rank tools and to skip ones that keep failing or are often wrong. Badges use them too."),
    h("section", { class: "card" }, [
      h("div", { class: "card-head" }, [h("h2", null, "Tool scores"), h("span", { class: "toolbar" }, [exportBtn, " ", resetBtn])]),
      message,
      body,
    ]),
  ]));

  exportBtn.addEventListener("click", async function () {
    try {
      const data = await api("/learning/export");
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = h("a", { href: url, download: "osint-learning-export.json" });
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      message.textContent = "Exported " + formatNumber((data.verdicts || []).length) + " votes and " + formatNumber((data.tool_outcomes || []).length) + " tool records.";
    } catch (err) {
      message.textContent = "Export failed: " + err.message;
    }
  });
  resetBtn.addEventListener("click", async function () {
    if (!window.confirm("Reset everything the dashboard has learned? Votes and tool statistics are deleted. Saved investigations and findings are not touched.")) return;
    try {
      await postFeedback("/learning/reset", { confirm: true });
      message.textContent = "Learning was reset.";
      draw();
    } catch (err) {
      message.textContent = "Reset failed: " + err.message;
    }
  });

  async function draw() {
    let data;
    try {
      data = await api("/learning");
    } catch (err) {
      if (!current(gen)) return;
      body.replaceChildren(isOffline(err) ? offlineBanner() : errorBlock(err.message));
      return;
    }
    if (!current(gen)) return;
    const rows = Array.isArray(data.tools) ? data.tools.filter(function (t) { return t.runs || t.decided_votes || (t.votes && t.votes.unsure) || t.skipped; }) : [];
    const meta = data.meta || {};
    const facts = [
      formatNumber(data.total_votes || 0) + " votes so far",
      "precision starts from a default and moves as votes arrive (each default counts as " + formatNumber(data.prior_votes || 10) + " votes, so a handful of votes can't swing it)",
    ];
    if (meta.backfilled_jobs) facts.push("learned from " + formatNumber(Number(meta.backfilled_jobs)) + " earlier investigations when this started");
    if (meta.reset_at) facts.push("last reset " + RESULTS.readableDate(meta.reset_at));
    const table = rows.length ? h("div", { class: "table-wrap" }, h("table", { class: "learning-table" }, [
      h("thead", null, h("tr", null, ["Tool", "Type", "Runs", "Problems", "Findings", "Repeats", "Avg time", "Votes \u2713 / \u2717 / ?", "Precision", "Status"].map(function (name) {
        return h("th", { scope: "col" }, name);
      }))),
      h("tbody", null, rows.map(function (t) {
        const v = t.votes || {};
        const problems = [];
        if (t.errors) problems.push(t.errors + " failed");
        if (t.timeouts) problems.push(t.timeouts + " timed out");
        if (t.rate_limited) problems.push(t.rate_limited + " rate-limited");
        if (t.rejected) problems.push(t.rejected + " bad input (not counted)");
        return h("tr", null, [
          h("td", null, RESULTS.toolName(t.tool)),
          h("td", null, targetTypeLabel(t.target_type)),
          h("td", null, formatNumber(t.runs || 0) + (t.skipped ? " (+" + t.skipped + " skipped)" : "")),
          h("td", null, problems.length ? problems.join(", ") : "none"),
          h("td", null, formatNumber(t.findings || 0)),
          h("td", null, pct(t.duplicate_rate)),
          h("td", null, t.avg_runtime_seconds == null ? "\u2014" : t.avg_runtime_seconds + " s"),
          h("td", null, (v.correct || 0) + " / " + (v.wrong || 0) + " / " + (v.unsure || 0)),
          h("td", { title: "default " + pct(t.prior_precision) + (t.raw_precision != null ? ", raw votes " + pct(t.raw_precision) : "") }, pct(t.precision)),
          h("td", null, [h("span", { class: "badge-learn badge-learn-" + (t.status || "ok") }, LEARN_STATUS[t.status] || t.status), t.status_reason ? h("div", { class: "faint" }, t.status_reason) : null]),
        ]);
      })),
    ])) : h("p", { class: "empty" }, "Nothing learned yet. Finish an investigation or vote on a finding and the numbers appear here.");
    body.replaceChildren(
      h("ul", { class: "learning-facts" }, facts.map(function (f) { return h("li", null, f); })),
      table,
      h("p", { class: "footnote" }, data.privacy || "")
    );
  }
  draw();
}

function parseHash() {
  let raw = (location.hash || "#/overview").replace(/^#/, "");
  if (raw.charAt(0) !== "/") raw = "/" + raw;
  const splitAt = raw.indexOf("?");
  const path = splitAt === -1 ? raw : raw.slice(0, splitAt);
  const query = splitAt === -1 ? "" : raw.slice(splitAt + 1);
  return { parts: path.split("/").filter(Boolean), params: new URLSearchParams(query) };
}

function highlight(section) {
  document.querySelectorAll("[data-nav]").forEach(function (link) {
    if (link.getAttribute("data-nav") === section) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
}

function route() {
  stopPoll();
  if (typeof stopDetroitRefresh === "function") stopDetroitRefresh();
  if (typeof stopShotspotterRefresh === "function") stopShotspotterRefresh();
  const dialog = el("detail-dialog");
  if (dialog && dialog.open) dialog.close();
  const gen = ++renderGen;
  const parsed = parseHash();
  const top = parsed.parts[0] || "overview";
  const view = el("view");
  highlight(top === "investigation" ? "search" : top);
  if (top !== "detroit" && typeof disposeDetroitMap === "function") disposeDetroitMap();
  if (top !== "shotspotter-detroit" && typeof disposeShotspotterMap === "function") disposeShotspotterMap();
  if (top === "overview") renderOverview(view, gen);
  else if (top === "search") renderSearch(view, gen);
  else if (top === "findings") renderFindings(view, gen, parsed.params);
  else if (top === "evidence") renderEvidence(view, gen, parsed.params);
  else if (top === "learning") renderLearning(view, gen);
  else if (top === "detroit" && typeof renderDetroit === "function") renderDetroit(view, gen, parsed.params);
  else if (top === "detroit") view.replaceChildren(errorBlock("The Detroit page did not load."));
  else if (top === "shotspotter-detroit" && typeof renderShotspotter === "function") renderShotspotter(view, gen, parsed.params);
  else if (top === "shotspotter-detroit") view.replaceChildren(errorBlock("The ShotSpotter Detroit page did not load."));
  else if (top === "exposure" && typeof renderExposure === "function") renderExposure(view, gen, parsed.params);
  else if (top === "exposure") view.replaceChildren(errorBlock("The Internet Exposure page did not load."));
  else if (top === "analytics" && typeof renderAnalytics === "function") renderAnalytics(view, gen);
  else if (top === "reports" && typeof renderReports === "function") renderReports(view, gen);
  else if (top === "settings" && typeof renderSettings === "function") renderSettings(view, gen);
  else if (top === "investigation" && parsed.parts[1]) renderInvestigation(view, gen, parsed.parts[1]);
  else view.replaceChildren(errorBlock("That page does not exist."));
  view.focus();
}

el("dialog-close").addEventListener("click", function () {
  el("detail-dialog").close();
});

window.addEventListener("hashchange", route);
refreshHealth();
setInterval(refreshHealth, 15000);
if (!location.hash) location.hash = "#/overview";
else route();
