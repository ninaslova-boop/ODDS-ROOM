/* Loads app/app.js in a sandbox without a browser and runs checks given as a JSON file.
   Usage: node tests/js/harness.js <fixture.json>   -> prints a JSON report */
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const el = () => ({ textContent: "", innerHTML: "", value: "", style: {}, dataset: {}, onclick: null, onchange: null,
  addEventListener() {}, querySelector: () => el(), querySelectorAll: () => [], classList: { toggle() {} } });
const store = {};
const ctx = {
  console, Date, Math, JSON, Intl, Promise, Set, Map, Object, Array, String, Number, isNaN, parseFloat, parseInt,
  document: { addEventListener() {}, querySelector: () => el(), querySelectorAll: () => [] },
  window: { innerWidth: 1000, innerHeight: 800, scrollTo() {} },
  localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); } },
  location: { hash: "" }, history: { replaceState() {} }, navigator: {}, CSS: { escape: (s) => s },
  fetch: () => Promise.resolve({ ok: false, json: () => null }), confirm: () => true, prompt: () => null,
};
ctx.globalThis = ctx;
vm.createContext(ctx);
const src = fs.readFileSync(path.join(__dirname, "../../app/app.js"), "utf8");
vm.runInContext(src + "\n;globalThis.__app = { S, lineProb, claudeExport, b365Rows, rowResult, isBad, settleMk, bestValue, isPick, isStrong, minOdds, threshold };", ctx);
const app = ctx.__app;

const fx = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const S = app.S;
S.data.matches = fx.matches || [];
S.data.mismatches = fx.mismatches || [];
S.data.players = fx.players || {};
S.data.picks = fx.picks || { picks: [], stats: {} };
S.data.meta = fx.meta || {};
S.data.validation = fx.validation || {};
S.data.results = fx.results || {};
S.data.byId = Object.fromEntries(S.data.matches.map((m) => [m.id, m]));
S.b365 = { day: "all", minP: 0, q: "" };

const out = {};
out.lineProb = (fx.lineProb || []).map(([id, scope, kind, line]) => app.lineProb(S.data.byId[id], scope, kind, line));
out.export = app.claudeExport(fx.day || "all2", "all", true, true);
out.b365 = app.b365Rows().map((r) => ({ key: r.key, p: r.p, min: +app.minOdds(r.p).toFixed(2), line: r.line }));
out.rowResult = (fx.rowResult || []).map(([key, o, l]) => {
  const r = app.b365Rows().find((x) => x.key === key);
  const res = app.rowResult(r, { o, l });
  return { p: res.p, ok: res.ok, strong: !!res.strong, ev: res.ev ?? null, thr: res.thr ?? null };
});
out.rule = (fx.rule || []).map(([p, o]) => ({ pick: app.isPick(p, o), strong: app.isStrong(p, o), thr: app.threshold(o), min: app.minOdds(p) }));
out.bad = S.data.matches.filter((x) => app.isBad(x)).map((x) => x.id);
process.stdout.write(JSON.stringify(out));
