/* Smart search target detection, browser copy.
 * Mirrors backend/smart/detect.py so the type chip appears while typing;
 * the backend /detect answer is authoritative and supplies the tool plan.
 * tests/fixtures/detect_cases.json keeps the two in step.
 * No DOM, no network: exports OsintDetect.detect(text). */
(function (root) {
  "use strict";
  var COMMON_TLDS = {};
  ("com net org edu gov mil int info biz io ai app dev xyz online site tech store shop blog " +
   "news media cloud live pro name mobi me tv co us uk ca de fr nl eu au jp cn ru br in es it " +
   "se no fi pl ch at be dk nz agency company digital email world space network systems " +
   "solutions services group global page link club top art design studio games health law")
    .split(" ").forEach(function (t) { COMMON_TLDS[t] = true; });
  var US_STATES = {};
  ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ " +
   "NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC")
    .split(" ").forEach(function (t) { US_STATES[t] = true; });

  var EMAIL_RE = /^[A-Za-z0-9._%+'-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,63}$/;
  var EMAIL_FIND = /[A-Za-z0-9._%+'-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,63}/;
  var EMAIL_FIND_G = /[A-Za-z0-9._%+'-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,63}/g;
  var URL_RE = /^https?:\/\/[^\s\/?#]+[^\s]*$/i;
  var URL_FIND = /https?:\/\/[^\s<>"']+/i;
  var URL_FIND_G = /https?:\/\/[^\s<>"']+/gi;
  var DOMAIN_RE = /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/;
  var HANDLE_RE = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,38}[A-Za-z0-9_]$/;
  var PHONE_CHARS = /^\+?[\d\s().\-]+$/;
  var PHONE_FIND = /(?:\+\d[\d\s().\-]{6,18}\d|\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4})/;
  var NAME_WORD = /^\p{L}(?:\p{L}|['\u2019.\-])*$/u;

  function digits(s) { return s.replace(/\D/g, ""); }

  function phone(value) {
    var v = value.trim();
    if (!v || !PHONE_CHARS.test(v)) return null;
    var d = digits(v);
    if (v.charAt(0) === "+") return d.length >= 8 && d.length <= 15 ? "+" + d : null;
    if (d.length === 10 && "01".indexOf(d.charAt(0)) === -1) return "+1" + d;
    if (d.length === 11 && d.charAt(0) === "1" && "01".indexOf(d.charAt(1)) === -1) return "+" + d;
    return null;
  }

  function domain(value) {
    var v = value.trim().toLowerCase().replace(/\.$/, "");
    if (/^www\./.test(v) && v.split(".").length === 2) return null;
    if (!DOMAIN_RE.test(v) || v.length > 253) return null;
    return v;
  }

  function tldKnown(d) {
    var tld = d.split(".").pop();
    return tld.length === 2 || !!COMMON_TLDS[tld];
  }

  function urlHost(url) {
    var m = /^https?:\/\/([^\/?#:@\s]+)/i.exec(url);
    return m ? domain(m[1]) : null;
  }

  function cand(type, value, score, reason, extra) {
    var c = { type: type, value: value, score: Math.round(score * 100) / 100, reason: reason };
    if (extra) Object.keys(extra).forEach(function (k) { c[k] = extra[k]; });
    return c;
  }

  function person(text) {
    var raw = text.trim().replace(/\s+/g, " ");
    var namePart = raw, location = "";
    if (raw.indexOf(",") !== -1) {
      var i = raw.indexOf(",");
      namePart = raw.slice(0, i).trim();
      location = raw.slice(i + 1).trim().replace(/^,+|,+$/g, "").trim();
      var locWords = location.split(/[\s,]+/).filter(Boolean);
      if (!locWords.length || !locWords.every(function (w) { return NAME_WORD.test(w); })) return null;
    }
    var words = namePart.split(" ");
    if (!location && words.length >= 3 && US_STATES[words[words.length - 1]]) {
      location = words.slice(-2).join(" ");
      words = words.slice(0, -2);
    }
    if (words.length < 2 || words.length > 5) return null;
    if (!words.every(function (w) { return NAME_WORD.test(w); })) return null;
    if (words.filter(function (w) { return w.replace(/^\.+|\.+$/g, "").length >= 2; }).length < 2) return null;
    return { name: words.join(" "), location: location };
  }

  function detect(text) {
    var raw = String(text == null ? "" : text).trim();
    var out = { input: raw, type: null, value: "", reason: "", alternatives: [],
      also_username: null, location: "", host: null };
    if (!raw) { out.reason = "Type something to search."; return out; }
    if (raw.length > 300) { out.reason = "That is too long to be a single target."; return out; }
    var cands = [];
    var s = /^mailto:/i.test(raw) ? raw.slice(7) : raw;
    var single = !/\s/.test(s);
    var slashHost = single && s.indexOf("/") !== -1 ? domain(s.split("/")[0]) : null;

    if (URL_RE.test(s) && urlHost(s)) {
      var host = urlHost(s);
      cands.push(cand("url", s, 0.98, "starts with http:// or https://", { host: host }));
      cands.push(cand("domain", host, 0.5, "the web address's host"));
    } else if (EMAIL_RE.test(s)) {
      var email = s.toLowerCase();
      cands.push(cand("email", email, 0.99, "has a name, an @ and a domain"));
      cands.push(cand("domain", email.split("@")[1], 0.3, "the email's domain"));
      cands.push(cand("username", email.split("@")[0], 0.25, "the part before the @"));
    } else if (phone(s)) {
      var plus = s.charAt(0) === "+";
      cands.push(cand("phone", phone(s), plus || !single ? 0.97 : 0.9,
        plus ? "starts with + and a country code" : "looks like a US phone number"));
      if (single && !plus) cands.push(cand("username", s, 0.2, "a run of digits can also be a username"));
    } else if (single && s.charAt(0) === "@" && HANDLE_RE.test(s.slice(1) || "-")) {
      cands.push(cand("username", s.slice(1), 0.97, "starts with @, like a social handle"));
    } else if (single && slashHost) {
      cands.push(cand("url", "https://" + s, 0.85, "a domain followed by a path", { host: slashHost }));
      cands.push(cand("domain", slashHost, 0.6, "the web address's host"));
    } else if (single && domain(s)) {
      var dom = domain(s);
      if (tldKnown(dom)) {
        cands.push(cand("domain", dom, 0.95, "has a dot and a known ending (." + dom.split(".").pop() + ")"));
        if (dom.split(".").length === 2 && HANDLE_RE.test(s)) cands.push(cand("username", s, 0.2, "some usernames contain a dot"));
      } else {
        cands.push(cand("username", s, 0.7, "a single word with a dot but no common web ending"));
        cands.push(cand("domain", dom, 0.45, "it could be a domain with an unusual ending"));
      }
    } else if (single && HANDLE_RE.test(s)) {
      cands.push(cand("username", s, /^\d+$/.test(s) ? 0.6 : 0.9, "a single word with no spaces"));
    } else {
      var rest = s.replace(EMAIL_FIND_G, " ").replace(URL_FIND_G, " ");
      rest = rest.replace(/\s+/g, " ").replace(/^[ ,;]+|[ ,;]+$/g, "");
      var whole = person(s);
      var p = whole || (rest !== s ? person(rest) : null);
      if (p) {
        var reason = "two or more words made of letters";
        if (p.location) reason += ", followed by a place";
        var mixed = rest !== s && !whole;
        cands.push(cand("person", p.name, mixed ? 0.75 : 0.9, reason, { location: p.location }));
        out.location = p.location;
        var words = p.name.split(" ");
        if (words.length <= 3 && /^[A-Za-z .'-]+$/.test(p.name)) {
          var joined = words.join("").replace(/[^A-Za-z0-9_]/g, "").toLowerCase();
          if (joined.length >= 3 && joined.length <= 30) out.also_username = joined;
        }
      }
      var em = EMAIL_FIND.exec(s);
      if (em) cands.push(cand("email", em[0].toLowerCase(), p ? 0.85 : 0.95, "contains an email address"));
      var ur = URL_FIND.exec(s);
      if (ur && urlHost(ur[0])) cands.push(cand("url", ur[0], 0.85, "contains a web address", { host: urlHost(ur[0]) }));
      var ph = PHONE_FIND.exec(s);
      if (ph && phone(ph[0])) cands.push(cand("phone", phone(ph[0]), 0.85, "contains a phone number"));
      if (!cands.length) {
        var ws = s.split(/\s+/).filter(Boolean);
        if (ws.length && ws.length <= 5 && ws.every(function (w) { return HANDLE_RE.test(w); })) {
          cands.push(cand("person", ws.join(" "), 0.35, "several words, but not clearly a name"));
          out.also_username = null;
        }
      }
    }
    if (!cands.length) { out.reason = "Could not tell what this is. Pick a type below."; return out; }
    var seen = {}, ranked = [];
    cands.map(function (c, i) { return [c, i]; })
      .sort(function (a, b) { return b[0].score - a[0].score || a[1] - b[1]; })
      .forEach(function (pair) {
        var c = pair[0], key = c.type + "|" + c.value.toLowerCase();
        if (seen[key]) return;
        seen[key] = true;
        ranked.push(c);
      });
    var best = ranked[0];
    out.type = best.type; out.value = best.value; out.reason = best.reason;
    out.score = best.score; out.host = best.host || null;
    if (best.type !== "person") { out.also_username = null; out.location = ""; }
    else out.location = best.location || "";
    out.alternatives = ranked.slice(1, 4).map(function (c) {
      return { type: c.type, value: c.value, reason: c.reason, score: c.score };
    });
    return out;
  }

  var api = { detect: detect };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.OsintDetect = api;
})(typeof window !== "undefined" ? window : this);
