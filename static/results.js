"use strict";

// Plain-language formatting for saved findings and evidence.
// Pure functions only: no DOM, no network, no stored-data changes.
// app.js turns these view models into page elements.
(function (root) {
  const TOOL_NAMES = {
    sherlock: "Sherlock",
    maigret: "Maigret",
    spiderfoot: "SpiderFoot",
    holehe: "Holehe",
    subfinder: "Subfinder",
    amass: "Amass",
    recon_ng: "Recon-ng",
    identity: "Identity organizer",
    profile: "Profile reader",
    dns: "DNS lookup",
    website: "Homepage reader",
    asn: "Network lookup",
    phone: "Phone parser",
    phoneinfoga: "PhoneInfoga",
    ignorant: "Ignorant",
    theharvester: "theHarvester",
    blackbird: "Blackbird",
    social_analyzer: "Social Analyzer",
    waybackurls: "waybackurls",
    katana: "Katana",
  };

  const TOOL_ROLES = {
    sherlock: "checks hundreds of sites for a username",
    maigret: "checks sites for a username and related names",
    spiderfoot: "runs public-source modules on the target",
    holehe: "checks which sites an email address is registered on",
    subfinder: "lists subdomains from public sources",
    amass: "maps subdomains and related assets",
    recon_ng: "runs web reconnaissance modules",
    identity: "organizes account results into public profiles",
    profile: "reads public profile pages",
    dns: "looks up public DNS records",
    website: "reads the public homepage",
    asn: "looks up network ownership",
    phone: "parses phone number formats",
    phoneinfoga: "reads a phone number's country and formats",
    ignorant: "checks which sites a phone number is registered on",
    theharvester: "collects subdomains, IPs and emails from public sources",
    blackbird: "checks WhatsMyName sites for a username or email",
    social_analyzer: "looks for a username on popular social sites",
    waybackurls: "lists addresses that web archives saved for a site",
    katana: "lightly crawls the site's own pages (active mode only)",
  };

  // These tools re-read other tools' results, so they do not count as an
  // independent second source.
  const DERIVED_TOOLS = { identity: true, profile: true };

  const CATEGORIES = {
    accounts: {
      label: "Accounts on other sites",
      order: 1,
      title: function (n, sites) { return sites === 1 ? "Account found on 1 site" : "Accounts found on " + formatCount(sites) + " sites"; },
      blurb: "Public profile pages that matched the name. A match means a page exists; it does not prove who runs it.",
    },
    email_sites: {
      label: "Sites an email is registered on",
      order: 2,
      title: function (n) { return "Sites this email is registered on (" + formatCount(n) + ")"; },
      blurb: "Sites that reported an account exists for this email address. The account owner is not shown.",
    },
    subdomains: {
      label: "Subdomains",
      order: 3,
      title: function (n) { return "Subdomains (" + formatCount(n) + ")"; },
      blurb: "Host names under the domain, listed in public sources such as certificate logs. Some may be old or no longer work.",
    },
    phone_sites: {
      label: "Sites a phone number is registered on",
      order: 2.5,
      title: function (n) { return "Sites this phone number may be registered on (" + formatCount(n) + ")"; },
      blurb: "Sites that answered as if an account uses this phone number. These checks often give false positives, so treat each one as a lead.",
    },
    phone_details: {
      label: "Phone number details",
      order: 2.6,
      title: function (n) { return "Phone number details (" + formatCount(n) + ")"; },
      blurb: "What the number's format says: its country and how it is written locally and internationally. This comes from the number itself, not from a lookup of who owns it.",
    },
    archived_urls: {
      label: "Archived pages",
      order: 3.5,
      title: function (n) { return "Archived pages (" + formatCount(n) + ")"; },
      blurb: "Addresses on this site that web archives (Wayback Machine, Common Crawl, AlienVault OTX) saved at some point. A page may no longer exist.",
    },
    crawled_urls: {
      label: "Pages found by a light crawl",
      order: 3.6,
      title: function (n) { return "Pages found by a light crawl (" + formatCount(n) + ")"; },
      blurb: "Pages on the site's own host that a shallow, rate-limited crawl reached.",
    },
    ip_addresses: {
      label: "IP addresses",
      order: 7.5,
      title: function (n) { return "IP addresses seen with the domain (" + formatCount(n) + ")"; },
      blurb: "Addresses that public sources list next to this domain or its subdomains. They are not checked here and may be shared or out of date.",
    },
    related_usernames: {
      label: "Related usernames",
      order: 4,
      title: function (n) { return "Related usernames (" + formatCount(n) + ")"; },
      blurb: "Other names a tool saw linked to the target. Treat them as leads to check.",
    },
    linked_sites: {
      label: "Websites linked from profiles",
      order: 5,
      title: function (n) { return "Websites linked from profiles (" + formatCount(n) + ")"; },
      blurb: "Sites that a public profile links to.",
    },
    contacts: {
      label: "Public contact details",
      order: 6,
      title: function (n) { return "Public contact details (" + formatCount(n) + ")"; },
      blurb: "Contact details or addresses that a public page lists.",
    },
    dns: {
      label: "DNS records",
      order: 7,
      title: function (n) { return "DNS records (" + formatCount(n) + ")"; },
      blurb: "Public DNS answers: where the name points, its mail servers and name servers.",
    },
    network: {
      label: "Network and hosting",
      order: 8,
      title: function (n) { return "Network and hosting (" + formatCount(n) + ")"; },
      blurb: "Who hosts the address and which network it belongs to.",
    },
    identities: {
      label: "Possible identities",
      order: 9,
      title: function (n) { return "Possible identities (" + formatCount(n) + ")"; },
      blurb: "Names that might belong to the target. These are leads, not proof.",
    },
    conflicts: {
      label: "Conflicting evidence",
      order: 10,
      title: function (n) { return "Conflicting evidence (" + formatCount(n) + ")"; },
      blurb: "Results that disagree with other results.",
    },
    seed: {
      label: "Starting point",
      order: 11,
      title: function (n) { return "Starting point (" + formatCount(n) + ")"; },
      blurb: "The target itself, as a tool recorded it. This is not a discovery.",
    },
    other: {
      label: "Other findings",
      order: 12,
      title: function (n) { return "Other findings (" + formatCount(n) + ")"; },
      blurb: "Findings that do not fit the groups above.",
    },
  };

  const RELATION_TEXT = {
    RESOLVES_TO: "points to",
    HAS_MX_RECORD: "receives mail through",
    HAS_NAMESERVER: "uses the name server",
    BELONGS_TO_ASN: "belongs to the network",
    HOSTED_BY: "is hosted by",
    HOSTS: "hosts",
    HAS_PUBLIC_CONTACT: "publishes the contact",
    HAS_PUBLIC_CRYPTO_ADDRESS: "publishes the crypto address",
    HAS_EMAIL: "uses the email",
    HAS_PHONE: "uses the phone number",
    IDENTITY_CANDIDATE: "may be",
    USES_TECHNOLOGY: "uses",
    OWNS_DOMAIN: "owns the domain",
    MENTIONED_BY: "is mentioned by",
    CONTRADICTS: "conflicts with",
    ASSOCIATED_WITH: "is associated with",
  };

  const BADGES = {
    confirmed: { label: "Confirmed", order: 1 },
    likely: { label: "Likely", order: 2 },
    unverified: { label: "Unverified", order: 3 },
    conflict: { label: "Conflict", order: 0 },
  };

  function text(value) {
    return value == null ? "" : String(value).trim();
  }

  function toolName(tool) {
    const key = text(tool).toLowerCase();
    return TOOL_NAMES[key] || (key ? key.charAt(0).toUpperCase() + key.slice(1) : "Unknown tool");
  }

  function toolRole(tool) {
    return TOOL_ROLES[text(tool).toLowerCase()] || "";
  }

  function parseRaw(value) {
    if (value == null || value === "") return null;
    if (typeof value === "object") return value;
    try {
      const parsed = JSON.parse(String(value));
      return parsed && typeof parsed === "object" ? parsed : null;
    } catch (err) {
      return null;
    }
  }

  function safeUrl(value) {
    const v = text(value);
    if (!/^https?:\/\/[^\s<>"']+$/i.test(v)) return "";
    return v;
  }

  function linkOf(row) {
    const raw = parseRaw(row.raw_json || row.parsed_json) || {};
    const direct = safeUrl(row.source_url) || safeUrl(raw.url);
    if (direct) return direct;
    const sf = /<SFURL>(.*?)<\/SFURL>/i.exec(text(raw.data));
    if (sf && safeUrl(sf[1])) return safeUrl(sf[1]);
    if (safeUrl(row.dst_value)) return safeUrl(row.dst_value);
    if (safeUrl(row.value)) return safeUrl(row.value);
    return "";
  }

  function hostOf(url) {
    const m = /^https?:\/\/([^/?#:]+)/i.exec(text(url));
    return m ? m[1].toLowerCase().replace(/^www\./, "") : "";
  }

  // A rough "site" for counting: the last two labels of the host, or three
  // when the second-to-last is a short country-style label (co.uk).
  function siteKeyOf(host) {
    const parts = text(host).toLowerCase().split(".").filter(Boolean);
    if (parts.length <= 2) return parts.join(".");
    const last = parts[parts.length - 1];
    const second = parts[parts.length - 2];
    if (last.length === 2 && second.length <= 3) return parts.slice(-3).join(".");
    return parts.slice(-2).join(".");
  }

  function normalizeUrlKey(url) {
    const v = safeUrl(url);
    if (!v) return "";
    return v.toLowerCase()
      .replace(/^https?:\/\//, "")
      .replace(/^www\./, "")
      .replace(/[#].*$/, "")
      .replace(/\/+$/, "");
  }

  function siteName(row, link) {
    const raw = parseRaw(row.raw_json) || {};
    let name = text(raw.sitename) || text(raw.site);
    if (!name && raw.data) {
      const first = text(raw.data).split("\n")[0].replace(/\s*\(Category:.*$/i, "");
      if (first && !/<SFURL>/i.test(first)) name = first;
    }
    if (!name && text(row.dst_type) === "social_account") {
      const dst = text(row.dst_value);
      const idx = dst.indexOf(":");
      if (idx > 0 && !/^https?:/i.test(dst)) name = dst.slice(0, idx);
    }
    if (!name) name = hostOf(link);
    name = name.replace(/\s+user\d*$/i, "").replace(/\s*\((user|users)\)$/i, "");
    return name || "an unnamed site";
  }

  function accountName(row) {
    const raw = parseRaw(row.raw_json) || {};
    if (text(raw.username)) return text(raw.username);
    const dst = text(row.dst_value);
    if (text(row.dst_type) === "social_account" && dst.indexOf(":") > 0 && !/^https?:/i.test(dst)) {
      return dst.slice(dst.indexOf(":") + 1);
    }
    return text(row.value);
  }

  function categoryOf(row) {
    const rel = text(row.relationship_type).toUpperCase();
    const tool = text(row.tool).toLowerCase();
    const etype = text(row.entity_type).toLowerCase();
    const dtype = text(row.dst_type).toLowerCase();
    if (text(row.service_level).toUpperCase() === "CONTRADICTED" || rel === "CONTRADICTS") return "conflicts";
    if (tool === "holehe" || (etype === "email" && rel === "LINKED_ACCOUNT")) return "email_sites";
    if (etype === "email" && dtype === "social_account") return "email_sites";
    if (tool === "ignorant" || (etype === "phone" && dtype === "social_account")) return "phone_sites";
    if (tool === "phoneinfoga") return "phone_details";
    if (tool === "waybackurls") return "archived_urls";
    if (tool === "katana") return "crawled_urls";
    if (rel === "ASSOCIATED_WITH" && dtype === "ip") return "ip_addresses";
    if (rel === "HAS_SUBDOMAIN" || (!rel && etype === "subdomain")) return "subdomains";
    if (rel === "LINKED_ACCOUNT" || rel === "HAS_PUBLIC_PROFILE") return "accounts";
    if (!rel && (etype === "public_profile" || etype === "social_account")) return "accounts";
    if (rel === "HAS_USERNAME" || (rel === "ASSOCIATED_WITH" && dtype === "username")) return "related_usernames";
    if (rel === "ASSOCIATED_WITH" && (dtype === "domain" || dtype === "url")) return "linked_sites";
    if (rel === "HAS_PUBLIC_CONTACT" || rel === "HAS_PUBLIC_CRYPTO_ADDRESS" || rel === "HAS_EMAIL" || rel === "HAS_PHONE") return "contacts";
    if (rel === "RESOLVES_TO" || rel === "HAS_MX_RECORD" || rel === "HAS_NAMESERVER") return "dns";
    if (rel === "BELONGS_TO_ASN" || rel === "HOSTED_BY" || rel === "HOSTS") return "network";
    if (rel === "IDENTITY_CANDIDATE" || etype === "identity_candidate") return "identities";
    if (!rel && text(row.target) && text(row.value).toLowerCase() === text(row.target).toLowerCase()) return "seed";
    return "other";
  }

  function dedupeKey(category, row) {
    const link = linkOf(row);
    if (category === "phone_sites") return category + "|" + siteName(row, "").toLowerCase() + "|" + text(row.value).toLowerCase();
    if (category === "phone_details") return category + "|" + text(row.value) + "|" + text((parseRaw(row.raw_json) || {}).scanner);
    if (category === "archived_urls" || category === "crawled_urls") {
      return category + "|" + (normalizeUrlKey(text(row.dst_value)) || text(row.dst_value).toLowerCase());
    }
    if (category === "accounts" || category === "email_sites") {
      const urlKey = normalizeUrlKey(link);
      if (urlKey) return category + "|" + urlKey;
      return category + "|" + siteName(row, link).toLowerCase() + "|" + accountName(row).toLowerCase();
    }
    if (category === "subdomains") return category + "|" + text(row.dst_value || row.value).toLowerCase().replace(/\.$/, "");
    if (category === "seed") return category + "|" + text(row.value).toLowerCase();
    return [category, text(row.relationship_type).toUpperCase(), text(row.value).toLowerCase(), text(row.dst_value).toLowerCase()].join("|");
  }

  function titleFor(category, row, link) {
    if (category === "accounts") return siteName(row, link) + ": " + accountName(row);
    if (category === "email_sites" || category === "phone_sites") return siteName(row, link);
    if (category === "phone_details") {
      const raw = parseRaw(row.raw_json) || {};
      return text(raw.international ? "+" + text(raw.international).replace(/^\+/, "") : row.value) + (text(row.dst_value) ? " (" + text(row.dst_value) + ")" : "");
    }
    if (category === "archived_urls" || category === "crawled_urls") return text(row.dst_value);
    if (category === "subdomains") return text(row.dst_value || row.value);
    if (category === "related_usernames") return text(row.dst_value) || text(row.value);
    if (category === "linked_sites") return text(row.dst_value) || hostOf(link) || text(row.value);
    if (category === "seed") return text(row.value);
    return text(row.dst_value) || text(row.value) || "Unnamed result";
  }

  function explanationFor(category, row, link) {
    const value = text(row.value);
    const dst = text(row.dst_value);
    const rel = text(row.relationship_type).toUpperCase();
    switch (category) {
      case "accounts":
        return "A public profile named \u201c" + accountName(row) + "\u201d exists on " + siteName(row, link) + ". Check the page to see whether it is the same person.";
      case "email_sites":
        return "This email address appears to be registered on " + siteName(row, link) + ".";
      case "phone_sites":
        return toolName(row.tool) + " reports that " + value + " appears to be registered on " + siteName(row, link) + ". These checks can be wrong; open the site yourself before relying on it.";
      case "phone_details": {
        const raw = parseRaw(row.raw_json) || {};
        const parts = [];
        if (text(raw.country)) parts.push("country " + text(raw.country));
        if (text(raw.local)) parts.push("local format " + text(raw.local));
        if (text(raw.e164)) parts.push("E.164 " + text(raw.e164));
        if (text(raw.carrier)) parts.push("carrier " + text(raw.carrier));
        if (text(raw.line_type)) parts.push("line type " + text(raw.line_type));
        const scanner = text(raw.scanner) || "local";
        const base = "PhoneInfoga's " + scanner + " scanner read: " + (parts.length ? parts.join("; ") : "no details") + ".";
        return scanner === "local" ? base + " This comes from the number's format, not from a lookup of who owns it." : base;
      }
      case "archived_urls": {
        const raw = parseRaw(row.raw_json) || {};
        const when = text(raw.archived_at) ? " (saved " + text(raw.archived_at).slice(0, 10) + ")" : "";
        return "A web archive saved this address" + when + ". The live page may have changed or gone.";
      }
      case "crawled_urls": {
        const raw = parseRaw(row.raw_json) || {};
        const code = raw.status_code ? " and answered with HTTP " + raw.status_code : "";
        return "A shallow crawl of " + value + " reached this page" + code + ".";
      }
      case "ip_addresses":
        return toolName(row.tool) + " saw " + dst + " listed with " + value + " in public sources.";
      case "subdomains":
        return (dst || value) + " is a host name under " + (dst ? value : "the domain") + ".";
      case "related_usernames": {
        const raw = parseRaw(row.raw_json) || {};
        const where = text(raw.sitename) ? " on " + text(raw.sitename) : "";
        return "The name \u201c" + dst + "\u201d was seen together with \u201c" + value + "\u201d" + where + ".";
      }
      case "linked_sites":
        return "The profile " + value + " links to " + (dst || hostOf(link)) + ".";
      case "seed":
        return "This is the target you searched for, as " + toolName(row.tool) + " recorded it. It is not a new discovery.";
      case "identities":
        return "\u201c" + dst + "\u201d may be connected to " + value + ". This is a lead to check, not proof.";
      case "conflicts":
        return "This result conflicts with other evidence about " + value + ". Do not treat it as settled.";
      default: {
        const verb = RELATION_TEXT[rel];
        if (verb && dst) return value + " " + verb + " " + dst + ".";
        return toolName(row.tool) + " recorded \u201c" + (dst || value) + "\u201d.";
      }
    }
  }

  function badgeFor(rows) {
    const list = Array.isArray(rows) ? rows : [rows];
    const confidences = list.map(function (r) { return text(r.confidence).toUpperCase(); });
    const services = list.map(function (r) { return text(r.service_level).toUpperCase(); });
    const allTools = independentTools(list);
    const type = list.length ? rowType(list[0]) : "";
    const weak = {};
    allTools.forEach(function (t) { const e = type ? lowPrecision(t, type) : null; if (e) weak[t] = e; });
    const tools = allTools.filter(function (t) { return !weak[t]; });
    const verdict = verdictOf(list);
    if (verdict === "correct") {
      return { key: "confirmed", label: BADGES.confirmed.label, why: "You marked this correct.", verdict: verdict };
    }
    if (verdict === "wrong") {
      return { key: "conflict", label: "Marked wrong", why: "You marked this wrong. It is kept, but treat it as false.", verdict: verdict };
    }
    if (services.indexOf("CONTRADICTED") !== -1) {
      return { key: "conflict", label: BADGES.conflict.label, why: "The command center marked this as conflicting with other evidence." };
    }
    if (confidences.indexOf("CONFIRMED") !== -1 || services.indexOf("USER_CONFIRMED") !== -1) {
      return { key: "confirmed", label: BADGES.confirmed.label, why: "An investigator confirmed this." };
    }
    if (allTools.length && !tools.length) {
      const e = weak[allTools[0]];
      const pct = Math.round((1 - Number(e.precision)) * 100);
      return { key: "unverified", label: BADGES.unverified.label,
        why: "People marked about " + pct + "% of " + toolName(allTools[0]) + "'s " + type + " results wrong (" +
          formatCount(Number(e.decided_votes) || 0) + " votes), so this stays unverified until checked." };
    }
    if (confidences.indexOf("HIGH") !== -1 || services.indexOf("CORRELATED") !== -1) {
      return { key: "likely", label: BADGES.likely.label, why: "The command center rated this high confidence or corroborated." };
    }
    if (tools.length >= 2) {
      return { key: "likely", label: BADGES.likely.label, why: "Reported separately by " + joinList(tools.map(toolName)) + "." };
    }
    const level = confidences.indexOf("MEDIUM") !== -1 ? "Medium" : (confidences.indexOf("LOW") !== -1 ? "Low" : "not recorded");
    return { key: "unverified", label: BADGES.unverified.label, why: "Reported by one automated tool (confidence: " + level + "). Treat it as a lead." };
  }

  // ---- Learning loop context (set by the page; empty in tests by default) ----
  // learned: "tool|type" -> {precision, decided_votes, status, votes}
  const LEARN = { learned: {}, verdicts: {}, jobType: "" };
  const LOW_PRECISION = 0.5;
  const MIN_VOTES = 5;
  const TARGET_TYPES = { username: 1, email: 1, domain: 1, phone: 1, person: 1, url: 1 };

  function setLearning(tools) {
    LEARN.learned = {};
    (Array.isArray(tools) ? tools : []).forEach(function (t) {
      if (t && t.tool && t.target_type) LEARN.learned[text(t.tool).toLowerCase() + "|" + text(t.target_type).toLowerCase()] = t;
    });
  }
  function setVerdicts(map) { LEARN.verdicts = map && typeof map === "object" ? Object.assign({}, map) : {}; }
  function setVerdict(ids, verdict) {
    (ids || []).forEach(function (id) {
      if (verdict === "clear") delete LEARN.verdicts[String(id)];
      else LEARN.verdicts[String(id)] = verdict;
    });
  }
  function setJobType(type) { LEARN.jobType = TARGET_TYPES[type] ? type : ""; }
  function verdictOf(rows) {
    const seen = {};
    rows.forEach(function (r) { const v = LEARN.verdicts[String(r.id)]; if (v) seen[v] = true; });
    if (seen.wrong && !seen.correct) return "wrong";
    if (seen.correct && !seen.wrong) return "correct";
    if (seen.correct && seen.wrong) return "mixed";
    if (seen.unsure) return "unsure";
    return "";
  }
  function rowType(row) {
    if (LEARN.jobType) return LEARN.jobType;
    const t = text(row.entity_type).toLowerCase();
    return TARGET_TYPES[t] ? t : "";
  }
  // A tool whose findings people often mark wrong on this target type.
  function lowPrecision(tool, type) {
    const e = LEARN.learned[tool + "|" + type];
    if (!e) return null;
    const votes = Number(e.decided_votes) || 0;
    if (e.status === "false_positive_heavy" || (votes >= MIN_VOTES && Number(e.precision) < LOW_PRECISION)) return e;
    return null;
  }

  function independentTools(rows) {
    const seen = {};
    rows.forEach(function (r) {
      const t = text(r.tool).toLowerCase();
      if (t && !DERIVED_TOOLS[t]) seen[t] = true;
    });
    return Object.keys(seen).sort();
  }

  function uniqueInOrder(values) {
    const seen = {};
    return values.filter(function (v) {
      if (!v || seen[v]) return false;
      seen[v] = true;
      return true;
    });
  }

  function uniqueSorted(values) {
    const seen = {};
    values.forEach(function (v) { if (v) seen[v] = true; });
    return Object.keys(seen).sort();
  }

  // Lower-case the first letter for use mid-sentence, but keep acronyms
  // such as "DNS" or "IP" as written.
  function lowerFirst(value) {
    const v = text(value);
    if (/^[A-Z]{2}/.test(v)) return v;
    return v.charAt(0).toLowerCase() + v.slice(1);
  }

  // PhoneInfoga builds Google search links but does not run them.
  function searchLinksOf(rows) {
    const out = [];
    const seen = {};
    rows.forEach(function (r) {
      const raw = parseRaw(r.raw_json) || {};
      (Array.isArray(raw.manual_search_links) ? raw.manual_search_links : []).forEach(function (l) {
        const url = safeUrl(l && l.url);
        if (url && !seen[url]) {
          seen[url] = true;
          out.push({ group: text(l.group) || "Other", url: url });
        }
      });
    });
    return out;
  }

  // Merge rows that describe the same fact. Stored rows are not changed.
  function buildItems(findings, groupBy) {
    const byKey = {};
    const order = [];
    (Array.isArray(findings) ? findings : []).forEach(function (row) {
      if (!row || typeof row !== "object") return;
      const category = categoryOf(row);
      let key = dedupeKey(category, row);
      if (groupBy === "tool") key = text(row.tool).toLowerCase() + "#" + key;
      if (!byKey[key]) {
        byKey[key] = { key: key, category: category, rows: [] };
        order.push(key);
      }
      byKey[key].rows.push(row);
    });
    return order.map(function (key) {
      const entry = byKey[key];
      const rows = entry.rows;
      let link = "";
      rows.some(function (r) { link = linkOf(r); return !!link; });
      const first = rows[0];
      const times = rows.map(function (r) { return text(r.created_at); }).filter(Boolean).sort();
      return {
        key: key,
        category: entry.category,
        title: titleFor(entry.category, first, link),
        explanation: explanationFor(entry.category, first, link),
        link: link,
        tools: uniqueSorted(rows.map(function (r) { return text(r.tool).toLowerCase(); })),
        jobIds: uniqueSorted(rows.map(function (r) { return text(r.job_id); })),
        evidenceIds: uniqueSorted(rows.map(function (r) { return r.evidence_id == null ? "" : String(r.evidence_id); })),
        badge: badgeFor(rows),
        firstSeen: times[0] || "",
        lastSeen: times[times.length - 1] || "",
        copies: rows.length,
        searchLinks: searchLinksOf(rows),
        rows: rows,
      };
    });
  }

  function siteCount(items) {
    const seen = {};
    items.forEach(function (item) {
      const key = siteKeyOf(hostOf(item.link)) || item.title.split(":")[0].toLowerCase();
      seen[key] = true;
    });
    return Object.keys(seen).length;
  }

  function badgeRank(item) { return (BADGES[item.badge.key] || BADGES.unverified).order; }

  function sortItems(items) {
    return items.slice().sort(function (a, b) {
      return badgeRank(a) - badgeRank(b) || b.tools.length - a.tools.length || a.title.localeCompare(b.title);
    });
  }

  function groupByCategory(items) {
    const groups = {};
    items.forEach(function (item) {
      (groups[item.category] = groups[item.category] || []).push(item);
    });
    return Object.keys(groups).sort(function (a, b) {
      return CATEGORIES[a].order - CATEGORIES[b].order;
    }).map(function (cat) {
      const list = sortItems(groups[cat]);
      const info = CATEGORIES[cat];
      return {
        key: cat,
        title: info.title(list.length, cat === "accounts" ? siteCount(list) : list.length),
        blurb: info.blurb,
        count: list.length,
        items: list,
      };
    });
  }

  function groupByTool(items) {
    const groups = {};
    items.forEach(function (item) {
      const tool = item.tools[0] || "unknown";
      (groups[tool] = groups[tool] || []).push(item);
    });
    return Object.keys(groups).sort(function (a, b) {
      return groups[b].length - groups[a].length || a.localeCompare(b);
    }).map(function (tool) {
      const list = sortItems(groups[tool]);
      const role = toolRole(tool);
      return {
        key: "tool-" + tool,
        title: toolName(tool) + " (" + formatCount(list.length) + (list.length === 1 ? " result)" : " results)"),
        blurb: role ? toolName(tool) + " " + role + "." : "",
        count: list.length,
        items: list,
      };
    });
  }

  function groupItems(items, groupBy) {
    return groupBy === "tool" ? groupByTool(items) : groupByCategory(items);
  }

  function plural(n, one, many) {
    return n + " " + (n === 1 ? one : many);
  }

  function formatCount(n) {
    return Number(n || 0).toLocaleString("en-US");
  }

  function joinList(parts) {
    if (parts.length <= 1) return parts.join("");
    return parts.slice(0, -1).join(", ") + " and " + parts[parts.length - 1];
  }

  const TYPE_WORDS = {
    domain: "domain", email: "email address", username: "username", phone: "phone number", person: "person", url: "web address",
  };

  // A plain-English summary built only from what the command center stored.
  function summarize(job, findings) {
    const j = job || {};
    const rows = Array.isArray(findings) ? findings : [];
    const items = buildItems(rows, "type");
    const groups = groupByCategory(items);
    const runs = Array.isArray(j.tool_results) && j.tool_results.length ? j.tool_results : (Array.isArray(j.results) ? j.results : []);
    const ran = uniqueSorted(runs.map(function (r) { return text(r.tool).toLowerCase(); }));
    const requested = uniqueInOrder((Array.isArray(j.requested_tools) ? j.requested_tools : []).map(function (t) { return text(t).toLowerCase(); }));
    const helpers = ran.filter(function (t) { return requested.indexOf(t) === -1; });
    const toolList = requested.length ? requested.concat(helpers) : ran;
    const leadTools = requested.length ? requested : ran;
    const typeWord = TYPE_WORDS[text(j.target_type)] || "target";
    const target = text(j.target) || "this target";
    const parts = groups.filter(function (g) { return g.key !== "seed"; }).map(function (g) { return lowerFirst(g.title); });

    let sentence;
    if (!toolList.length) {
      sentence = "No tools were run for the " + typeWord + " \u201c" + target + "\u201d, so there are no results.";
    } else if (!rows.length) {
      sentence = joinList(leadTools.map(toolName)) + " looked up the " + typeWord + " \u201c" + target + "\u201d and saved no findings.";
    } else {
      sentence = joinList(leadTools.map(toolName)) + " looked up the " + typeWord + " \u201c" + target + "\u201d and saved " +
        formatCount(rows.length) + (rows.length === 1 ? " finding" : " findings") +
        (parts.length ? ": " + joinList(parts) + "." : (rows.length === 1 ? ". It is only the starting target itself." : ". They only record the starting target itself."));
    }

    const notable = [];
    const duplicates = rows.length - items.length;
    if (duplicates > 0) {
      notable.push(formatCount(rows.length) + " saved findings come down to " + formatCount(items.length) + " distinct results. " +
        formatCount(duplicates) + (duplicates === 1 ? " repeat is" : " repeats are") + " hidden here (the same fact reported by more than one tool or run); the stored data is unchanged.");
    }
    const multi = items.filter(function (i) { return independentTools(i.rows).length >= 2; }).length;
    if (multi) notable.push(plural(multi, "result was", "results were") + " reported by two or more separate tools.");
    const confirmed = items.filter(function (i) { return i.badge.key === "confirmed"; }).length;
    if (confirmed) notable.push(plural(confirmed, "result is", "results are") + " confirmed by an investigator.");
    const conflicts = items.filter(function (i) { return i.badge.key === "conflict"; }).length;
    if (conflicts) notable.push(plural(conflicts, "result conflicts", "results conflict") + " with other evidence.");
    const subs = groups.filter(function (g) { return g.key === "subdomains"; })[0];
    if (subs && subs.count >= 500) {
      notable.push("The subdomain list is very long (" + formatCount(subs.count) + "). Lists this size often include generated or stale names, so check any single entry before relying on it.");
    }
    const failedRuns = runs.filter(function (r) { return ["failed", "timeout"].indexOf(text(r.status)) !== -1; });
    failedRuns.forEach(function (r) {
      notable.push(toolName(r.tool) + (text(r.status) === "timeout" ? " ran out of time" : " failed") + (text(r.error) ? ": " + text(r.error) : "."));
    });
    runs.filter(function (r) { return text(r.status) === "skipped"; }).forEach(function (r) {
      notable.push(toolName(r.tool) + " was skipped" + (text(r.error) ? ": " + text(r.error).replace(/^Skipped:\s*/i, "") : "."));
    });
    const missing = requested.filter(function (t) { return ran.indexOf(t) === -1; });
    if (missing.length && ran.length) notable.push(joinList(missing.map(toolName)) + (missing.length === 1 ? " was" : " were") + " requested but never reported a result.");
    const status = text(j.status);
    const errors = Array.isArray(j.errors) ? j.errors.map(text) : [];
    if (status === "failed" && errors.some(function (e) { return /^interrupted/i.test(e); })) {
      notable.push("This investigation was interrupted before it finished, so the results may be incomplete.");
    } else if (status === "running" || status === "queued") {
      notable.push("This investigation is still running. More results may appear.");
    }
    return {
      sentence: sentence,
      tools: toolList.map(function (t) {
        const run = runs.filter(function (r) { return text(r.tool).toLowerCase() === t; });
        const counts = run.map(function (r) { return typeof r.finding_count === "number" ? r.finding_count : null; }).filter(function (n) { return n != null; });
        const saved = rows.filter(function (r) { return text(r.tool).toLowerCase() === t; }).length;
        const status = run.length ? text(run[run.length - 1].status) : (saved ? "no_final_status" : "not_started");
        return {
          tool: t,
          name: toolName(t),
          role: toolRole(t),
          runs: run.length,
          status: status,
          statusLabel: status === "no_final_status" ? "No final status recorded" : "",
          findings: counts.length ? counts.reduce(function (a, b) { return a + b; }, 0) : (saved || null),
        };
      }),
      categories: groups.map(function (g) { return { key: g.key, title: g.title, count: g.count }; }),
      notable: notable,
      totalFindings: rows.length,
      distinct: items.length,
    };
  }

  function emptyMessage(job, error) {
    const j = job || {};
    if (error) return "The findings could not be loaded (" + text(error) + "). Nothing was changed. Reload the page to try again.";
    const status = text(j.status);
    const errors = Array.isArray(j.errors) ? j.errors.map(text).filter(Boolean) : [];
    const requested = Array.isArray(j.requested_tools) ? j.requested_tools : [];
    const runs = Array.isArray(j.tool_results) && j.tool_results.length ? j.tool_results : (Array.isArray(j.results) ? j.results : []);
    if (status === "running" || status === "queued") return "This investigation is still running. Results appear here as tools finish.";
    if (!requested.length && !runs.length) return "No tools were selected for this investigation, so there are no results to show.";
    if (status === "failed") {
      return "No findings were saved before this investigation stopped." + (errors.length ? " Reason recorded: " + errors[errors.length - 1] : "");
    }
    if (status === "cancelled") return "This investigation was cancelled before any findings were saved.";
    return "The tools finished but saved no findings. Usually that means nothing public matched this target, or a tool could not reach its sources. Tool status below shows each tool's result.";
  }

  // Evidence records: one readable line per tool output record.
  function describeEvidence(row) {
    const r = row || {};
    const parsed = parseRaw(r.parsed_json) || {};
    const tool = toolName(r.tool);
    const target = text(r.target) || "the target";
    const link = safeUrl(r.source_url) || safeUrl(parsed.url);
    let what;
    if (text(r.error)) what = tool + " reported a problem: " + text(r.error);
    else if (parsed.sitename || parsed.site) what = tool + " found a page for \u201c" + target + "\u201d on " + text(parsed.sitename || parsed.site) + ".";
    else if (parsed.subdomain) what = tool + " listed the subdomain " + text(parsed.subdomain) + ".";
    else if (parsed.type && parsed.data) what = tool + " recorded a " + text(parsed.type).toLowerCase() + ": " + text(parsed.data).split("\n")[0].replace(/<\/?SFURL>/gi, "") + ".";
    else if (link) what = tool + " recorded the page " + link + ".";
    else if (text(r.stdout_excerpt)) what = tool + " saved " + formatCount(text(r.stdout_excerpt).length) + " characters of output for \u201c" + target + "\u201d.";
    else what = tool + " saved a record for \u201c" + target + "\u201d.";
    return {
      title: tool + " output for \u201c" + target + "\u201d",
      what: what,
      link: link,
      badge: badgeFor([r]),
      exitText: r.exit_code == null ? "" : (Number(r.exit_code) === 0 ? "finished normally" : "exited with code " + r.exit_code),
    };
  }

  function evidenceKey(row) {
    const r = row || {};
    return [text(r.tool).toLowerCase(), text(r.target).toLowerCase(), text(r.parsed_json), text(r.stdout_excerpt), text(r.stderr_excerpt), text(r.error)].join("|");
  }

  function dedupeEvidence(rows) {
    const byKey = {};
    const out = [];
    (Array.isArray(rows) ? rows : []).forEach(function (row) {
      const key = evidenceKey(row);
      if (byKey[key]) { byKey[key].copies.push(row.id); return; }
      byKey[key] = { row: row, copies: [row.id] };
      out.push(byKey[key]);
    });
    return out;
  }

  function prettyRaw(value) {
    const parsed = parseRaw(value);
    if (parsed) {
      try { return JSON.stringify(parsed, null, 2); } catch (err) { return text(value); }
    }
    return text(value);
  }

  function readableDate(value, now) {
    if (!value) return "Not recorded";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    const base = date.toLocaleString("en-US", { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
    const age = relativeAge(value, now);
    return age ? base + " (" + age + ")" : base;
  }

  function relativeAge(value, now) {
    const then = Date.parse(value || "");
    if (!then) return "";
    const ref = now == null ? Date.now() : (typeof now === "number" ? now : Date.parse(now));
    const minutes = Math.floor((ref - then) / 60000);
    if (minutes < 0) return "";
    if (minutes < 1) return "just now";
    if (minutes < 60) return plural(minutes, "minute", "minutes") + " ago";
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return plural(hours, "hour", "hours") + " ago";
    const days = Math.floor(hours / 24);
    if (days < 60) return plural(days, "day", "days") + " ago";
    const months = Math.floor(days / 30);
    if (months < 24) return plural(months, "month", "months") + " ago";
    return plural(Math.floor(days / 365), "year", "years") + " ago";
  }

  const api = {
    TOOL_NAMES: TOOL_NAMES,
    CATEGORIES: CATEGORIES,
    toolName: toolName,
    toolRole: toolRole,
    parseRaw: parseRaw,
    safeUrl: safeUrl,
    linkOf: linkOf,
    siteName: siteName,
    accountName: accountName,
    categoryOf: categoryOf,
    badgeFor: badgeFor,
    setLearning: setLearning,
    setVerdicts: setVerdicts,
    setVerdict: setVerdict,
    setJobType: setJobType,
    verdictOf: verdictOf,
    buildItems: buildItems,
    groupItems: groupItems,
    summarize: summarize,
    emptyMessage: emptyMessage,
    describeEvidence: describeEvidence,
    dedupeEvidence: dedupeEvidence,
    prettyRaw: prettyRaw,
    readableDate: readableDate,
    relativeAge: relativeAge,
  };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.OsintResults = api;
})(typeof window !== "undefined" ? window : this);
