"use strict";
// Checks the plain-language formatting in static/results.js.
const assert = require("assert");
const path = require("path");
const R = require(path.join(__dirname, "..", "static", "results.js"));

function f(extra) {
  return Object.assign({ id: 1, job_id: "job1", target: "exampleuser", entity_type: "username", value: "exampleuser",
    confidence: "MEDIUM", service_level: "OBSERVED", created_at: "2026-10-08T19:07:02Z" }, extra);
}

const sherlock = f({ id: 1, tool: "sherlock", relationship_type: "LINKED_ACCOUNT", dst_type: "social_account", dst_value: "github:exampleuser",
  raw_json: JSON.stringify({ site: "GitHub", url: "https://github.com/exampleuser" }), evidence_id: 10 });
const maigret = f({ id: 2, tool: "maigret", relationship_type: "LINKED_ACCOUNT", dst_type: "social_account", dst_value: "github:exampleuser",
  source_url: "https://www.github.com/exampleuser/", raw_json: JSON.stringify({ sitename: "GitHub", url: "https://www.github.com/exampleuser/", username: "exampleuser" }), evidence_id: 11 });
const identity = f({ id: 3, tool: "identity", relationship_type: "HAS_PUBLIC_PROFILE", dst_type: "public_profile", dst_value: "https://github.com/exampleuser",
  source_url: "https://github.com/exampleuser", raw_json: JSON.stringify({ url: "https://github.com/exampleuser", site: "GitHub" }) });
const sfOnly = f({ id: 4, tool: "spiderfoot", relationship_type: "LINKED_ACCOUNT", dst_type: "social_account", dst_value: "youtube user2:exampleuser",
  raw_json: JSON.stringify({ type: "Account on External Site", data: "YouTube User2 (Category: video)\n<SFURL>https://www.youtube.com/@exampleuser</SFURL>" }) });
const seed = f({ id: 5, tool: "spiderfoot", raw_json: JSON.stringify({ type: "Username", data: "exampleuser" }) });
const sub1 = f({ id: 6, tool: "subfinder", target: "example.com", entity_type: "domain", value: "example.com", relationship_type: "HAS_SUBDOMAIN", dst_type: "subdomain", dst_value: "www.example.com" });
const sub2 = Object.assign({}, sub1, { id: 7, dst_value: "WWW.example.com." });
const evil = f({ id: 8, tool: "sherlock", relationship_type: "LINKED_ACCOUNT", dst_type: "social_account", dst_value: "x:exampleuser",
  source_url: "javascript:alert(1)", raw_json: JSON.stringify({ site: "X", url: "javascript:alert(1)" }) });
const holehe = f({ id: 9, tool: "holehe", target: "user@example.com", entity_type: "email", value: "user@example.com", relationship_type: "LINKED_ACCOUNT",
  dst_type: "social_account", dst_value: "spotify:user@example.com", raw_json: JSON.stringify({ site: "Spotify" }) });
const high = Object.assign({}, sfOnly, { id: 12, dst_value: "gitlab:exampleuser", raw_json: JSON.stringify({ site: "GitLab", url: "https://gitlab.com/exampleuser" }), confidence: "HIGH" });
const confirmed = Object.assign({}, high, { id: 13, dst_value: "codeberg:exampleuser", raw_json: JSON.stringify({ site: "Codeberg", url: "https://codeberg.org/exampleuser" }), confidence: "MEDIUM", service_level: "USER_CONFIRMED" });
const conflict = Object.assign({}, high, { id: 14, dst_value: "bitbucket:exampleuser", raw_json: JSON.stringify({ site: "Bitbucket", url: "https://bitbucket.org/exampleuser" }), confidence: "MEDIUM", service_level: "CONTRADICTED" });

// Categories and friendly labels
assert.strictEqual(R.categoryOf(sherlock), "accounts");
assert.strictEqual(R.categoryOf(identity), "accounts");
assert.strictEqual(R.categoryOf(sub1), "subdomains");
assert.strictEqual(R.categoryOf(seed), "seed");
assert.strictEqual(R.categoryOf(holehe), "email_sites");
assert.strictEqual(R.categoryOf(conflict), "conflicts");

// Links: SpiderFoot SFURL is extracted; unsafe schemes are dropped
assert.strictEqual(R.linkOf(sfOnly), "https://www.youtube.com/@exampleuser");
assert.strictEqual(R.linkOf(evil), "");
assert.strictEqual(R.safeUrl("javascript:alert(1)"), "");
assert.strictEqual(R.siteName(sfOnly, R.linkOf(sfOnly)), "YouTube");

// Duplicates merge for display only; input rows are untouched
const input = [sherlock, maigret, identity, sfOnly, seed, sub1, sub2];
const snapshot = JSON.stringify(input);
const items = R.buildItems(input, "type");
assert.strictEqual(JSON.stringify(input), snapshot, "buildItems must not modify stored rows");
const github = items.filter(function (i) { return i.title === "GitHub: exampleuser"; });
assert.strictEqual(github.length, 1, "same GitHub account from three tools shows once");
assert.deepStrictEqual(github[0].tools, ["identity", "maigret", "sherlock"]);
assert.strictEqual(github[0].copies, 3);
assert.strictEqual(items.filter(function (i) { return i.category === "subdomains"; }).length, 1, "subdomains compare case- and dot-insensitively");
assert.ok(github[0].explanation.indexOf("GitHub") !== -1);
assert.strictEqual(github[0].link, "https://github.com/exampleuser");

// Badges come from stored labels or from independent tools; derived tools do not count
assert.strictEqual(github[0].badge.key, "likely");
assert.ok(/Maigret and Sherlock/.test(github[0].badge.why));
assert.strictEqual(R.badgeFor([sherlock, identity]).key, "unverified", "identity re-reads sherlock, so it is not a second source");
assert.strictEqual(R.badgeFor([sfOnly]).key, "unverified");
assert.strictEqual(R.badgeFor([high]).key, "likely");
assert.strictEqual(R.badgeFor([confirmed]).key, "confirmed");
assert.strictEqual(R.badgeFor([conflict]).key, "conflict");

// Grouping labels
const groups = R.groupItems(items, "type");
assert.strictEqual(groups[0].key, "accounts");
assert.strictEqual(groups[0].title, "Accounts found on 2 sites");
assert.ok(groups.some(function (g) { return g.title === "Subdomains (1)"; }));
const holeheGroups = R.groupItems(R.buildItems([holehe], "type"), "type");
assert.strictEqual(holeheGroups[0].title, "Sites this email is registered on (1)");
const byTool = R.groupItems(R.buildItems(input, "tool"), "tool");
assert.ok(byTool.some(function (g) { return /^Sherlock \(\d+ results?\)$/.test(g.title); }));

// Summary uses only stored data
const job = { id: "job1", target: "exampleuser", target_type: "username", status: "completed", requested_tools: ["sherlock", "maigret"],
  tool_results: [{ tool: "sherlock", status: "completed", finding_count: 1 }, { tool: "maigret", status: "failed", error: "site timeout", finding_count: 0 }, { tool: "identity", status: "completed", finding_count: 1 }],
  errors: [] };
const s = R.summarize(job, input);
assert.ok(/^Sherlock and Maigret looked up the username \u201cexampleuser\u201d and saved 7 findings/.test(s.sentence), s.sentence);
assert.ok(s.notable.some(function (n) { return /7 saved findings come down to 4 distinct results/.test(n); }), s.notable.join(" | "));
assert.ok(s.notable.some(function (n) { return n === "Maigret failed: site timeout"; }));
assert.ok(s.tools.some(function (t) { return t.name === "Identity organizer"; }));
const interrupted = R.summarize({ target: "example.com", target_type: "domain", status: "failed", requested_tools: ["subfinder"], errors: ["interrupted: no live worker"] }, [sub1]);
assert.ok(interrupted.notable.some(function (n) { return /interrupted/.test(n); }));
assert.strictEqual(interrupted.tools[0].statusLabel, "No final status recorded");
assert.strictEqual(interrupted.tools[0].findings, 1);
const many = R.groupItems(R.buildItems(Array.from({ length: 1200 }, function (_, i) { return Object.assign({}, sub1, { id: 100 + i, dst_value: "h" + i + ".example.com" }); }), "type"), "type");
assert.strictEqual(many[0].title, "Subdomains (1,200)");
assert.strictEqual(interrupted.sentence, "Subfinder looked up the domain \u201cexample.com\u201d and saved 1 finding: subdomains (1).");

// Empty and error states explain what happened
assert.ok(/saved no findings/.test(R.emptyMessage({ status: "completed", requested_tools: ["subfinder"], tool_results: [{ tool: "subfinder", status: "completed" }] })));
assert.ok(/still running/.test(R.emptyMessage({ status: "running", requested_tools: ["subfinder"] })));
assert.ok(/No tools were selected/.test(R.emptyMessage({ status: "completed", requested_tools: [] })));
assert.ok(/Reason recorded: interrupted/.test(R.emptyMessage({ status: "failed", requested_tools: ["amass"], errors: ["interrupted: x"] })));
assert.ok(/could not be loaded \(boom\)/.test(R.emptyMessage(null, "boom")));

// Readable dates
assert.strictEqual(R.relativeAge("2026-10-08T19:07:02Z", "2026-10-09T20:07:02Z"), "1 day ago");
assert.strictEqual(R.relativeAge("2026-10-08T19:07:02Z", "2026-10-08T19:37:02Z"), "30 minutes ago");
assert.ok(/2026/.test(R.readableDate("2026-10-08T19:07:02Z")));
assert.strictEqual(R.readableDate(""), "Not recorded");
assert.strictEqual(R.readableDate("not a date"), "not a date");

// Evidence: readable line, duplicates collapsed, bad JSON tolerated
const ev = { id: 20, tool: "sherlock", target: "exampleuser", parsed_json: JSON.stringify({ site: "GitHub", url: "https://github.com/exampleuser" }), confidence: "MEDIUM", service_level: "OBSERVED", exit_code: 0 };
const d = R.describeEvidence(ev);
assert.strictEqual(d.what, "Sherlock found a page for \u201cexampleuser\u201d on GitHub.");
assert.strictEqual(d.link, "https://github.com/exampleuser");
assert.strictEqual(d.exitText, "finished normally");
assert.strictEqual(R.dedupeEvidence([ev, Object.assign({}, ev, { id: 21 })]).length, 1);
assert.strictEqual(R.describeEvidence({ tool: "amass", target: "example.com", parsed_json: "{not json" }).what, "Amass saved a record for \u201cexample.com\u201d.");
assert.strictEqual(R.prettyRaw("{\"a\":1}"), "{\n  \"a\": 1\n}");
assert.strictEqual(R.prettyRaw("plain text"), "plain text");

// New tools (2026-11-17): phone, domain, username/email and URL adapters
const PHONE = "+12025550100";
const ign = f({ id: 30, tool: "ignorant", target: PHONE, entity_type: "phone", value: PHONE, relationship_type: "ASSOCIATED_WITH",
  dst_type: "social_account", dst_value: "instagram.com:" + PHONE, confidence: "LOW", raw_json: JSON.stringify({ site: "instagram.com", phone: PHONE, status: "used" }) });
const pinfo = f({ id: 31, tool: "phoneinfoga", target: PHONE, entity_type: "phone", value: PHONE, relationship_type: "ASSOCIATED_WITH",
  dst_type: "location", dst_value: "US", raw_json: JSON.stringify({ scanner: "local", country: "US", local: "(202) 555-0100", e164: PHONE, international: "12025550100",
    manual_search_links: [{ group: "Social media", url: "https://www.google.com/search?q=site%3Afacebook.com+intext%3A%2212025550100%22" },
      { group: "Social media", url: "javascript:alert(1)" }] }) });
const ph = R.buildItems([ign, pinfo]);
const phSite = ph.filter(function (i) { return i.category === "phone_sites"; })[0];
assert.ok(phSite, "ignorant -> phone_sites");
assert.strictEqual(phSite.title, "instagram.com");
assert.ok(/appears to be registered on instagram\.com/.test(phSite.explanation));
assert.strictEqual(phSite.badge.key, "unverified");
const phDet = ph.filter(function (i) { return i.category === "phone_details"; })[0];
assert.strictEqual(phDet.title, "+12025550100 (US)");
assert.ok(/country US; local format \(202\) 555-0100/.test(phDet.explanation));
assert.ok(/not from a lookup/.test(phDet.explanation));
assert.strictEqual(phDet.searchLinks.length, 1, "unsafe search links dropped");
const phSum = R.summarize({ target: PHONE, target_type: "phone", requested_tools: ["phoneinfoga", "ignorant"], status: "completed",
  tool_results: [{ tool: "phoneinfoga", status: "completed", finding_count: 1 }, { tool: "ignorant", status: "completed", finding_count: 1 }] }, [ign, pinfo]);
assert.deepStrictEqual(phSum.tools.map(function (t) { return t.name; }), ["PhoneInfoga", "Ignorant"]);
assert.ok(phSum.categories.some(function (c) { return c.title === "Sites this phone number may be registered on (1)"; }));

const th1 = f({ id: 32, tool: "theharvester", target: "example.com", entity_type: "domain", value: "example.com", relationship_type: "HAS_SUBDOMAIN", dst_type: "subdomain", dst_value: "www.example.com" });
const thIp = f({ id: 33, tool: "theharvester", target: "example.com", entity_type: "domain", value: "example.com", relationship_type: "ASSOCIATED_WITH", dst_type: "ip", dst_value: "93.184.215.14", confidence: "LOW" });
const dom = R.buildItems([sub1, th1, thIp]);
const www = dom.filter(function (i) { return i.category === "subdomains"; });
assert.strictEqual(www.length, 1, "theHarvester + Subfinder subdomain merged");
assert.strictEqual(www[0].badge.key, "likely");
assert.ok(/theHarvester/.test(www[0].badge.why));
const ipItem = dom.filter(function (i) { return i.category === "ip_addresses"; })[0];
assert.ok(/theHarvester saw 93\.184\.215\.14 listed with example\.com/.test(ipItem.explanation));

const bb = f({ id: 34, tool: "blackbird", relationship_type: "LINKED_ACCOUNT", dst_type: "social_account", dst_value: "github:exampleuser",
  source_url: "https://github.com/exampleuser", raw_json: JSON.stringify({ sitename: "GitHub", url: "https://github.com/exampleuser", username: "exampleuser" }) });
const sa = f({ id: 35, tool: "social_analyzer", relationship_type: "LINKED_ACCOUNT", dst_type: "social_account", dst_value: "facebook:exampleuser", confidence: "LOW",
  raw_json: JSON.stringify({ sitename: "Facebook", url: "https://facebook.com/exampleuser", username: "exampleuser" }) });
const acc = R.buildItems([sherlock, bb, sa]);
const gh = acc.filter(function (i) { return i.title === "GitHub: exampleuser"; })[0];
assert.deepStrictEqual(gh.tools, ["blackbird", "sherlock"]);
assert.strictEqual(gh.badge.key, "likely");
assert.ok(acc.some(function (i) { return i.title === "Facebook: exampleuser" && i.badge.key === "unverified"; }));
const bbEmail = f({ id: 36, tool: "blackbird", target: "user@example.com", entity_type: "email", value: "user@example.com", relationship_type: "ASSOCIATED_WITH",
  dst_type: "social_account", dst_value: "spotify:user@example.com", confidence: "LOW", raw_json: JSON.stringify({ site: "Spotify", check_url: "https://api.example/x" }) });
const em = R.buildItems([holehe, bbEmail]);
assert.strictEqual(em.length, 1, "Holehe + Blackbird email site merged");
assert.strictEqual(em[0].category, "email_sites");
assert.strictEqual(em[0].link, "", "API check URL is not shown as a page link");

const wb = f({ id: 37, tool: "waybackurls", target: "https://example.com", entity_type: "url", value: "https://example.com", relationship_type: "ASSOCIATED_WITH",
  dst_type: "url", dst_value: "http://example.com/about", raw_json: JSON.stringify({ url: "http://example.com/about", archived_at: "2010-05-02T08:30:00Z" }) });
const wb2 = Object.assign({}, wb, { id: 38, dst_value: "http://www.example.com/about/", raw_json: JSON.stringify({ url: "http://www.example.com/about/" }) });
const kt = f({ id: 39, tool: "katana", target: "https://example.com", entity_type: "url", value: "https://example.com", relationship_type: "ASSOCIATED_WITH",
  dst_type: "url", dst_value: "https://example.com/s.js", raw_json: JSON.stringify({ url: "https://example.com/s.js", status_code: 200 }) });
const urls = R.buildItems([wb, wb2, kt]);
const arch = urls.filter(function (i) { return i.category === "archived_urls"; });
assert.strictEqual(arch.length, 1, "archived URL duplicates collapse");
assert.ok(/saved 2010-05-02/.test(arch[0].explanation));
assert.strictEqual(arch[0].link, "http://example.com/about");
const crawl = urls.filter(function (i) { return i.category === "crawled_urls"; })[0];
assert.ok(/answered with HTTP 200/.test(crawl.explanation));
const skipSum = R.summarize({ target: "https://example.com", target_type: "url", requested_tools: ["waybackurls", "katana"], status: "completed",
  tool_results: [{ tool: "waybackurls", status: "completed", finding_count: 1 },
    { tool: "katana", status: "skipped", finding_count: 0, error: "Skipped: katana contacts the live site, so it only runs in Authorized Active mode. This job is passive." }] }, [wb]);
assert.ok(skipSum.notable.some(function (n) { return /^Katana was skipped: katana contacts the live site/.test(n); }));
assert.strictEqual(R.toolName("social_analyzer"), "Social Analyzer");
const dnsRow = f({ id: 40, tool: "dns", target: "example.com", entity_type: "domain", value: "example.com", relationship_type: "RESOLVES_TO", dst_type: "ip", dst_value: "93.184.215.14" });
const dnsSum = R.summarize({ target: "example.com", target_type: "domain", requested_tools: ["theharvester"], status: "completed",
  tool_results: [{ tool: "theharvester", status: "completed", finding_count: 1 }] }, [th1, thIp, dnsRow]);
assert.ok(/DNS records \(1\)/.test(dnsSum.headline || JSON.stringify(dnsSum)), "acronym kept upper-case");
assert.ok(!/dNS|iP addresses/.test(JSON.stringify(dnsSum)));
assert.ok(R.toolRole("katana").indexOf("active mode only") !== -1);

// Learning loop: verdicts and learned precision change badges (2026-11-17)
R.setLearning([{ tool: "social_analyzer", target_type: "username", precision: 0.3, decided_votes: 12, status: "ok" },
  { tool: "sherlock", target_type: "username", precision: 0.45, decided_votes: 3, status: "ok" }]);
R.setJobType("username");
const saItem = R.buildItems([sa])[0];
assert.strictEqual(saItem.badge.key, "unverified");
assert.ok(/People marked about 70% of Social Analyzer's username results wrong \(12 votes\)/.test(saItem.badge.why), saItem.badge.why);
const saHigh = Object.assign({}, sa, { id: 41, confidence: "HIGH" });
assert.strictEqual(R.buildItems([saHigh])[0].badge.key, "unverified", "high-FP tool cannot reach Likely");
const fbPair = R.buildItems([sa, Object.assign({}, bb, { dst_value: "facebook:exampleuser", raw_json: JSON.stringify({ sitename: "Facebook", url: "https://facebook.com/exampleuser", username: "exampleuser" }) })]);
const fbItem = fbPair.filter(function (i) { return i.title === "Facebook: exampleuser"; })[0];
assert.strictEqual(fbItem.badge.key, "unverified", "a weak tool does not count as a second source");
assert.strictEqual(R.buildItems([sherlock])[0].badge.key, "unverified", "3 votes are too few to judge");
R.setVerdicts({ "35": "correct" });
assert.strictEqual(R.buildItems([sa])[0].badge.key, "confirmed");
R.setVerdict([35], "wrong");
const wrongItem = R.buildItems([sa])[0];
assert.strictEqual(wrongItem.badge.key, "conflict");
assert.strictEqual(wrongItem.badge.label, "Marked wrong");
R.setVerdict([35], "clear");
assert.strictEqual(R.verdictOf([sa]), "");
R.setLearning([{ tool: "social_analyzer", target_type: "username", precision: 0.6, decided_votes: 2, status: "false_positive_heavy" }]);
assert.strictEqual(R.buildItems([saHigh])[0].badge.key, "unverified");
R.setLearning([]); R.setVerdicts({}); R.setJobType("");
assert.strictEqual(R.buildItems([saHigh])[0].badge.key, "likely", "no learning -> original rule");

console.log("results-format-ok");
