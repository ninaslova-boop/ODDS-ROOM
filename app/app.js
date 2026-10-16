/* The Odds Room Analytics — static front end. Data comes from data/*.json (built by GitHub Actions). */
"use strict";

const S = {
  data: { matches: [], players: {}, picks: { picks: [], stats: {} }, meta: {} },
  sport: "all",
  tab: "matches",
  open: new Set(),            // expanded match cards
  dtab: {},                   // selected inner tab per match
  rank: { min: 0.55, max: 0.92, kind: "all", group: "all", q: "", onlyOdds: false },
  value: { minEdge: 0.03, inclAssumed: true, showBad: false },
  players: { sport: "football", league: "all", q: "", sort: null, dir: -1, minMin: 450 },
  matchDay: "all", league: "all",
  log: { filter: "all" },
  post: { selected: [], type: "auto" },
  b365: { day: "today", minP: 0.2, q: "" },
};

const SPORTS = {
  football: { name: "Nogomet", icon: "⚽", en: "⚽" },
  nhl: { name: "NHL", icon: "🏒", en: "🏒" },
  nba: { name: "NBA", icon: "🏀", en: "🏀" },
  euroleague: { name: "Euroliga", icon: "🏀", en: "🏀" },
};
const UNIT = { goals: "golov", points: "točk", corners: "kotov", cards: "kartonov", sot: "strelov v okvir" };
const UNIT_EN = { goals: "goals", points: "points", corners: "corners", cards: "cards", sot: "shots on target" };

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (p, d = 0) => (p == null ? "–" : (p * 100).toFixed(d) + "%");
const odds = (o) => (o ? o.toFixed(2) : "–");
const fmtNum = (v, d = 2) => (v == null || isNaN(v) ? "–" : Number(v).toFixed(d));
const fmtLine = (l) => (l == null ? "" : Number(l).toString());
const STRONG_EV = 1.10;

/* a match that must not give picks or appear in "reliable" lists */
function isBad(x) { return !!(x.unreliable || x.mismatch || x.markets.some((m) => m.unreliable)); }
/* p_final with p_model in brackets */
function pfm(m) { return m.p_model != null && Math.abs(m.p_model - m.p) >= 0.005 ? `${pct(m.p)} (model ${pct(m.p_model)})` : pct(m.p); }
function valEntry(k) {
  const v = S.data.validation?.[k];
  if (!v) return null;
  const b = v.by_w?.[String(v.prod_w)] || Object.values(v.by_w || {})[0] || {};
  return { ...v, ok: !!v.validated && (b.games_with_market || 0) >= 150, b };
}
function isValidated(x) { return !!valEntry(x.league)?.ok; }
function validTag(x) {
  const v = valEntry(x.league);
  if (v?.ok) return "";
  return ` <span class="badge grey" data-tip="${v ? (v.b.games_with_market ? `Backtest ima premalo tekem (${v.games}).` : "Backtest brez zgodovinskih kvot: pravila picka ni bilo mogoče preveriti.") : "Za to tekmovanje še ni backtesta."}">ni validirano</span>`;
}

/* ---------- exact line: probabilities from the model distribution (p_final parameters) ---------- */
function normCdf(z) {
  // Abramowitz-Stegun 7.1.26
  const t = 1 / (1 + 0.3275911 * Math.abs(z) / Math.SQRT2);
  const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-z * z / 2);
  return z >= 0 ? (1 + y) / 2 : (1 - y) / 2;
}
function poisPmf(lam, n) { let p = Math.exp(-lam); for (let i = 1; i <= n; i++) p *= lam / i; return p; }
function scoreMatrix(lh, la, max, rho) {
  const M = [];
  for (let i = 0; i <= max; i++) { M.push([]); for (let j = 0; j <= max; j++) {
    let p = poisPmf(lh, i) * poisPmf(la, j);
    if (rho) {
      if (i === 0 && j === 0) p *= 1 - lh * la * rho; else if (i === 0 && j === 1) p *= 1 + lh * rho;
      else if (i === 1 && j === 0) p *= 1 + la * rho; else if (i === 1 && j === 1) p *= 1 - rho;
    }
    M[i].push(p);
  } }
  let s = 0; for (const r of M) for (const v of r) s += v;
  return M.map((r) => r.map((v) => v / s));
}
/* p of a market at any line. exp = [a, b] (football/NHL goals, basketball points). */
function lineProb(x, scope, kind, line, exp) {
  const [a, b] = exp || x.exp;
  if (x.sport === "nba" || x.sport === "euroleague") {
    const sdT = x.sd?.[0] || 18, sdM = x.sd?.[1] || 12, t = a + b, mg = a - b;
    if (scope === "match") { const p = normCdf(mg / sdM); return kind === "1" ? p : 1 - p; }
    if (scope === "total") { const po = 1 - normCdf((line - t) / sdT); return kind === "over" ? po : 1 - po; }
    if (scope === "spread") { const pc = 1 - normCdf((-line - mg) / sdM); return kind === "home" ? pc : 1 - normCdf((-line + mg) / sdM); }
    return null;
  }
  const hockey = x.sport === "nhl";
  const M = scoreMatrix(a, b, hockey ? 12 : 10, hockey ? 0 : -0.06);
  let p = 0;
  for (let i = 0; i < M.length; i++) for (let j = 0; j < M.length; j++) {
    const v = M[i][j], tot = i + j + (hockey && i === j ? 1 : 0);
    let ok = false;
    if (scope === "match") {
      if (hockey) ok = kind === "1" ? i > j : kind === "2" ? i < j : false;
      else ok = kind === "1" ? i > j : kind === "2" ? i < j : kind === "X" ? i === j : kind === "1X" ? i >= j : kind === "X2" ? i <= j : i !== j;
    } else if (scope === "total") ok = kind === "over" ? tot > line : tot < line;
    else if (scope === "btts") ok = kind === "yes" ? i > 0 && j > 0 : !(i > 0 && j > 0);
    else if (scope === "spread") ok = kind === "home" ? i - j > -line : j - i > -line;
    if (hockey && scope === "match" && i === j) { p += v * (kind === "1" ? 0.52 : 0.48); continue; }
    if (hockey && scope === "spread" && i === j) { p += v * (kind === "home" ? (line > 0 ? 1 : 0) * 0.52 + (line > 0 ? 0.48 : 0) : (line > 0 ? 1 : 0)); continue; }
    if (ok) p += v;
  }
  return p;
}
/* settle a market (same rules as pipeline/models.settle). Returns 1, 0 or null (push). */
function settleMk(sport, scope, kind, line, h, a) {
  if (scope === "match") { const r = h > a ? "1" : h < a ? "2" : "X"; return kind.includes(r) ? 1 : 0; }
  if (scope === "total") { const t = h + a; if (t === line) return null; return (kind === "over" ? t > line : t < line) ? 1 : 0; }
  if (scope === "btts") return ((h > 0 && a > 0) === (kind === "yes")) ? 1 : 0;
  if (scope === "spread") { const d = kind === "home" ? h - a + line : a - h + line; return d === 0 ? null : d > 0 ? 1 : 0; }
  return null;
}

function fmtTime(iso) {
  const d = new Date(iso);
  return d.toLocaleString("sl-SI", { weekday: "short", hour: "2-digit", minute: "2-digit" });
}
function dayKey(iso) {
  const d = new Date(iso);
  return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
}
function dayLabel(key) {
  const today = dayKey(new Date().toISOString());
  const tm = new Date(); tm.setDate(tm.getDate() + 1);
  if (key === today) return "Danes";
  if (key === dayKey(tm.toISOString())) return "Jutri";
  const [y, m, d] = key.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("sl-SI", { weekday: "long", day: "numeric", month: "numeric" });
}

/* ---------- market labels (SL for the app, EN for posts) ---------- */
function slLabel(m, x) {
  const H = x.home, A = x.away, L = fmtLine(m.line), u = UNIT[m.stat] || "";
  const hockey = x.sport === "nhl";
  switch (m.scope) {
    case "match":
      return { "1": `${H} zmaga${hockey ? " (vklj. OT)" : ""}`, X: hockey ? "Remi po 60 min" : "Remi", "2": `${A} zmaga${hockey ? " (vklj. OT)" : ""}`,
        "1X": `${H} ali remi`, X2: `${A} ali remi`, "12": "Brez remija (1 ali 2)" }[m.kind] || m.label;
    case "dnb": return `${m.kind === "home" ? H : A} (remi = vračilo)`;
    case "total": return `${m.kind === "over" ? "Več" : "Manj"} kot ${L} ${u}`;
    case "home": case "away": {
      const t = m.scope === "home" ? H : A;
      if (m.stat === "goals" && m.kind === "under" && m.line === 0.5) return `${t} brez gola`;
      return `${t} ${m.kind === "over" ? "več" : "manj"} kot ${L} ${u}`;
    }
    case "btts": return m.kind === "yes" ? "Oba zadeneta" : "Ne zadeneta oba";
    case "btts_over": return "Oba zadeneta + več kot 2.5";
    case "spread": return `${m.kind === "home" ? H : A} ${m.line > 0 ? "+" : ""}${L}`;
  }
  return m.label;
}
function enLabel(m, x) {
  const H = x.home, A = x.away, L = fmtLine(m.line), u = UNIT_EN[m.stat] || "";
  const hockey = x.sport === "nhl";
  switch (m.scope) {
    case "match":
      return { "1": `${H} to win${hockey ? " (incl. OT)" : ""}`, X: hockey ? "Draw after 60 min" : "Draw",
        "2": `${A} to win${hockey ? " (incl. OT)" : ""}`, "1X": `${H} or draw`, X2: `${A} or draw`, "12": "No draw (1 or 2)" }[m.kind];
    case "dnb": return `${m.kind === "home" ? H : A} draw no bet`;
    case "total": return `${m.kind === "over" ? "Over" : "Under"} ${L} ${u}`;
    case "home": case "away": {
      const t = m.scope === "home" ? H : A;
      if (m.stat === "goals" && m.kind === "under" && m.line === 0.5) return `${t} to not score`;
      return `${t} ${m.kind === "over" ? "over" : "under"} ${L} ${u}`;
    }
    case "btts": return m.kind === "yes" ? "Both teams to score" : "Both teams to score: No";
    case "btts_over": return "BTTS + Over 2.5 goals";
    case "spread": return `${m.kind === "home" ? H : A} ${m.line > 0 ? "+" : ""}${L}`;
  }
  return m.label;
}
const PROP_EN = {
  shots: (l) => `${Math.ceil(l)}+ shots`, sot: (l) => `${Math.ceil(l)}+ shots on target`, goals: () => "anytime goal",
  assists: () => "to assist", "g+a": () => "goal or assist", sog: (l) => `${Math.ceil(l)}+ shots on goal`,
  points: (l) => `${Math.ceil(l)}+ points`, pts: (l) => `over ${l} points`, reb: (l) => `over ${l} rebounds`,
  ast: (l) => `over ${l} assists`, pra: (l) => `over ${l} pts+reb+ast`, tpm: (l) => `${Math.ceil(l)}+ threes`,
};

/* ---------- data ---------- */
async function load() {
  const get = (f) => fetch(`data/${f}`, { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  const [matches, players, picks, meta, validation, results, userPicks] = await Promise.all(
    ["matches.json", "players.json", "picks.json", "meta.json", "validation.json", "results.json", "user_picks.json"].map(get));
  S.data.matches = matches?.matches || [];
  S.data.mismatches = matches?.mismatches || [];
  S.data.validation = validation?.competitions || {};
  S.data.results = results?.results || {};
  S.data.userPicks = userPicks || { picks: [], stats: {} };
  S.data.players = players || {};
  S.data.picks = picks || { picks: [], stats: {} };
  S.data.meta = meta || {};
  S.data.byId = Object.fromEntries(S.data.matches.map((m) => [m.id, m]));
  const gen = meta?.generated || matches?.generated;
  $("#updated").textContent = gen ? "posodobljeno " + new Date(gen).toLocaleString("sl-SI", { day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" }) : "ni podatkov";
  restore();
  renderChips();
  render();
}

function restore() {
  try {
    const s = JSON.parse(localStorage.getItem("oddsroom.ui") || "{}");
    if (s.sport) S.sport = s.sport;
    if (s.tab) S.tab = s.tab;
  } catch (e) { /* storage unavailable */ }
  const h = location.hash.slice(1);
  if (h && $(`#tabs [data-tab="${h}"]`)) S.tab = h;
}
function persist() {
  try { localStorage.setItem("oddsroom.ui", JSON.stringify({ sport: S.sport, tab: S.tab })); } catch (e) { /* ignore */ }
}

function sportMatches() {
  const now = Date.now() - 3 * 3600e3;
  return S.data.matches.filter((m) => (S.sport === "all" || m.sport === S.sport) && new Date(m.kickoff).getTime() >= now);
}

/* every predicted event: team markets + player props */
function allEvents() {
  const out = [];
  for (const x of sportMatches()) {
    for (const m of x.markets) {
      out.push({ kind: "team", sport: x.sport, match: x, m, p: m.p, odds: m.odds, edge: m.edge, assumed: m.odds_assumed,
        title: slLabel(m, x), sub: `${x.home} – ${x.away}`, group: m.group, kickoff: x.kickoff });
    }
  }
  for (const [sp, list] of Object.entries(S.data.players)) {
    if (!Array.isArray(list) || (S.sport !== "all" && S.sport !== sp)) continue;
    for (const pl of list) {
      if (!pl.props || !pl.next) continue;
      const x = S.data.byId[pl.next.match];
      if (!x || new Date(x.kickoff).getTime() < Date.now() - 3 * 3600e3) continue;
      for (const pr of pl.props) {
        out.push({ kind: "player", sport: sp, match: x, player: pl, prop: pr, p: pr.p, odds: null, edge: null,
          title: `${pl.name}: ${pr.label}`, sub: `${pl.team} ${pl.next.home ? "vs" : "@"} ${pl.next.opp}`, group: "Igralci", kickoff: x.kickoff });
      }
    }
  }
  return out;
}

/* ---------- chrome ---------- */
function renderChips() {
  const counts = {};
  for (const m of S.data.matches) counts[m.sport] = (counts[m.sport] || 0) + 1;
  const chips = [["all", "Vse"], ...Object.entries(SPORTS).map(([k, v]) => [k, `${v.icon} ${v.name}`])];
  $("#sportChips").innerHTML = chips.map(([k, l]) =>
    `<button class="chip ${S.sport === k ? "on" : ""}" data-sport="${k}">${l}${k !== "all" ? ` <span class="faint">${counts[k] || 0}</span>` : ""}</button>`).join("");
}

document.addEventListener("click", (e) => {
  const chip = e.target.closest("[data-sport]");
  if (chip) { S.sport = chip.dataset.sport; S.league = "all"; persist(); renderChips(); render(); return; }
  const tab = e.target.closest("#tabs [data-tab]");
  if (tab) { S.tab = tab.dataset.tab; history.replaceState(null, "", "#" + S.tab); persist(); render(); window.scrollTo(0, 0); return; }
  const dt = e.target.closest("[data-dtab]");
  if (dt) { S.dtab[dt.closest(".match").dataset.id] = dt.dataset.dtab; render(); return; }
  const card = e.target.closest(".match[data-id]");
  if (card && !e.target.closest(".details")) {
    const id = card.dataset.id;
    S.open.has(id) ? S.open.delete(id) : S.open.add(id);
    render(); return;
  }
});

/* tooltip for any element with data-tip */
document.addEventListener("pointermove", (e) => {
  const el = e.target.closest("[data-tip]");
  const tip = $("#tip");
  if (!el) { tip.hidden = true; return; }
  tip.innerHTML = el.dataset.tip;
  tip.hidden = false;
  const w = tip.offsetWidth, h = tip.offsetHeight;
  tip.style.left = Math.min(window.innerWidth - w - 8, e.clientX + 12) + "px";
  tip.style.top = Math.max(8, e.clientY - h - 12) + "px";
});

function render() {
  document.querySelectorAll("#tabs [data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === S.tab));
  const v = $("#view");
  if (!S.data.matches.length && !Object.keys(S.data.players).length && S.tab !== "log") {
    v.innerHTML = emptyState();
    return;
  }
  ({ matches: viewMatches, ranking: viewRanking, value: viewValue, b365: viewB365, players: viewPlayers, log: viewLog,
    backtest: viewBacktest, posts: viewPosts })[S.tab](v);
}

function emptyState() {
  const errs = (S.data.meta.errors || []).slice(0, 8).map((e) => `<li>${esc(e)}</li>`).join("");
  return `<div class="card empty"><b>Še ni podatkov</b>
    Podatke zgradi GitHub Actions (workflow <i>Update data</i>). Zaženi ga ročno ali počakaj na naslednji zagon.
    ${errs ? `<ul style="text-align:left;font-size:12px">${errs}</ul>` : ""}</div>`;
}

function mismatchList() {
  const out = (S.data.mismatches || []).filter((m) => S.sport === "all" || m.sport === S.sport)
    .filter((m) => new Date(m.kickoff).getTime() >= Date.now() - 3 * 3600e3);
  return out.sort((a, b) => a.kickoff.localeCompare(b.kickoff));
}
function mismatchBox() {
  const list = mismatchList();
  if (!list.length) return "";
  return `<details class="warn-box"><summary><b>DATA MISMATCH: ${list.length}</b> · viri se ne ujemajo, te tekme niso v pickih, value seznamih in dogodkih igralcev</summary>
    ${list.map((m) => `<div style="font-size:13px;margin-top:4px">${fmtTime(m.kickoff)} · ${esc(m.league || "")}: <b>${esc(m.home)} – ${esc(m.away)}</b> <span class="faint">${esc(m.reason)}</span></div>`).join("")}</details>`;
}
function sourceWarnings() {
  const st = S.data.meta.status || {};
  const bad = Object.entries(st).filter(([k, v]) => !v.ok && (S.sport === "all" || S.sport === k));
  if (!bad.length) return "";
  return `<div class="warn-box">Ni svežih tekem ali modela za: ${bad.map(([k]) => SPORTS[k]?.name || k).join(", ")}.
    Lahko je premor sezone ali težava vira. <details><summary>Podrobnosti</summary>${(S.data.meta.errors || []).slice(0, 15).map(esc).join("<br>")}</details></div>`;
}

/* ---------- small components ---------- */
function formDots(f, big) {
  if (!f || !f.length) return "";
  return `<span class="dots ${big ? "big" : ""}">${f.map((g) => `<i class="${g.r}" data-tip="${esc(`${g.h ? "doma" : "v gosteh"} vs ${g.o}: ${g.s} (${g.d})`)}"></i>`).join("")}</span>`;
}
function formChips(f) {
  if (!f || !f.length) return "";
  return `<span class="form">${f.map((g) => `<i class="${g.r}" data-tip="${esc(`${g.h ? "doma" : "v gosteh"} vs ${g.o}: ${g.s} (${g.d})`)}">${g.r === "W" ? "Z" : g.r === "D" ? "R" : "P"}</i>`).join("")}</span>`;
}
function pbar(p) {
  return `<div class="pbar"><div class="track"><div class="fill" style="width:${Math.round(p * 100)}%"></div></div><span>${pct(p)}</span></div>`;
}
function edgeCell(e, assumed) {
  if (e == null) return `<span class="faint">–</span>`;
  const cls = e > 0 ? "edge-pos" : "edge-neg";
  return `<span class="${cls}" ${assumed ? 'data-tip="Kvota ni znana, privzeto 1.91 (-110)."' : ""}>${e > 0 ? "+" : ""}${(e * 100).toFixed(1)}%${assumed ? "~" : ""}</span>`;
}

/* top events of a match: different groups, 55–90 % */
function topEvents(x, n = 3) {
  const seen = new Set(), out = [];
  const cands = x.markets.filter((m) => m.p >= 0.55 && m.p <= 0.9 && m.scope !== "dnb").sort((a, b) => b.p - a.p);
  for (const m of cands) {
    const g = m.group + (m.scope === "home" || m.scope === "away" ? m.scope : "");
    if (seen.has(g)) continue;
    seen.add(g); out.push(m);
    if (out.length >= n) break;
  }
  return out;
}

/* ---------- view: matches ---------- */
function viewMatches(v) {
  let list = sportMatches();
  const leagues = [...new Set(list.map((m) => m.league_name))].sort();
  if (S.league !== "all") list = list.filter((m) => m.league_name === S.league);
  const days = [...new Set(list.map((m) => dayKey(m.kickoff)))].sort();
  if (S.matchDay !== "all" && days.includes(S.matchDay)) list = list.filter((m) => dayKey(m.kickoff) === S.matchDay);
  const groups = {};
  for (const m of list) (groups[dayKey(m.kickoff)] ||= []).push(m);

  v.innerHTML = `${sourceWarnings()}${mismatchBox()}
    <div class="filters">
      <label>Dan<select id="fDay"><option value="all">Vsi dnevi</option>${days.map((d) => `<option value="${d}" ${S.matchDay === d ? "selected" : ""}>${dayLabel(d)}</option>`).join("")}</select></label>
      <label>Liga<select id="fLeague"><option value="all">Vse lige</option>${leagues.map((l) => `<option ${S.league === l ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>
      <span class="spacer"></span><span class="count">${list.length} tekem · tapni tekmo za podrobnosti</span>
    </div>
    ${Object.keys(groups).length ? Object.entries(groups).map(([d, ms]) => `<div class="day-h">${dayLabel(d)} <span class="faint">${ms.length}</span></div>${ms.map(matchCard).join("")}`).join("")
      : `<div class="card empty"><b>Ni tekem</b>Za ta izbor v naslednjih 7 dneh ni tekem.</div>`}`;
  $("#fDay").onchange = (e) => { S.matchDay = e.target.value; render(); };
  $("#fLeague").onchange = (e) => { S.league = e.target.value; render(); };
}

const STATUS_SL = { out: "ne igra", doubt: "vprašljiv" };
const SRC_SL = { pinnacle: "Pinnacle", espn: "ESPN", avg: "povprečje stavnic" };
function refText(x) {
  const r = x.market_ref;
  if (!r) return "";
  const f = (k) => `${pct(r.model[k])} / ${pct(r.p[k])}`;
  const parts = ["1", "X", "2"].filter((k) => r.p[k] != null).map((k) => `${k}: ${f(k)}`);
  if (r.p["over2.5"] != null) parts.push(`O2.5: ${f("over2.5")}`);
  if (r.p.over != null) parts.push(`O${r.line}: ${f("over")}`);
  return `Model se od trga (${SRC_SL[r.src] || r.src}, brez marže) razlikuje za več kot 10 o. t.<br>` +
    `${parts.join(" · ")}<br>(model / trg)` +
    (r.blend ? `<br>Prikazane verjetnosti: ${Math.round(r.blend.w_model * 100)} % model + ${100 - Math.round(r.blend.w_model * 100)} % Pinnacle.` : "");
}
function unreliableTag(m, x) {
  return m.unreliable ? ` <span class="badge red" data-tip="${esc(refText(x))}">model nezanesljiv</span>` : "";
}
function absList(x, side) { return x.availability?.absent?.[side] || []; }
function newsBadges(x) {
  const av = x.availability;
  if (!av) return "";
  const n = absList(x, "home").length + absList(x, "away").length;
  const names = ["home", "away"].flatMap((s) => absList(x, s).map((a) => `${esc(a.name)} (${STATUS_SL[a.status] || a.status})`)).slice(0, 12).join("<br>");
  const adj = x.adj ? `<span class="badge red" data-tip="${esc(adjText(x))}">PRILAGOJENO</span> ` : "";
  return `${adj}${av.lineup ? `<span class="badge" data-tip="Začetni postavi sta objavljeni.">POSTAVA ✓</span> ` : ""}` +
    (n ? `<span class="badge grey" data-tip="${names}">🚑 ${n}</span> ` : "");
}
function adjText(x) {
  if (!x.adj) return "";
  return Object.entries(x.adj.factors).map(([side, f]) => {
    const miss = x.adj.missing?.[side] || [];
    return `${x[side]}: napad ${Math.round((f - 1) * 100)} %${miss.length ? ` (brez: ${miss.slice(0, 4).join(", ")})` : ""}`;
  }).join(" · ");
}
function goalieText(g) {
  if (!g) return "";
  const eff = Math.round((1 - g.factor) * 100);
  return `${esc(g.name)} · obr. ${(g.sv * 100).toFixed(1)} % · verjetnost ${Math.round(g.p * 100)} %` +
    (g.b2b ? " · 2. tekma v 2 dneh" : "") + (g.alt && g.p < 0.75 ? ` · lahko ${esc(g.alt)}` : "") +
    (Math.abs(eff) >= 1 ? ` · nasprotnik ${eff > 0 ? "−" : "+"}${Math.abs(eff)} % golov` : "");
}
function goalieBlock(x) {
  if (!x.goalies) return "";
  return `<h4>Verjetna vratarja</h4><p class="note">NHL vratarja ne objavi vnaprej. Ocena: največ začetih tekem, na 2. tekmi v 2 dneh pogosto brani rezervni. Obrambe so izravnane proti povprečju lige.</p>
    <div class="grid2">${["home", "away"].map((s) => `<div><div style="font-weight:700">${esc(x[s])}</div><div style="font-size:13px">🥅 ${goalieText(x.goalies[s]) || "–"}</div></div>`).join("")}</div>`;
}
function availBlock(x) {
  const av = x.availability;
  if (!av) return goalieBlock(x);
  const col = (side) => {
    const lu = av.lineup?.[side];
    const abs = absList(x, side);
    return `<div><div class="h" style="font-weight:700;margin-bottom:4px">${esc(x[side])}</div>
      ${lu ? `<div class="muted" style="font-size:12px">Začetna postava</div><div style="font-size:13px">${lu.xi.map((p) => esc(p.name)).join(", ")}</div>
        <div class="muted" style="font-size:12px;margin-top:4px">Klop: ${lu.bench.map((p) => esc(p.name)).join(", ") || "–"}</div>` : ""}
      ${abs.length ? `<div class="muted" style="font-size:12px;margin-top:6px">Odsotni / vprašljivi</div>${abs.map((a) =>
        `<div style="font-size:13px">${a.status === "out" ? "🚑" : "⚠️"} ${esc(a.name)} <span class="faint">${esc(a.reason || "")}${a.chance != null && a.status !== "out" ? ` · ${a.chance} %` : ""} · ${esc(a.src || "")}</span></div>`).join("")}` : (lu ? "" : `<div class="faint" style="font-size:12px">Ni znanih odsotnosti.</div>`)}
    </div>`;
  };
  return `${x.adj ? `<div class="warn-box">Model je prilagojen: ${esc(adjText(x))}</div>` : ""}
    <div class="grid2">${col("home")}${col("away")}</div>${goalieBlock(x)}`;
}

function mkt(x, scope, kind, line) {
  return x.markets.find((m) => m.stat !== "corners" && m.stat !== "cards" && m.stat !== "sot" &&
    m.scope === scope && m.kind === kind && (line === undefined || m.line === line));
}
/* one 1X2 (or 1/2) stacked bar: home, draw, away */
function resultBar(x) {
  const h = mkt(x, "match", "1"), d = x.sport === "football" ? mkt(x, "match", "X") : null, a = mkt(x, "match", "2");
  if (!h || !a) return "";
  const segs = [["h", h.p, "1"], ...(d ? [["d", d.p, "X"]] : []), ["a", a.p, "2"]];
  const ref = x.market_ref?.p;
  const tip = (k, p) => `${k === "1" ? esc(x.home) : k === "2" ? esc(x.away) : "Remi"}: <b>${pct(p)}</b>` +
    (ref?.[k] != null ? `<br>trg: ${pct(ref[k])}` : "");
  return `<div class="rbar">${segs.map(([c, p, k]) => `<div class="seg ${c}" style="flex:${Math.max(p, 0.04)}" data-tip="${tip(k, p)}"><span>${pct(p)}</span></div>`).join("")}</div>`;
}
/* best pick of a match: p_final x bet365 > 1 (reliable matches only) */
function bestValue(x) {
  if (isBad(x)) return null;
  return x.markets.filter((m) => m.b365 && m.p * m.b365 > 1).sort((a, b) => b.p * b.b365 - a.p * a.b365)[0] || null;
}
function cardFacts(x) {
  const isBall = x.sport === "nba" || x.sport === "euroleague";
  if (isBall) {
    const t = x.exp[0] + x.exp[1], m = x.exp[0] - x.exp[1];
    return [["Skupaj", fmtNum(t, 1)], ["Razlika", (m > 0 ? "+" : "") + fmtNum(m, 1)]];
  }
  const facts = [["Najv. izid", x.top_scores?.[0]?.s || "–"]];
  if (x.sport === "football") {
    const o = mkt(x, "total", "over", 2.5), b = mkt(x, "btts", "yes");
    if (o) facts.push(["Več 2.5", pct(o.p)]);
    if (b) facts.push(["Oba dasta", pct(b.p)]);
  } else {
    const line = x.odds_raw?.total || 5.5, o = mkt(x, "total", "over", line);
    if (o) facts.push([`Več ${line}`, pct(o.p)]);
  }
  return facts;
}
function statusIcons(x) {
  const out = [];
  if (x.mismatch) out.push(`<span class="ico warn" data-tip="${esc("DATA MISMATCH: " + x.mismatch)}">≠</span>`);
  else if (isBad(x)) out.push(`<span class="ico warn" data-tip="${esc(refText(x))}">⚠</span>`);
  if (x.context?.length) out.push(`<span class="ico" data-tip="${esc(x.context.join("<br>"))}">⏱</span>`);
  if (x.adj) out.push(`<span class="ico red" data-tip="${esc("Model prilagojen: " + adjText(x))}">±</span>`);
  if (x.availability?.lineup) out.push(`<span class="ico ok" data-tip="Začetni postavi sta objavljeni.">XI</span>`);
  const n = absList(x, "home").length + absList(x, "away").length;
  if (n) {
    const names = ["home", "away"].flatMap((s) => absList(x, s).map((a) => `${esc(a.name)} (${STATUS_SL[a.status] || a.status})`)).slice(0, 12).join("<br>");
    out.push(`<span class="ico" data-tip="${names}">🚑${n}</span>`);
  }
  return out.join("");
}

function matchCard(x) {
  const sp = SPORTS[x.sport] || {};
  const v = bestValue(x);
  const t = new Date(x.kickoff);
  return `<article class="card match ${S.open.has(x.id) ? "open" : ""}" data-id="${esc(x.id)}">
    <div class="m-top">
      <span class="m-time num">${t.toLocaleTimeString("sl-SI", { hour: "2-digit", minute: "2-digit" })}</span>
      <span class="m-league">${sp.icon || ""} ${esc(x.league_name)}</span>
      <span class="spacer"></span>${statusIcons(x)}
    </div>
    <div class="m-teams">
      <div class="m-team"><span class="nm">${esc(x.home)}</span>${formDots(x.form?.home)}<span class="xg num">${expLbl(x, 0)}</span></div>
      <div class="m-team"><span class="nm">${esc(x.away)}</span>${formDots(x.form?.away)}<span class="xg num">${expLbl(x, 1)}</span></div>
    </div>
    ${resultBar(x)}
    <div class="m-facts">${cardFacts(x).map(([l, val]) => `<span><em>${l}</em> <b class="num">${val}</b></span>`).join("")}
      ${v ? `<span class="m-value" data-tip="p_final ${pfm(v)} · min. kvota ${(1 / v.p).toFixed(2)} · bet365 ${odds(v.b365)}">PICK ${esc(slLabel(v, x))} @ ${odds(v.b365)} <b>${(v.p * v.b365).toFixed(2)}</b>${v.p * v.b365 >= STRONG_EV ? " ◆" : ""}</span>` : ""}
    </div>
    ${S.open.has(x.id) ? matchDetails(x) : ""}
  </article>`;
}

function expLbl(x, i) {
  if (x.sport === "football") return fmtNum(x.exp[i]) + " xG";
  if (x.sport === "nhl") return fmtNum(x.exp[i]) + " gol.";
  return fmtNum(x.exp[i], 1) + " t.";
}

function matchDetails(x) {
  const isBall = x.sport === "nba" || x.sport === "euroleague";
  const groups = {};
  for (const m of x.markets) (groups[m.group] ||= []).push(m);
  const st = x.stats || {};
  const statRows = x.sport === "football"
    ? [["Goli dani", "gf"], ["Goli prejeti", "ga"], ["Streli", "sf"], ["Streli prejeti", "sa"], ["V okvir", "sot"], ["Koti", "cf"], ["BTTS", "btts", 1], ["Več kot 2.5", "o25", 1], ["Brez prejetega", "cs", 1]]
    : [["Tekme", "gp"], [isBall ? "Točke dane" : "Goli dani", "for"], [isBall ? "Točke prejete" : "Goli prejeti", "against"], ["Zmage", "wins"]];
  const hasStats = Object.keys(st.home || {}).length || Object.keys(st.away || {}).length;
  const elBox = x.sport === "euroleague" && (st.home?.box || st.away?.box);
  const bx = (side, per, k) => st[side]?.box?.[per]?.[k] ?? "–";
  const elTbl = elBox ? `<h4>Uradna statistika ekip (EuroLeague API): sezona / zadnjih 5</h4><table class="tbl compact"><tr><th></th><th class="r">${esc(x.home)}</th><th class="r">${esc(x.away)}</th></tr>
    ${[["Točke", "pts"], ["Prejete točke", "opp_pts"], ["Skoki", "reb"], ["Podaje", "ast"], ["Trojke", "tpm"]].map(([l, k]) => `<tr><td class="muted">${l}</td><td class="r num">${bx("home", "season", k)} / ${bx("home", "l5", k)}</td><td class="r num">${bx("away", "season", k)} / ${bx("away", "l5", k)}</td></tr>`).join("")}</table>` : "";
  const statTbl = elTbl + (hasStats ? `<table class="tbl compact"><tr><th>${x.sport === "football" ? "Sezona / tekmo" : "Ta sezona"}</th><th class="r">${esc(x.home)}</th><th class="r">${esc(x.away)}</th></tr>
    ${statRows.map(([l, k, isP]) => `<tr><td class="muted">${l}</td><td class="r num">${isP ? pct(st.home?.[k]) : st.home?.[k] ?? "–"}</td><td class="r num">${isP ? pct(st.away?.[k]) : st.away?.[k] ?? "–"}</td></tr>`).join("")}</table>`
    : `<p class="note">Za to tekmovanje ni sezonske statistike. Model uporablja rezultate tekmovanja in moč domače lige.</p>`);
  const model = x.total_dist ? `<h4>Porazdelitev skupnih golov</h4>${distChart(x.total_dist, x.total_mode)}
      <h4>Najverjetnejši izidi</h4><div class="chips">${x.top_scores.map((s) => `<span class="ev">${s.s} <b>${pct(s.p, 1)}</b></span>`).join("")}</div>`
    : `<p class="note">Pričakovano ${fmtNum(x.exp[0], 1)} – ${fmtNum(x.exp[1], 1)}. Raztros: total ±${x.sd?.[0]}, razlika ±${x.sd?.[1]} (1 SD).</p>`;
  const extra = x.extra ? `<p class="note">${x.extra.corners ? `Koti <b>${x.extra.corners[0]} – ${x.extra.corners[1]}</b> · ` : ""}${x.extra.cards ? `kartoni <b>${x.extra.cards[0]} – ${x.extra.cards[1]}</b> · ` : ""}${x.extra.sot ? `streli v okvir <b>${x.extra.sot[0]} – ${x.extra.sot[1]}</b>` : ""}</p>` : "";
  const ref = x.market_ref ? `<div class="refbox ${x.markets.some((m) => m.unreliable) ? "bad" : ""}">${refText(x).replace("razlikuje za več kot 10 o. t.", x.markets.some((m) => m.unreliable) ? "razlikuje za več kot 10 o. t." : "ujema v okviru 10 o. t.")}</div>` : "";
  const players = matchPlayers(x);
  const main = ["Izid", "Skupni goli", "Skupaj", "Skupne točke", "BTTS", "Hendikep"];
  const tabs = [["trgi", "Trgi"], ["model", "Model"], ["stat", "Statistika"], ...(x.availability || x.goalies ? [["postave", x.goalies ? "Postave/vratarja" : "Postave"]] : []), ...(players ? [["igralci", "Igralci"]] : [])];
  const cur = S.dtab[x.id] && tabs.some(([k]) => k === S.dtab[x.id]) ? S.dtab[x.id] : "trgi";
  const body = {
    trgi: ref + Object.entries(groups).map(([g, ms]) => `<details class="grp" ${main.includes(g) ? "open" : ""}><summary>${esc(g)} <span class="faint">${ms.length}</span></summary><div class="scroll"><table class="tbl">
      <tr><th>Dogodek</th><th>Verjetnost</th><th class="r">Fer</th><th class="r">Kvota</th><th class="r">Prednost</th></tr>
      ${ms.map((m) => `<tr class="${m.edge >= 0.05 && !m.unreliable ? "hl" : ""}"><td>${esc(slLabel(m, x))}${unreliableTag(m, x)}</td><td>${pbar(m.p)}</td><td class="r num">${odds(m.fair)}</td><td class="r num">${odds(m.odds)}${m.odds_assumed ? "~" : ""}</td><td class="r num">${edgeCell(m.edge, m.odds_assumed)}</td></tr>`).join("")}
      </table></div></details>`).join(""),
    model: model + extra,
    stat: statTbl + `<h4>Forma</h4><div class="formrows"><div><span class="muted">${esc(x.home)}</span> ${formDots(x.form?.home, true)}</div><div><span class="muted">${esc(x.away)}</span> ${formDots(x.form?.away, true)}</div></div>`,
    postave: availBlock(x),
    igralci: players,
  }[cur];
  return `<div class="details">
    <nav class="dtabs">${tabs.map(([k, l]) => `<button data-dtab="${k}" class="${k === cur ? "on" : ""}">${l}</button>`).join("")}</nav>
    <div class="dbody">${body}</div>
  </div>`;
}

function matchPlayers(x) {
  const list = (S.data.players[x.sport] || []).filter((p) => p.next?.match === x.id && (p.props || p.props_note));
  if (!list.length) return "";
  const key = { football: "1+ strel v okvir", nhl: "Strelov na gol 3+", nba: null }[x.sport];
  const sorted = list.slice().sort((a, b) => (b.next.exp_shots || b.next.exp_sog || b.next.proj_pts || 0) - (a.next.exp_shots || a.next.exp_sog || a.next.proj_pts || 0)).slice(0, 12);
  return `<div class="scroll"><table class="tbl"><tr><th>Igralec</th><th>Ekipa</th><th>Napoved</th><th>Top dogodki</th></tr>
    ${sorted.map((p) => {
      if (!p.props) return `<tr><td>${esc(p.name)}</td><td class="muted">${esc(p.team)}</td><td class="num">${fmtNum(p.pts, 1)} točk (sezona)</td><td class="faint">premalo tekem (${p.gp}/5) – ni verjetnosti</td></tr>`;
      const proj = x.sport === "football" ? `${p.next.exp_shots} strelov · ${p.next.exp_sot} v okvir` : x.sport === "nhl" ? `${p.next.exp_sog} SOG` : `${p.next.proj_pts} točk`;
      const props = p.props.filter((r) => r.p >= 0.5 && r.p <= 0.92).sort((a, b) => b.p - a.p).slice(0, 2);
      return `<tr><td>${esc(p.name)}</td><td class="muted">${esc(p.team)}</td><td class="num">${proj}</td><td>${props.map((r) => `${esc(r.label)} <b class="g">${pct(r.p)}</b>`).join(" · ") || (key ? "" : "")}</td></tr>`;
    }).join("")}</table></div>`;
}

function distChart(dist, mode) {
  const max = Math.max(...dist.map((d) => d.p));
  return `<div class="dist" role="img" aria-label="Porazdelitev skupnega števila golov">${dist.map((d) =>
    `<div class="col ${d.n === mode ? "mode" : ""}" data-tip="${d.n}${d.n === dist.length - 1 ? "+" : ""} skupaj: <b>${pct(d.p, 1)}</b>">
      <span class="v">${pct(d.p)}</span><div class="bar" style="height:${(d.p / max) * 80}%"></div><span class="n">${d.n}</span></div>`).join("")}</div>`;
}

/* ---------- view: ranking ---------- */
function viewRanking(v) {
  const R = S.rank;
  let ev = allEvents().filter((e) => e.p >= R.min && e.p <= R.max);
  if (R.kind !== "all") ev = ev.filter((e) => e.kind === R.kind);
  const groups = [...new Set(ev.map((e) => e.group))].sort();
  if (R.group !== "all") ev = ev.filter((e) => e.group === R.group);
  if (R.onlyOdds) ev = ev.filter((e) => e.odds);
  if (R.q) { const q = R.q.toLowerCase(); ev = ev.filter((e) => (e.title + " " + e.sub).toLowerCase().includes(q)); }
  ev.sort((a, b) => b.p - a.p);
  const shown = ev.slice(0, 250);
  v.innerHTML = `<h2>Rang lestvica najverjetnejših dogodkov</h2>
    <p class="section-note">Vsi dogodki modela (ekipe in igralci) za naslednjih 7 dni, razvrščeni po verjetnosti. Zelo verjetni dogodki imajo nizke kvote – uporabi drsnike.</p>
    <div class="filters">
      <label>Min. verjetnost <span class="g" id="minV">${pct(R.min)}</span><input type="range" id="fMin" min="0.3" max="0.95" step="0.01" value="${R.min}"></label>
      <label>Maks. verjetnost <span class="g" id="maxV">${pct(R.max)}</span><input type="range" id="fMax" min="0.5" max="1" step="0.01" value="${R.max}"></label>
      <label>Vrsta<select id="fKind"><option value="all">Vse</option><option value="team" ${R.kind === "team" ? "selected" : ""}>Ekipe</option><option value="player" ${R.kind === "player" ? "selected" : ""}>Igralci</option></select></label>
      <label>Trg<select id="fGroup"><option value="all">Vsi</option>${groups.map((g) => `<option ${R.group === g ? "selected" : ""}>${esc(g)}</option>`).join("")}</select></label>
      <label>Išči<input id="fQ" value="${esc(R.q)}" placeholder="ekipa, igralec…"></label>
      <label class="check"><input type="checkbox" id="fOdds" ${R.onlyOdds ? "checked" : ""}> samo s kvoto</label>
    </div>
    <div class="card scroll" style="padding:4px 6px"><table class="tbl stack rank">
      <tr><th>#</th><th>Dogodek</th><th>Tekma</th><th>Čas</th><th>Verjetnost</th><th class="r">Fer</th><th class="r">Kvota</th><th class="r">Prednost</th></tr>
      ${shown.map((e, i) => `<tr class="${e.edge >= 0.05 && !e.m?.unreliable ? "hl" : ""}"><td class="faint num">${i + 1}</td><td>${SPORTS[e.sport]?.icon || ""} ${esc(e.title)}${e.m ? unreliableTag(e.m, e.match) : ""}</td><td class="muted">${esc(e.sub)}</td><td class="muted" style="white-space:nowrap">${fmtTime(e.kickoff)}</td><td>${pbar(e.p)}</td><td class="r num">${odds(1 / e.p)}</td><td class="r num">${odds(e.odds)}</td><td class="r">${edgeCell(e.edge, e.assumed)}</td></tr>`).join("")}
    </table>${ev.length > shown.length ? `<div class="muted" style="padding:8px;font-size:12px">Prikazanih ${shown.length} od ${ev.length}. Zoži filtre.</div>` : ""}
    ${!ev.length ? `<div class="empty">Ni dogodkov za te filtre.</div>` : ""}</div>`;
  const re = () => render();
  $("#fMin").oninput = (e) => { R.min = +e.target.value; $("#minV").textContent = pct(R.min); };
  $("#fMin").onchange = re;
  $("#fMax").oninput = (e) => { R.max = +e.target.value; $("#maxV").textContent = pct(R.max); };
  $("#fMax").onchange = re;
  $("#fKind").onchange = (e) => { R.kind = e.target.value; re(); };
  $("#fGroup").onchange = (e) => { R.group = e.target.value; re(); };
  $("#fOdds").onchange = (e) => { R.onlyOdds = e.target.checked; re(); };
  $("#fQ").onchange = (e) => { R.q = e.target.value; re(); };
}

/* ---------- view: value ---------- */
function valueEvents() {
  return allEvents().filter((e) => e.edge != null && e.edge >= S.value.minEdge && e.p >= 0.3 && (S.value.inclAssumed || !e.assumed)
      && (S.value.showBad || !isBad(e.match)))
    .sort((a, b) => (isBad(a.match) - isBad(b.match)) || b.edge - a.edge);
}
function viewValue(v) {
  const ev = valueEvents();
  v.innerHTML = `<h2>Value: model proti kvoti</h2>
    <p class="section-note">Prednost = p_final × kvota − 1, kvota = povprečje stavnic (samo za orientacijo; pick velja šele s kvoto bet365, glej zavihek bet365). p_final = model, zmešan s trgom; v oklepaju je čisti model. Kvota z "~" je privzeta (-110). Tekme z oznako "model nezanesljiv" (razlika do trga nad 10 o. t. na glavnem trgu) ali DATA MISMATCH niso na seznamu in nikoli ne dajo picka.</p>
    <div class="filters">
      <label>Min. prednost <span class="g" id="eV">${(S.value.minEdge * 100).toFixed(0)}%</span><input type="range" id="fEdge" min="0" max="0.3" step="0.01" value="${S.value.minEdge}"></label>
      <label class="check"><input type="checkbox" id="fAss" ${S.value.inclAssumed ? "checked" : ""}> privzete kvote</label>
      <label class="check"><input type="checkbox" id="fBad" ${S.value.showBad ? "checked" : ""}> nezanesljivi trgi</label>
    </div>
    <div class="card scroll" style="padding:4px 6px"><table class="tbl stack">
      <tr><th>Dogodek</th><th>Tekma</th><th>Čas</th><th>Model</th><th class="r">Kvota</th><th class="r">Implicirano</th><th class="r">Prednost</th><th></th></tr>
      ${ev.map((e, i) => `<tr><td>${SPORTS[e.sport]?.icon || ""} ${esc(e.title)}${e.m ? unreliableTag(e.m, e.match) : ""}${validTag(e.match)}</td><td class="muted">${esc(e.sub)}</td><td class="muted" style="white-space:nowrap">${fmtTime(e.kickoff)}</td><td>${pbar(e.p)}${e.m?.p_model != null ? `<div class="faint" style="font-size:11px">model ${pct(e.m.p_model)}</div>` : ""}</td><td class="r num">${odds(e.odds)}${e.assumed ? "~" : ""}</td><td class="r num">${pct(1 / e.odds)}</td><td class="r">${edgeCell(e.edge, e.assumed)}</td><td><button class="btn o s" data-post="${i}">Objava</button></td></tr>`).join("")}
    </table>${!ev.length ? `<div class="empty"><b>Ni value dogodkov</b>Ni kvot ali pa model ne vidi prednosti. "Tiho = malo pickov."</div>` : ""}</div>`;
  $("#fEdge").oninput = (e) => { S.value.minEdge = +e.target.value; $("#eV").textContent = (S.value.minEdge * 100).toFixed(0) + "%"; };
  $("#fEdge").onchange = () => render();
  $("#fAss").onchange = (e) => { S.value.inclAssumed = e.target.checked; render(); };
  $("#fBad").onchange = (e) => { S.value.showBad = e.target.checked; render(); };
  v.querySelectorAll("[data-post]").forEach((b) => b.onclick = () => {
    S.post.selected = [evKey(ev[+b.dataset.post])]; S.post.type = "auto"; S.tab = "posts"; history.replaceState(null, "", "#posts"); render();
  });
}

/* ---------- view: players ---------- */
const PCOLS = {
  football: [["name", "Igralec"], ["team", "Ekipa"], ["pos", "Poz."], ["min", "Min", 0], ["goals", "G", 0], ["xg90", "xG/90", 2], ["sh90", "Streli/90", 2], ["xa90", "xA/90", 2], ["kp90", "KP/90", 2], ["_next", "Naslednja"], ["_p1", "P(1+ v okvir)"], ["_p2", "P(2+ strela)"]],
  nhl: [["name", "Igralec"], ["team", "Ekipa"], ["pos", "Poz."], ["gp", "GP", 0], ["goals", "G", 0], ["points", "P", 0], ["sog_g", "SOG/tek", 2], ["p_g", "T/tek", 2], ["toi", "TOI", 1], ["_next", "Naslednja"], ["_p1", "P(3+ SOG)"], ["_p2", "P(gol)"]],
  nba: [["name", "Igralec"], ["team", "Ekipa"], ["min", "Min", 1], ["pts", "Točke", 1], ["reb", "Skoki", 1], ["ast", "Podaje", 1], ["tpm", "3P", 1], ["pra", "T+S+P", 1], ["_next", "Naslednja"], ["_proj", "Proj. točke"]],
};
PCOLS.euroleague = [["name", "Igralec"], ["team", "Ekipa"], ["gp", "GP", 0], ["min", "Min", 1], ["pts", "Točke", 1], ["l5_pts", "Točke L5", 1],
  ["reb", "Skoki", 1], ["l5_reb", "Skoki L5", 1], ["ast", "Podaje", 1], ["l5_ast", "Podaje L5", 1], ["tpm", "3P", 1], ["l5_tpm", "3P L5", 1],
  ["pra", "T+S+P", 1], ["l5_pra", "T+S+P L5", 1], ["_next", "Naslednja"], ["_proj", "Proj. točke"]];
function plData(sport) {
  const pd = S.data.meta.player_data?.[sport];
  return pd ? pd : null;
}
function playerStatus(p) {
  const st = p.next?.status;
  return { out: ` <span class="badge red" data-tip="Ne igra (poškodba, kazen ali ni v postavi).">NE IGRA</span>`,
    doubt: ` <span class="badge amber" data-tip="Vprašljiv za tekmo.">?</span>`,
    start: ` <span class="badge" data-tip="V začetni postavi.">XI</span>`,
    bench: ` <span class="badge grey" data-tip="Na klopi.">KLOP</span>` }[st] || "";
}

function pval(p, k) {
  if (k === "_p1" || k === "_p2") {
    const lbl = { football: { _p1: "1+ strel v okvir", _p2: "2+ strela" }, nhl: { _p1: "Strelov na gol 3+", _p2: "Da gol" } }[S.players.sport]?.[k];
    return p.props?.find((r) => r.label === lbl)?.p ?? null;
  }
  if (k === "_proj") return p.next?.proj_pts ?? null;
  if (k.startsWith("l5_")) return p.l5?.[k.slice(3)] ?? null;
  if (k === "_next") return p.next ? p.next.kickoff : "";
  return p[k];
}
function viewPlayers(v) {
  const P = S.players;
  if (S.sport !== "all" && PCOLS[S.sport]) P.sport = S.sport;
  let list = (S.data.players[P.sport] || []).slice();
  const leagues = [...new Set(list.map((p) => p.league).filter(Boolean))];
  if (P.sport === "football") {
    if (P.league !== "all") list = list.filter((p) => p.league === P.league);
    list = list.filter((p) => p.min >= P.minMin);
  }
  if (P.q) { const q = P.q.toLowerCase(); list = list.filter((p) => (p.name + " " + p.team).toLowerCase().includes(q)); }
  const cols = PCOLS[P.sport];
  const sk = P.sort || { football: "xg90", nhl: "p_g", nba: "pts", euroleague: "pts" }[P.sport];
  list.sort((a, b) => {
    const x = pval(a, sk), y = pval(b, sk);
    if (typeof x === "string" || typeof y === "string") return P.dir * -String(x ?? "").localeCompare(String(y ?? ""));
    return P.dir * ((x ?? -1) - (y ?? -1));
  });
  const shown = list.slice(0, 200);
  const lgName = (c) => ({ E0: "Premier League", SP1: "LaLiga", D1: "Bundesliga", I1: "Serie A", F1: "Ligue 1" }[c] || c);
  v.innerHTML = `<h2>Lestvica igralcev</h2>
    <p class="section-note">${P.sport === "football" ? "Understat: tekoča sezona + pol uteži prejšnje sezone, na 90 minut. Verjetnosti upoštevajo nasprotnika, dom/gostovanje in pričakovane minute (brez potrjenih postav)." : P.sport === "nhl" ? "NHL stats API: na tekmo, tekoča sezona + pol uteži prejšnje. Verjetnosti upoštevajo strele, ki jih nasprotnik dopušča." : P.sport === "euroleague" ? (plData("euroleague")?.ok === false ? `<b>player data: none</b> – ${esc(plData("euroleague").reason || "")}. Prejšnje sezone ne uporabimo.` : `Uradni EuroLeague API (api-live.euroleague.net), samo sezona ${esc(plData("euroleague")?.season || "")}: povprečje sezone in zadnjih 5 tekem (L5). Verjetnosti: μ = (sezona + L5) / 2, točke prilagojene obrambi nasprotnika.`) : "ESPN box score: zadnjih 15 tekem igralca. Projekcija točk upošteva obrambo nasprotnika."}</p>
    <div class="filters">
      <label>Šport<select id="pSport">${Object.keys(PCOLS).map((k) => `<option value="${k}" ${P.sport === k ? "selected" : ""}>${SPORTS[k].name}</option>`).join("")}</select></label>
      ${P.sport === "football" ? `<label>Liga<select id="pLeague"><option value="all">Vse</option>${leagues.map((l) => `<option value="${l}" ${P.league === l ? "selected" : ""}>${lgName(l)}</option>`).join("")}</select></label>
      <label>Min. minut<input id="pMin" type="number" min="0" step="90" value="${P.minMin}" style="width:90px"></label>` : ""}
      <label>Išči<input id="pQ" value="${esc(P.q)}" placeholder="ime, ekipa…"></label>
    </div>
    <div class="card scroll" style="padding:4px 6px"><table class="tbl nowrap">
      <tr>${cols.map(([k, l, d]) => `<th class="sort ${d != null || k.startsWith("_p") ? "r" : ""} ${sk === k ? "act" : ""}" data-k="${k}">${l}${sk === k ? (P.dir < 0 ? " ↓" : " ↑") : ""}</th>`).join("")}</tr>
      ${shown.map((p) => `<tr>${cols.map(([k, , d]) => {
        if (k === "_next") return `<td class="muted" style="white-space:nowrap">${p.next ? `${p.next.home ? "vs" : "@"} ${esc(p.next.opp)} · ${fmtTime(p.next.kickoff)}${playerStatus(p)}` : "–"}</td>`;
        if (k === "_p1" || k === "_p2") { const val = pval(p, k); return `<td class="r">${val == null ? "–" : pbar(val)}</td>`; }
        if (k === "_proj") return `<td class="r num">${fmtNum(pval(p, k), 1)}</td>`;
        if (d != null) return `<td class="r num">${fmtNum(pval(p, k), d)}</td>`;
        return `<td>${esc(p[k] ?? "")}</td>`;
      }).join("")}</tr>`).join("")}
    </table>${!list.length ? `<div class="empty">Ni igralcev za te filtre.</div>` : ""}</div>`;
  $("#pSport").onchange = (e) => { P.sport = e.target.value; P.sort = null; if (S.sport !== "all") { S.sport = "all"; renderChips(); } render(); };
  if ($("#pLeague")) $("#pLeague").onchange = (e) => { P.league = e.target.value; render(); };
  if ($("#pMin")) $("#pMin").onchange = (e) => { P.minMin = +e.target.value || 0; render(); };
  $("#pQ").onchange = (e) => { P.q = e.target.value; render(); };
  v.querySelectorAll("th[data-k]").forEach((th) => th.onclick = () => {
    const k = th.dataset.k;
    if (P.sort === k || (!P.sort && sk === k)) P.dir = -P.dir; else { P.sort = k; P.dir = -1; }
    P.sort = k; render();
  });
}

/* ---------- view: bet365 list ---------- */
const LS_IN = "oddsroom.b365in", LS_MY = "oddsroom.mypicks";
function lsGet(k, d) { try { return JSON.parse(localStorage.getItem(k) || "null") ?? d; } catch (e) { return d; } }
function lsSet(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch (e) { return false; } }
const LS_BACKUP = "oddsroom.lastbackup", LS_TOKEN = "oddsroom.ghtoken";
const REPO = { owner: "ninaslova-boop", repo: "ODDS-ROOM", path: "data/user_picks.json", branch: "main" };
function localPicks() { return lsGet(LS_MY, []); }
/* the official record: picks saved in the repository (published with status) + picks only in this device */
function myPicks() {
  const repo = S.data.userPicks?.picks || [];
  const keys = new Set(repo.map((p) => p.key));
  return repo.concat(localPicks().filter((p) => !keys.has(p.key)));
}
function unsavedPicks() {
  const keys = new Set((S.data.userPicks?.picks || []).map((p) => p.key));
  const saved = new Set(lsGet("oddsroom.savedkeys", []));
  return localPicks().filter((p) => !keys.has(p.key) && !saved.has(p.key));
}
function backupWarning() {
  const n = unsavedPicks().length;
  if (!n) return "";
  const last = lsGet(LS_BACKUP, null);
  const days = last ? Math.floor((Date.now() - new Date(last).getTime()) / 86400e3) : null;
  return `<div class="warn-box"><b>NI VARNOSTNO SHRANJENO:</b> ${n} pickov je samo v tej napravi. ${days == null ? "Še nikoli shranjeno ali izvoženo." : `Zadnje shranjevanje / izvoz: pred ${days} dnevi.`} Uporabi <b>Shrani na GitHub</b> (uradni dnevnik) ali <b>Izvozi</b>.</div>`;
}
/* base64 for UTF-8 text */
const b64enc = (t) => btoa(unescape(encodeURIComponent(t)));
const b64dec = (t) => decodeURIComponent(escape(atob(t.replace(/\n/g, ""))));
async function saveToGitHub() {
  const token = lsGet(LS_TOKEN, null);
  if (!token) throw new Error("ni žetona (GitHub token)");
  const url = `https://api.github.com/repos/${REPO.owner}/${REPO.repo}/contents/${REPO.path}`;
  const hdr = { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json" };
  const cur = await fetch(`${url}?ref=${REPO.branch}`, { headers: hdr, cache: "no-store" });
  if (!cur.ok && cur.status !== 404) throw new Error(`GitHub ${cur.status}`);
  const j = cur.ok ? await cur.json() : null;
  const data = j ? JSON.parse(b64dec(j.content)) : { picks: [] };
  const have = new Set((data.picks || []).map((p) => p.key));
  const strip = ({ status, score, ...rest }) => rest;          // status is computed by the pipeline
  const add = localPicks().filter((p) => !have.has(p.key)).map(strip);
  data.picks = (data.picks || []).concat(add);
  data.updated = new Date().toISOString();
  const put = await fetch(url, { method: "PUT", headers: { ...hdr, "Content-Type": "application/json" },
    body: JSON.stringify({ message: `User bet365 picks: +${add.length} (app)`, content: b64enc(JSON.stringify(data, null, 1)),
      branch: REPO.branch, ...(j ? { sha: j.sha } : {}) }) });
  if (!put.ok) throw new Error(`GitHub ${put.status}: ${(await put.text()).slice(0, 120)}`);
  lsSet("oddsroom.savedkeys", [...new Set([...lsGet("oddsroom.savedkeys", []), ...localPicks().map((p) => p.key)])]);
  lsSet(LS_BACKUP, new Date().toISOString());
  return add.length;
}

function mainLine(x, scope) {
  const o = x.odds_raw || {};
  if (scope === "total") {
    if (x.sport === "football") return 2.5;
    if (o.total) return o.total;
    if (x.sport === "nhl") return [5.5, 6.5].sort((a, b) => Math.abs(lineProb(x, "total", "over", a) - 0.5) - Math.abs(lineProb(x, "total", "over", b) - 0.5))[0];
    return Math.round(x.exp[0] + x.exp[1]) + 0.5;
  }
  if (o.spread_home != null) return o.spread_home;
  let sl = -Math.round((x.exp[0] - x.exp[1]) * 2) / 2;
  if (sl === Math.trunc(sl)) sl -= 0.5;
  return sl;
}
function b365Specs(x) {
  if (x.sport === "football") return [["match", "1"], ["match", "X"], ["match", "2"], ["total", "over", 2.5], ["total", "under", 2.5], ["btts", "yes"], ["btts", "no"]];
  const t = mainLine(x, "total");
  const out = [["match", "1"], ["match", "2"], ["total", "over", t], ["total", "under", t]];
  if (x.sport !== "nhl") { const sl = mainLine(x, "spread"); out.push(["spread", "home", sl], ["spread", "away", -sl]); }
  return out;
}
function b365Rows() {
  const B = S.b365;
  const now = Date.now();
  const dayOk = (iso) => B.day === "all" || (B.day === "today" ? dayKey(iso) === todayKey(0) : B.day === "tomorrow" ? dayKey(iso) === todayKey(1) : true);
  const rows = [];
  for (const x of S.data.matches) {
    if ((S.sport !== "all" && x.sport !== S.sport) || new Date(x.kickoff).getTime() < now || !dayOk(x.kickoff) || isBad(x)) continue;
    if (B.q && !(x.home + " " + x.away + " " + x.league_name).toLowerCase().includes(B.q.toLowerCase())) continue;
    for (const [scope, kind, line] of b365Specs(x)) {
      const m = x.markets.find((k) => (k.stat === "goals" || k.stat === "points") && k.scope === scope && k.kind === kind && (line == null || k.line === line));
      const p = m ? m.p : lineProb(x, scope, kind, line);
      const pm = m?.p_model ?? (x.exp_model ? lineProb(x, scope, kind, line, x.exp_model) : p);
      if (p == null || p < B.minP || p > 0.95) continue;
      const mk = { stat: x.sport === "football" || x.sport === "nhl" ? "goals" : "points", scope, kind, line: line ?? null };
      rows.push({ key: `${x.id}|${scope}|${kind}`, x, ...mk, label: slLabel(mk, x), p, pm, b365: m?.b365 || null });
    }
  }
  return rows.sort((a, b) => a.x.kickoff.localeCompare(b.x.kickoff) || a.x.id.localeCompare(b.x.id));
}
/* p_final / p_model for the line the user entered */
function rowProb(r, line) {
  if (r.line == null || line == null || isNaN(line) || line === r.line) return { p: r.p, pm: r.pm };
  return { p: lineProb(r.x, r.scope, r.kind, line), pm: lineProb(r.x, r.scope, r.kind, line, r.x.exp_model || r.x.exp) };
}
function rowResult(r, inp) {
  const o = parseFloat(String(inp.o ?? "").replace(",", "."));
  const line = inp.l === "" || inp.l == null ? r.line : parseFloat(String(inp.l).replace(",", "."));
  const { p, pm } = rowProb(r, line);
  if (!o || o <= 1) return { p, pm, line, html: `<span class="faint">vnesi kvoto</span>`, ok: false };
  const ev = p * o, ok = ev > 1;
  return { p, pm, line, o, ev, ok, strong: ev >= STRONG_EV,
    html: `<b class="${ok ? "g" : "red"}">${ok ? "PICK" : "NO PICK"}</b>${ev >= STRONG_EV ? ` <span data-tip="p_final × kvota ≥ 1.10">◆</span>` : ""} <span class="num faint">${ev.toFixed(3)}</span>` };
}
function viewB365(v) {
  const B = S.b365;
  const rows = b365Rows();
  const inputs = lsGet(LS_IN, {});
  const logged = new Set(myPicks().map((p) => p.key));
  const lineCell = (r) => r.line == null ? "" : `<input class="b-in" data-l="${esc(r.key)}" inputmode="decimal" value="${esc(inputs[r.key]?.l ?? "")}" placeholder="${fmtLine(r.line)}" aria-label="bet365 linija">`;
  v.innerHTML = `<h2>bet365: kaj preveriti</h2>
    <p class="section-note">bet365 nima javnega API-ja, zato kvote preveriš sam. <b>Min. kvota</b> = 1 / p_final. Pick velja, ko je p_final × kvota bet365 &gt; 1.00 (◆ pri ≥ 1.10). Če ima bet365 drugo linijo, jo vpiši: p se izračuna za točno to linijo iz porazdelitve modela, že zmešane s trgom. Brez nezanesljivih tekem in tekem z DATA MISMATCH. Vnosi in tvoj dnevnik so shranjeni samo v tej napravi.</p>
    <div class="filters">
      <label>Dan<select id="bDay">${[["today", "Danes"], ["tomorrow", "Jutri"], ["all", "Vsi"]].map(([k, l]) => `<option value="${k}" ${B.day === k ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label>Min. p<select id="bMin">${[0.2, 0.35, 0.5].map((k) => `<option value="${k}" ${B.minP === k ? "selected" : ""}>${pct(k)}</option>`).join("")}</select></label>
      <label>Išči<input id="bQ" value="${esc(B.q)}" placeholder="ekipa, liga…"></label>
      <span class="spacer"></span><span class="count">${rows.length} vrstic</span>
    </div>
    <div class="card scroll" style="padding:4px 6px"><table class="tbl stack b365">
      <tr><th>Čas</th><th>Tekma</th><th>Trg</th><th class="r">p_final (model)</th><th class="r">Min. kvota</th><th>bet365 kvota</th><th>bet365 linija</th><th>Rezultat</th><th></th></tr>
      ${rows.map((r) => { const res = rowResult(r, { o: inputs[r.key]?.o ?? r.b365 ?? "", l: inputs[r.key]?.l });
        return `<tr data-row="${esc(r.key)}"><td class="muted" style="white-space:nowrap">${fmtTime(r.x.kickoff)}</td>
        <td>${SPORTS[r.x.sport]?.icon || ""} ${esc(r.x.home)} – ${esc(r.x.away)} <span class="faint">${esc(r.x.league_name)}</span>${validTag(r.x)}</td>
        <td>${esc(r.label)}</td><td class="r num" data-pf>${pct(res.p, 1)} <span class="faint">(${pct(res.pm)})</span></td>
        <td class="r num" data-min><span class="faint">min.</span> <b>${(1 / res.p).toFixed(2)}</b></td>
        <td><input class="b-in" data-o="${esc(r.key)}" inputmode="decimal" value="${esc(inputs[r.key]?.o ?? (r.b365 ? r.b365 : ""))}" placeholder="npr. 1.95" aria-label="bet365 kvota">${r.b365 && inputs[r.key]?.o == null ? ` <span class="faint" data-tip="Kvota bet365 iz football-data.co.uk. Preveri, ali še velja.">B365</span>` : ""}</td>
        <td>${lineCell(r)}</td><td data-res>${res.html}</td>
        <td><button class="btn s" data-log="${esc(r.key)}" ${res.ok && !logged.has(r.key) ? "" : "disabled"}>${logged.has(r.key) ? "Zabeleženo" : "Zabeleži"}</button></td></tr>`; }).join("")}
    </table>${!rows.length ? `<div class="empty"><b>Ni vrstic</b>Ni tekem za ta izbor (ali so vse nezanesljive / DATA MISMATCH).</div>` : ""}</div>
    ${myPicksBlock()}`;
  const byKey = Object.fromEntries(rows.map((r) => [r.key, r]));
  const update = (key) => {
    const r = byKey[key], tr = v.querySelector(`tr[data-row="${CSS.escape(key)}"]`);
    if (!r || !tr) return;
    const inp = lsGet(LS_IN, {})[key] || {};
    const res = rowResult(r, { o: inp.o ?? r.b365 ?? "", l: inp.l });
    tr.querySelector("[data-pf]").innerHTML = `${pct(res.p, 1)} <span class="faint">(${pct(res.pm)})</span>`;
    tr.querySelector("[data-min]").innerHTML = `<span class="faint">min.</span> <b>${(1 / res.p).toFixed(2)}</b>`;
    tr.querySelector("[data-res]").innerHTML = res.html;
    const btn = tr.querySelector("[data-log]");
    if (!logged.has(key)) btn.disabled = !res.ok;
  };
  v.querySelectorAll("input.b-in").forEach((el) => el.addEventListener("input", () => {
    const key = el.dataset.o || el.dataset.l;
    const all = lsGet(LS_IN, {});
    all[key] = { ...(all[key] || {}), [el.dataset.o ? "o" : "l"]: el.value.trim() };
    lsSet(LS_IN, all);
    update(key);
  }));
  v.querySelectorAll("[data-log]").forEach((b) => b.onclick = () => {
    const r = byKey[b.dataset.log];
    const inp = lsGet(LS_IN, {})[r.key] || {};
    const res = rowResult(r, { o: inp.o ?? r.b365 ?? "", l: inp.l });
    if (!res.ok) return;
    const list = localPicks();
    if (myPicks().some((p) => p.key === r.key)) return;
    list.push({ key: r.key, match: r.x.id, sport: r.x.sport, league: r.x.league_name, home: r.x.home, away: r.x.away,
      kickoff: r.x.kickoff, stat: r.stat, scope: r.scope, kind: r.kind, line: res.line ?? null, label: slLabel({ ...r, line: res.line }, r.x),
      p_final: +res.p.toFixed(4), p_model: +res.pm.toFixed(4), w: r.x.blend?.w ?? 1, odds: res.o, odds_src: "bet365 (vnos)",
      ev: +res.ev.toFixed(4), strong: res.strong, logged: new Date().toISOString(), status: "open" });
    lsSet(LS_MY, list);
    if (lsGet(LS_TOKEN, null)) saveToGitHub().then(() => render()).catch((e) => { render(); $("#myMsg").textContent = "Shranjevanje ni uspelo: " + e.message; });
    render();
  });
  $("#bDay").onchange = (e) => { B.day = e.target.value; render(); };
  $("#bMin").onchange = (e) => { B.minP = +e.target.value; render(); };
  $("#bQ").onchange = (e) => { B.q = e.target.value; render(); };
  wireMyPicks(v);
}

/* the user's bet365 picks: settled in the browser with data/results.json */
function settleMyPicks() {
  const list = localPicks();
  let changed = false;
  for (const p of list) {
    if (p.status !== "open") continue;
    const r = S.data.results[p.match];
    const v = r && (r[p.stat] || r.goals || r.points);
    if (!v || v[0] == null) continue;
    const res = settleMk(p.sport, p.scope, p.kind, p.line, v[0], v[1]);
    p.status = res === 1 ? "won" : res === 0 ? "lost" : "push";
    p.score = `${v[0]}-${v[1]}`;
    changed = true;
  }
  if (changed) lsSet(LS_MY, list);
  return myPicks();
}
function pickSummary(list) {
  const d = list.filter((p) => p.status === "won" || p.status === "lost");
  const won = d.filter((p) => p.status === "won").length;
  const profit = d.reduce((a, p) => a + (p.status === "won" ? p.odds - 1 : -1), 0);
  return { n: list.length, open: list.filter((p) => p.status === "open").length, won, lost: d.length - won,
    hit: d.length ? won / d.length : null, exp: d.length ? d.reduce((a, p) => a + p.p_final, 0) / d.length : null,
    profit, roi: d.length ? profit / d.length : null };
}
function myPicksBlock() {
  const list = settleMyPicks().slice().sort((a, b) => b.kickoff.localeCompare(a.kickoff));
  const s = pickSummary(list);
  const groups = {};
  for (const p of list) (groups[`${SPORTS[p.sport]?.name || p.sport} · w=${p.w}`] ||= []).push(p);
  const sb = (st) => ({ won: `<span class="badge">ZADETEK</span>`, lost: `<span class="badge red">ZGREŠEN</span>`, push: `<span class="badge grey">VRAČILO</span>`, open: `<span class="badge amber">ODPRT</span>` }[st] || st);
  return `<h2 style="margin-top:28px">Uradni dnevnik (moji bet365 picki)</h2>
    <p class="section-note">To je uradni zapis: picki s kvoto bet365, ki si jo vnesel. <b>Shrani na GitHub</b> jih zapiše v repozitorij (data/user_picks.json), poravna jih pipeline. Samodejni picki modela so ločen »model shadow log« v zavihku Dnevnik.</p>
    ${backupWarning()}
    <div class="kpis">
      <div class="kpi"><div class="l">Picki</div><div class="v">${s.n}</div><div class="faint" style="font-size:12px">${s.open} odprtih</div></div>
      <div class="kpi"><div class="l">Z – P</div><div class="v">${s.won}–${s.lost}</div></div>
      <div class="kpi"><div class="l">Zadetki</div><div class="v">${pct(s.hit, 1)}</div><div class="faint" style="font-size:12px">pričakovano ${pct(s.exp, 1)}</div></div>
      <div class="kpi"><div class="l">Profit</div><div class="v ${s.profit > 0 ? "g" : s.profit < 0 ? "red" : ""}">${s.profit > 0 ? "+" : ""}${s.profit.toFixed(2)}u</div><div class="faint" style="font-size:12px">ROI ${s.roi == null ? "–" : (s.roi * 100).toFixed(1) + " %"}</div></div>
    </div>
    ${Object.keys(groups).length ? `<div class="card scroll"><h3 style="margin-top:0">Po športu in w</h3><table class="tbl"><tr><th>Skupina</th><th class="r">Picki</th><th class="r">Z–P</th><th class="r">Zadetki</th><th class="r">Pričakovano</th><th class="r">Profit</th></tr>
      ${Object.entries(groups).map(([g, l]) => { const k = pickSummary(l); return `<tr><td>${esc(g)}</td><td class="r num">${k.n}</td><td class="r num">${k.won}–${k.lost}</td><td class="r num">${pct(k.hit, 1)}</td><td class="r num">${pct(k.exp, 1)}</td><td class="r num">${k.profit.toFixed(2)}u</td></tr>`; }).join("")}</table></div>` : ""}
    <div class="card scroll" style="padding:4px 6px"><table class="tbl">
      <tr><th>Čas</th><th>Tekma</th><th>Pick</th><th class="r">p_final (model)</th><th class="r">bet365</th><th class="r">p × kvota</th><th>Status</th><th></th></tr>
      ${list.map((p, i) => `<tr><td class="muted" style="white-space:nowrap">${fmtTime(p.kickoff)}</td><td>${SPORTS[p.sport]?.icon || ""} ${esc(p.home)} – ${esc(p.away)}</td><td>${esc(p.label)}${p.strong ? " ◆" : ""}</td>
        <td class="r num">${pct(p.p_final, 1)} <span class="faint">(${pct(p.p_model)})</span></td><td class="r num">${odds(p.odds)}</td><td class="r num">${p.ev.toFixed(3)}</td>
        <td>${sb(p.status)}${p.score ? ` <span class="faint">${esc(p.score)}</span>` : ""}</td><td><button class="btn o s" data-del="${esc(p.key)}" title="Odstrani">×</button></td></tr>`).join("")}
    </table>${!list.length ? `<div class="empty">Še ni zabeleženih pickov.</div>` : ""}</div>
    <div class="row" style="margin-top:8px"><button class="btn" id="mySave">Shrani na GitHub</button><button class="btn o" id="myExport">Izvozi</button><button class="btn o" id="myImport">Uvozi</button><button class="btn o" id="myToken">${lsGet(LS_TOKEN, null) ? "Žeton ✓" : "Nastavi žeton"}</button><span class="muted small" id="myMsg"></span></div>`;
}
function wireMyPicks(v) {
  v.querySelectorAll("[data-del]").forEach((b) => b.onclick = () => {
    if ((S.data.userPicks?.picks || []).some((p) => p.key === b.dataset.del)) { alert("Ta pick je v uradnem dnevniku na GitHubu in ga ni mogoče odstraniti iz aplikacije."); return; }
    if (!confirm("Odstranim ta pick iz dnevnika?")) return;
    lsSet(LS_MY, localPicks().filter((p) => p.key !== b.dataset.del)); render();
  });
  const ex = $("#myExport"), im = $("#myImport"), sv = $("#mySave"), tk = $("#myToken");
  if (ex) ex.onclick = () => navigator.clipboard?.writeText(JSON.stringify(myPicks())).then(() => { lsSet(LS_BACKUP, new Date().toISOString()); $("#myMsg").textContent = "Kopirano v odložišče."; });
  if (tk) tk.onclick = () => {
    const t = prompt("GitHub fine-grained token (samo repozitorij ODDS-ROOM, dovoljenje Contents: Read and write). Shrani se samo v tej napravi. Prazno = izbriši.");
    if (t === null) return;
    t.trim() ? lsSet(LS_TOKEN, t.trim()) : localStorage.removeItem(LS_TOKEN);
    render();
  };
  if (sv) sv.onclick = () => { $("#myMsg").textContent = "Shranjujem…";
    saveToGitHub().then((n) => { $("#myMsg").textContent = `Shranjeno (${n} novih). Na strani bo vidno po naslednji posodobitvi.`; })
      .catch((e) => { $("#myMsg").textContent = "Napaka: " + e.message; }); };
  if (im) im.onclick = () => {
    const t = prompt("Prilepi izvožen dnevnik (JSON):");
    if (!t) return;
    try {
      const add = JSON.parse(t);
      if (!Array.isArray(add)) throw new Error("ni seznam");
      const have = new Set(myPicks().map((p) => p.key));
      lsSet(LS_MY, localPicks().concat(add.filter((p) => p && p.key && !have.has(p.key) && p.odds > 1)));
      render();
    } catch (e) { $("#myMsg").textContent = "Napaka: " + e.message; }
  };
}

/* ---------- view: backtest (data/validation.json) ---------- */
function viewBacktest(v) {
  const val = S.data.validation || {};
  const shown = new Map();
  for (const x of S.data.matches) if (!shown.has(x.league)) shown.set(x.league, { name: x.league_name, sport: x.sport });
  const keys = [...new Set([...Object.keys(val), ...shown.keys()])]
    .filter((k) => S.sport === "all" || (val[k]?.sport || shown.get(k)?.sport) === S.sport || (S.sport === "football" && val[k]?.sport === "football"));
  const roiTxt = (r) => r == null ? "–" : `${r > 0 ? "+" : ""}${(r * 100).toFixed(1)} %`;
  const rows = keys.map((k) => {
    const e = val[k];
    const nm = e?.name || shown.get(k)?.name || k;
    if (!e) return `<tr><td>${esc(nm)}</td><td colspan="8"><span class="badge grey">ni validirano</span> <span class="faint">backtest še ni narejen</span></td></tr>`;
    const b = e.by_w[String(e.prod_w)] || Object.values(e.by_w)[0];
    const grid = Object.values(e.by_w).map((x) => `w=${x.w}: ${x.picks} pickov, ROI ${roiTxt(x.roi)}, Brier ${fmtNum(x.brier_final, 3)}`).join("<br>");
    return `<tr><td>${esc(nm)} ${valEntry(k).ok ? "" : `<span class="badge grey">ni validirano</span>`}<div class="faint" style="font-size:12px">${esc(e.from)} – ${esc(e.to)}</div></td>
      <td class="r num">${e.games}</td><td class="r num">${fmtNum(b.brier_model, 3)}</td><td class="r num">${fmtNum(b.brier_final, 3)}</td><td class="r num">${fmtNum(b.brier_market, 3)}</td>
      <td class="r num">${b.picks}</td><td class="r num">${pct(b.hit, 1)} / ${pct(b.expected, 1)}</td><td class="r num" data-tip="${esc(grid)}">${roiTxt(b.roi)}</td>
      <td class="faint" style="font-size:12px">w=${b.w} · ${esc(e.odds_src)}${e.note ? `<br>${esc(e.note)}` : ""}</td></tr>`;
  }).join("");
  v.innerHTML = `<h2>Backtest po tekmovanjih</h2>
    <p class="section-note">Walk-forward: model se vsakih 14 dni ponovno nauči samo iz preteklih tekem in napove naslednjih 14 dni. p_final = w × model + (1 − w) × trg brez marže (zaključne kvote). Pick = najboljši izid s p_final × kvota &gt; 1. Brier: nižje je bolje (model / p_final / trg). Zadetki: dejansko / pričakovano. ROI pri zaključnih kvotah. Tekmovanje brez backtesta, z manj kot 150 tekmami ali brez zgodovinskih kvot je označeno <b>ni validirano</b>. Podrži na ROI za druge w.</p>
    <div class="card scroll" style="padding:4px 6px"><table class="tbl stack">
      <tr><th>Tekmovanje</th><th class="r">Tekme</th><th class="r">Brier model</th><th class="r">Brier p_final</th><th class="r">Brier trg</th><th class="r">Picki</th><th class="r">Zadetki / pričak.</th><th class="r">ROI</th><th>Kvote</th></tr>
      ${rows}
    </table>${!keys.length ? `<div class="empty">Ni podatkov.</div>` : ""}</div>`;
}

/* ---------- view: pick log ---------- */
function viewLog(v) {
  const all = (S.data.picks.picks || []).filter((p) => S.sport === "all" || p.sport === S.sport);
  const st = S.data.picks.stats || {};
  const sum = S.sport === "all" ? st.all : st.by_sport?.[S.sport];
  const k = sum || {};
  const list = all.filter((p) => S.log.filter === "all" || (S.log.filter === "open" ? p.status === "open" : p.status !== "open"))
    .sort((a, b) => b.kickoff.localeCompare(a.kickoff));
  const statusBadge = (s) => ({ won: `<span class="badge">ZADETEK</span>`, lost: `<span class="badge red">ZGREŠEN</span>`, push: `<span class="badge grey">VRAČILO</span>`, void: `<span class="badge grey">RAZVELJ.</span>`, open: `<span class="badge amber">ODPRT</span>` }[s] || s);
  v.innerHTML = `<h2>Model shadow log (samodejni picki)</h2>
    <div class="warn-box">To ni uradni dnevnik. Uradni zapis so tvoji picki s kvoto bet365 (zavihek bet365 → Uradni dnevnik). Tukaj model sam beleži, kaj bi izbral.</div>
    <p class="section-note">Samodejni dnevnik: en pick na tekmo, ko je p_final × kvota bet365 &gt; 1.00 (kvota bet365 iz football-data.co.uk; povprečje stavnic se nikoli ne shrani kot cena). ◆ = p × kvota ≥ 1.10. Nezanesljive tekme in DATA MISMATCH ne dajo picka. Starejši picki (pred 16. 10. 2026) so po starem pravilu. Nič se ne briše. Vložek 1 enota. Svoje picke z vnesenimi kvotami bet365 vodiš v zavihku bet365.</p>
    <div class="kpis">
      <div class="kpi"><div class="l">Picki</div><div class="v">${k.n ?? 0}</div><div class="faint" style="font-size:12px">${k.open ?? 0} odprtih</div></div>
      <div class="kpi"><div class="l">Z – P – V</div><div class="v">${k.won ?? 0}–${k.lost ?? 0}–${k.push ?? 0}</div></div>
      <div class="kpi"><div class="l">Zadetki</div><div class="v">${pct(k.hit, 1)}</div><div class="faint" style="font-size:12px">model je čakal ${pct(k.avg_p, 1)}</div></div>
      <div class="kpi"><div class="l">Profit</div><div class="v ${k.profit > 0 ? "g" : k.profit < 0 ? "red" : ""}">${k.profit > 0 ? "+" : ""}${fmtNum(k.profit ?? 0, 2)}u</div></div>
      <div class="kpi"><div class="l">ROI</div><div class="v">${k.roi == null ? "–" : (k.roi > 0 ? "+" : "") + (k.roi * 100).toFixed(1) + "%"}</div><div class="faint" style="font-size:12px">povp. kvota ${odds(k.avg_odds)}</div></div>
    </div>
    <div class="grid2">
      <div class="card chart"><h3 style="margin-top:0">Profit skozi čas (enote)</h3>${profitChart(all)}</div>
      <div class="card chart"><h3 style="margin-top:0">Kalibracija modela</h3>${calibChart(st.calibration || [])}
        <div class="faint" style="font-size:12px;margin-top:6px">Točka na diagonali = model je natančen. ${st.tracked_n ? `${st.tracked_n} ocen, Brier ${fmtNum(st.tracked_brier, 3)}.` : "Še ni zaključenih ocen."}</div></div>
      <div class="card chart"><h3 style="margin-top:0">Kalibracija: samo picki</h3>${calibChart(st.picks_calibration || [])}
        <div class="faint" style="font-size:12px;margin-top:6px">Picki so izbrani tam, kjer se model najbolj razlikuje od trga. Če so točke pod diagonalo, model tam precenjuje (pristranost izbire).</div></div>
      ${st.by_w && Object.keys(st.by_w).length ? `<div class="card scroll"><h3 style="margin-top:0">Picki po športu in w</h3><table class="tbl"><tr><th>Šport</th><th>w</th><th class="r">Picki</th><th class="r">Zadetki</th><th class="r">Pričakovano</th><th class="r">Profit</th></tr>
        ${Object.entries(st.by_w).flatMap(([sp, ws]) => Object.entries(ws).map(([w, k]) => `<tr><td>${SPORTS[sp]?.name || sp}</td><td>${esc(w)}</td><td class="r num">${k.n}</td><td class="r num">${pct(k.hit, 1)}</td><td class="r num">${pct(k.avg_p, 1)}</td><td class="r num">${fmtNum(k.profit, 2)}u</td></tr>`)).join("")}</table></div>` : ""}
    </div>
    ${Object.keys(st.by_sport || {}).length ? `<div class="card scroll"><h3 style="margin-top:0">Po športih</h3><table class="tbl"><tr><th>Šport</th><th class="r">Picki</th><th class="r">Z–P</th><th class="r">Zadetki</th><th class="r">Profit</th><th class="r">ROI</th></tr>
      ${Object.entries(st.by_sport).map(([sp, s]) => `<tr><td>${SPORTS[sp]?.icon || ""} ${SPORTS[sp]?.name || sp}</td><td class="r num">${s.n}</td><td class="r num">${s.won}–${s.lost}</td><td class="r num">${pct(s.hit, 1)}</td><td class="r num">${fmtNum(s.profit, 2)}u</td><td class="r num">${s.roi == null ? "–" : (s.roi * 100).toFixed(1) + "%"}</td></tr>`).join("")}</table></div>` : ""}
    <div class="filters"><label>Prikaži<select id="lF"><option value="all">Vse</option><option value="open" ${S.log.filter === "open" ? "selected" : ""}>Odprte</option><option value="done" ${S.log.filter === "done" ? "selected" : ""}>Zaključene</option></select></label></div>
    <div class="card scroll" style="padding:4px 6px"><table class="tbl">
      <tr><th>Dan</th><th>Tekma</th><th>Pick</th><th class="r">Model</th><th class="r">Kvota</th><th class="r">Prednost</th><th>Status</th></tr>
      ${list.map((p) => `<tr><td class="muted" style="white-space:nowrap">${p.day}</td><td>${SPORTS[p.sport]?.icon || ""} ${esc(p.home)} – ${esc(p.away)}</td>
        <td>${esc(slLabel(p, p))}${p.strong ? " ◆" : ""}${p.odds_src ? "" : ` <span class="faint" data-tip="Staro pravilo: povprečna kvota.">staro</span>`}</td><td class="r num">${pct(p.p)}${p.p_model != null ? ` <span class="faint">(${pct(p.p_model)})</span>` : ""}</td><td class="r num">${odds(p.odds)}${p.odds_assumed ? "~" : ""}</td><td class="r">${edgeCell(p.edge, p.odds_assumed)}</td><td>${statusBadge(p.status)}${p.void_reason ? ` <span class="faint" data-tip="${esc(p.void_reason)}">ⓘ</span>` : ""}${alertBadge(p)}</td></tr>`).join("")}
    </table>${!list.length ? `<div class="empty"><b>Dnevnik je prazen</b>Picki se zabeležijo samodejno ob naslednjih zagonih.</div>` : ""}</div>`;
  $("#lF").onchange = (e) => { S.log.filter = e.target.value; render(); };
}

function alertBadge(p) {
  if (!p.alert) return "";
  return ` <span class="badge ${p.alert.level === "red" ? "red" : "amber"}" data-tip="${esc(p.alert.text)}">${p.alert.level === "red" ? "🟥" : "🟨"} ALERT</span>`;
}

function profitChart(picks) {
  const done = picks.filter((p) => ["won", "lost", "push"].includes(p.status)).sort((a, b) => a.kickoff.localeCompare(b.kickoff));
  if (done.length < 2) return `<div class="empty" style="padding:20px">Premalo zaključenih pickov.</div>`;
  let cum = 0;
  const pts = done.map((p) => { cum += p.status === "won" ? p.odds - 1 : p.status === "lost" ? -1 : 0; return { y: cum, p }; });
  const W = 420, H = 180, pl = 34, pr = 10, pt = 10, pb = 22;
  const ys = pts.map((d) => d.y).concat(0);
  const lo = Math.min(...ys), hi = Math.max(...ys), span = hi - lo || 1;
  const X = (i) => pl + (i / (pts.length - 1)) * (W - pl - pr);
  const Y = (y) => pt + (1 - (y - lo) / span) * (H - pt - pb);
  const path = pts.map((d, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(d.y).toFixed(1)}`).join("");
  const ticks = [lo, (lo + hi) / 2, hi];
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Kumulativni profit">
    ${ticks.map((t) => `<line class="grid" x1="${pl}" x2="${W - pr}" y1="${Y(t)}" y2="${Y(t)}"/><text x="${pl - 6}" y="${Y(t) + 4}" text-anchor="end">${t.toFixed(1)}</text>`).join("")}
    <line class="axis" x1="${pl}" x2="${W - pr}" y1="${Y(0)}" y2="${Y(0)}"/>
    <path d="${path}" fill="none" stroke="var(--green)" stroke-width="2" stroke-linejoin="round"/>
    ${pts.map((d, i) => `<circle cx="${X(i)}" cy="${Y(d.y)}" r="9" fill="transparent" data-tip="${esc(`${d.p.day} · ${d.p.home} – ${d.p.away}<br>${slLabel(d.p, d.p)} @ ${odds(d.p.odds)}: ${d.p.status}<br>Skupaj: <b>${d.y.toFixed(2)}u</b>`)}"/>`).join("")}
    <circle cx="${X(pts.length - 1)}" cy="${Y(cum)}" r="4" fill="var(--green)"/>
    <text x="${W - pr}" y="${H - 6}" text-anchor="end">${done.length} pickov</text>
  </svg>`;
}

function calibChart(buckets) {
  if (!buckets.length) return `<div class="empty" style="padding:20px">Še ni podatkov.</div>`;
  const W = 300, H = 220, pl = 34, pr = 18, pt = 10, pb = 28;
  const X = (v) => pl + v * (W - pl - pr), Y = (v) => pt + (1 - v) * (H - pt - pb);
  const maxN = Math.max(...buckets.map((b) => b.n));
  const t = [0, 0.25, 0.5, 0.75, 1];
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Kalibracija: napovedano proti dejanskemu">
    ${t.map((v) => `<line class="grid" x1="${pl}" x2="${W - pr}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${pl - 6}" y="${Y(v) + 4}" text-anchor="end">${v * 100}%</text><text x="${X(v)}" y="${H - 12}" text-anchor="middle">${v * 100}%</text>`).join("")}
    <line x1="${X(0)}" y1="${Y(0)}" x2="${X(1)}" y2="${Y(1)}" stroke="var(--grey)" stroke-dasharray="4 4" stroke-width="1"/>
    ${buckets.map((b) => `<circle cx="${X(b.pred)}" cy="${Y(b.act)}" r="${4 + 6 * Math.sqrt(b.n / maxN)}" fill="var(--green)" fill-opacity=".85" stroke="var(--surface)" stroke-width="2"
       data-tip="Napovedano ${pct(b.pred, 1)}<br>Dejansko ${pct(b.act, 1)}<br>${b.n} ocen"/>`).join("")}
    <text x="${W - pr}" y="${H - 1}" text-anchor="end">napovedano →</text>
  </svg>`;
}

/* ---------- view: post generator ---------- */
function evKey(e) {
  return e.kind === "team" ? `t|${e.match.id}|${e.m.stat}|${e.m.scope}|${e.m.kind}|${e.m.line}` : `p|${e.match.id}|${e.player.name}|${e.prop.label}`;
}
/* no pick labels or tiers: one pick format (◆ when p_final x bet365 >= 1.10), a multi-leg format and the trap check */
const TYPES = {
  pick: { name: "Pick", head: "🎯 PICK" },
  acca: { name: "Acca / Builder", head: "🧱 ACCA" },
  trap: { name: "Trap + FLIP", head: "🪤 TRAP + FLIP" },
};
function autoType(e, list) {
  return list.length > 1 ? "acca" : "pick";
}

/* short-priced favourites the model does not back */
function traps() {
  const out = [];
  for (const x of sportMatches()) {
    for (const m of x.markets) {
      if (!m.odds || m.odds > 1.7 || m.odds_assumed) continue;
      const implied = 1 / m.odds;
      if (implied - m.p < 0.06) continue;
      const flip = flipOf(m, x);
      out.push({ x, m, implied, flip });
    }
  }
  return out.sort((a, b) => (b.implied - b.m.p) - (a.implied - a.m.p));
}
function flipOf(m, x) {
  const find = (scope, kind, line) => x.markets.find((k) => k.stat === m.stat && k.scope === scope && k.kind === kind && (line === undefined || k.line === line));
  if (m.scope === "match") {
    if (x.sport === "football") return find("match", m.kind === "1" ? "X2" : m.kind === "2" ? "1X" : "12");
    return find("match", m.kind === "1" ? "2" : "1");
  }
  if (m.scope === "total") return find("total", m.kind === "over" ? "under" : "over", m.line);
  if (m.scope === "btts") return find("btts", m.kind === "yes" ? "no" : "yes");
  if (m.scope === "spread") return find("spread", m.kind === "home" ? "away" : "home", -m.line);
  return null;
}

function whyLines(e) {
  const x = e.match, st = x.stats || {}, isBall = x.sport === "nba" || x.sport === "euroleague";
  const form = (f) => (f || []).map((g) => g.r).join("");
  const L = [];
  if (e.kind === "player") {
    const p = e.player;
    if (e.sport === "football") L.push(`${p.name}: ${fmtNum(p.sh90, 1)} shots / 90, ${fmtNum(p.xg90, 2)} xG / 90`, `Projected: ${p.next.exp_shots} shots, ${p.next.exp_sot} on target in ~${p.next.exp_min} min`);
    else if (e.sport === "nhl") L.push(`${p.name}: ${fmtNum(p.sog_g, 1)} shots on goal / game, ${fmtNum(p.p_g, 2)} points / game`, `Projected: ${p.next.exp_sog} shots on goal`);
    else L.push(`${p.name}: ${p.pts} pts, ${p.reb} reb, ${p.ast} ast (last ${p.gp})`, `Projected: ${p.next.proj_pts} points`);
    L.push(`Opponent: ${p.next.opp}`);
    return L;
  }
  if (x.sport === "football") {
    if (st.home?.gp) L.push(`${x.home}: ${st.home.gf} scored / ${st.home.ga} conceded per game · form ${form(x.form?.home)}`);
    if (st.away?.gp) L.push(`${x.away}: ${st.away.gf} scored / ${st.away.ga} conceded per game · form ${form(x.form?.away)}`);
    L.push(`Model xG: ${fmtNum(x.exp[0])} – ${fmtNum(x.exp[1])} · most likely total: ${x.total_mode}`);
  } else {
    const u = isBall ? "pts" : "goals";
    if (st.home?.gp) L.push(`${x.home}: ${st.home.for} ${u} for / ${st.home.against} against · form ${form(x.form?.home)}`);
    if (st.away?.gp) L.push(`${x.away}: ${st.away.for} ${u} for / ${st.away.against} against · form ${form(x.form?.away)}`);
    L.push(`Model: ${fmtNum(x.exp[0], 1)} – ${fmtNum(x.exp[1], 1)}`);
  }
  return L;
}

function evEn(e) {
  return e.kind === "team" ? enLabel(e.m, e.match) : `${e.player.name} ${PROP_EN[e.prop.stat] ? PROP_EN[e.prop.stat](e.prop.line) : e.prop.label}`;
}

function buildPost(list, type, trap) {
  const t = TYPES[type];
  const when = (iso) => new Date(iso).toLocaleString("en-GB", { weekday: "short", hour: "2-digit", minute: "2-digit" });
  const foot = `\nOur estimate. Not a promise.\n18+ · Play for fun. Never chase losses.\n#TheOddsRoom`;
  if (type === "trap" && trap) {
    const { x, m, implied, flip } = trap;
    return `${t.head}\n${SPORTS[x.sport].en} ${x.home} v ${x.away}\n🏆 ${x.league_name} · ${when(x.kickoff)}\n\n` +
      `Looks safe: ${enLabel(m, x)} @ ${odds(m.odds)}\nBook implies ${pct(implied)}. We say ${pct(m.p)}.\nCheap odds ≠ safe odds.\n` +
      (flip ? `\nThe FLIP: ${enLabel(flip, x)}${flip.odds ? ` @ ${odds(flip.odds)}` : ` (fair ${odds(1 / flip.p)})`} · Est. ${pct(flip.p)}\n` : "") +
      `\nThe read:\n${whyLines({ kind: "team", match: x }).map((l) => "• " + l).join("\n")}\n${foot}`;
  }
  if (type === "acca") {
    const sameGame = new Set(list.map((e) => e.match.id)).size === 1;
    const pOdds = list.every((e) => e.odds) ? list.reduce((a, e) => a * e.odds, 1) : null;
    const pP = list.reduce((a, e) => a * e.p, 1);
    return `${sameGame ? "🧱 BUILDER" : "🧱 ACCA"}\n${sameGame ? `${SPORTS[list[0].sport].en} ${list[0].match.home} v ${list[0].match.away}\n` : ""}\n` +
      list.map((e) => `• ${sameGame ? "" : `${e.match.home} v ${e.match.away}: `}${evEn(e)}${e.odds ? ` @ ${odds(e.odds)}` : ""} (${pct(e.p)})`).join("\n") +
      `\n\n${pOdds ? `Total odds: ${pOdds.toFixed(2)}\n` : ""}Est. %: ${pct(pP, 1)}${sameGame ? " (legs in one game are linked – treat as rough)" : ""}\nOne miss sinks it. Small stakes only.\n${foot}`;
  }
  const e = list[0];
  const x = e.match;
  const b365 = e.m?.b365;
  const ev = b365 ? e.p * b365 : null;
  const head = ev != null && ev >= STRONG_EV ? `${t.head} ◆` : t.head;
  const price = b365 ? `bet365: ${odds(b365)} · p × odds ${ev.toFixed(2)}${ev > 1 ? "" : " → no pick at this price"}`
    : `Min. odds: ${(1 / e.p).toFixed(2)} (check bet365)`;
  return `${head}\n${SPORTS[x.sport].en} ${x.home} v ${x.away}\n🏆 ${x.league_name} · ${when(x.kickoff)}\n\n` +
    `Pick: ${evEn(e)}\n${price}\nEst. %: ${pct(e.p)}\n\nThe read:\n${whyLines(e).map((l) => "• " + l).join("\n")}\n` + foot;
}

function buildAlertPost(p) {
  const x = S.data.byId[p.match] || p;
  const red = p.alert.level === "red";
  const miss = x.adj ? Object.values(x.adj.missing || {}).flat().slice(0, 4).join(", ") : "";
  return `🚨 LINEUP ALERT ${red ? "🟥" : "🟨"}\n${SPORTS[p.sport]?.en || ""} ${p.home} v ${p.away}\n\n` +
    `Pick: ${enLabel(p, p)} @ ${odds(p.odds)}\nWas: ${pct(p.p)} · Now: ${pct(p.p_now)}\n` +
    (miss ? `Out: ${miss}\n` : "") +
    (red ? "\nRed card: pick pulled. No value at this price now.\n" : "\nYellow card: still on, lower confidence.\n") +
    `\n18+ · Play for fun. Never chase losses.\n#TheOddsRoom`;
}

function viewPosts(v) {
  const events = allEvents();
  const byKey = Object.fromEntries(events.map((e) => [evKey(e), e]));
  S.post.selected = S.post.selected.filter((k) => byKey[k]);
  const sel = S.post.selected.map((k) => byKey[k]);
  const vals = valueEvents().slice(0, 40);
  const strong = events.filter((e) => e.p >= 0.6 && e.p <= 0.88).sort((a, b) => b.p - a.p).slice(0, 40);
  const tr = traps().slice(0, 15);
  const type = S.post.trapIdx != null ? "trap" : (S.post.type === "auto" || !TYPES[S.post.type] ? (sel[0] ? autoType(sel[0], sel) : "pick") : S.post.type);
  const alerts = (S.data.picks.picks || []).filter((p) => p.alert && p.status === "open");
  let text = S.post.trapIdx != null && tr[S.post.trapIdx] ? buildPost([], "trap", tr[S.post.trapIdx]) : (sel.length ? buildPost(sel, type) : "");
  if (S.post.alertIdx != null && alerts[S.post.alertIdx]) text = buildAlertPost(alerts[S.post.alertIdx]);
  const item = (e) => { const k = evKey(e); return `<div class="pick-item ${S.post.selected.includes(k) ? "on" : ""}" data-k="${esc(k)}"><input type="checkbox" ${S.post.selected.includes(k) ? "checked" : ""}>
    <div style="flex:1"><div>${SPORTS[e.sport]?.icon || ""} ${esc(e.title)}</div><div class="faint" style="font-size:12px">${esc(e.sub)} · ${fmtTime(e.kickoff)}</div></div>
    <div class="r num"><b class="g">${pct(e.p)}</b>${e.odds ? `<div class="faint" style="font-size:12px">@ ${odds(e.odds)}</div>` : ""}</div></div>`; };
  v.innerHTML = `<h2>Generator objav</h2>
    <p class="section-note">Izberi en dogodek (ali več za Acca / Builder). Brez oznak in stopenj: samo Pick, ◆ pri p_final × bet365 ≥ 1.10. Besedilo je v angleščini.</p>
    <div class="grid2">
      <div>
        <h3>Kandidati (zanesljive tekme)</h3><div class="pick-list">${vals.map(item).join("") || `<div class="empty" style="padding:16px">Ni kandidatov.</div>`}</div>
        <h3>Verjetni dogodki (60–88 %)</h3><div class="pick-list">${strong.map(item).join("")}</div>
        ${alerts.length ? `<h3>Lineup alerti</h3><div class="pick-list">${alerts.map((p, i) => `<div class="pick-item ${S.post.alertIdx === i ? "on" : ""}" data-alert="${i}"><div style="flex:1"><div>${p.alert.level === "red" ? "🟥" : "🟨"} ${esc(slLabel(p, p))} @ ${odds(p.odds)}</div><div class="faint" style="font-size:12px">${esc(p.home)} – ${esc(p.away)} · ${esc(p.alert.text)}</div></div></div>`).join("")}</div>` : ""}
        <h3>Trap radar <span class="badge red">+ FLIP</span></h3>
        <div class="pick-list">${tr.map((t, i) => `<div class="pick-item ${S.post.trapIdx === i ? "on" : ""}" data-trap="${i}"><div style="flex:1"><div>${esc(slLabel(t.m, t.x))} @ ${odds(t.m.odds)}</div><div class="faint" style="font-size:12px">${esc(t.x.home)} – ${esc(t.x.away)} · kvota pravi ${pct(t.implied)}, model ${pct(t.m.p)}</div></div></div>`).join("") || `<div class="empty" style="padding:16px">Ni pasti: kratke kvote se ujemajo z modelom.</div>`}</div>
      </div>
      <div>
        <h3>Tip objave</h3>
        <div class="types">${["auto", ...Object.keys(TYPES)].map((k) => `<button class="chip ${(S.post.trapIdx != null ? k === "trap" : S.post.type === k) ? "on" : ""}" data-type="${k}">${k === "auto" ? `Samodejno${sel[0] ? ` (${TYPES[type].name})` : ""}` : TYPES[k].name}</button>`).join("")}</div>
        <textarea id="postText" class="post-out" rows="16" placeholder="Izberi dogodek na levi…">${esc(text)}</textarea>
        <div class="row" style="margin-top:8px"><button class="btn" id="copyPost">Kopiraj</button><button class="btn o" id="clearPost">Počisti izbor</button><span class="muted" style="font-size:12px">${sel.length} izbranih</span></div>
      </div>
    </div>`;
  v.querySelectorAll(".pick-item[data-k]").forEach((el) => el.onclick = () => {
    const k = el.dataset.k; S.post.trapIdx = null; S.post.alertIdx = null;
    S.post.selected = S.post.selected.includes(k) ? S.post.selected.filter((x) => x !== k) : [...S.post.selected, k];
    render();
  });
  v.querySelectorAll(".pick-item[data-trap]").forEach((el) => el.onclick = () => { S.post.trapIdx = +el.dataset.trap; S.post.alertIdx = null; S.post.selected = []; render(); });
  v.querySelectorAll(".pick-item[data-alert]").forEach((el) => el.onclick = () => { S.post.alertIdx = +el.dataset.alert; S.post.trapIdx = null; S.post.selected = []; render(); });
  v.querySelectorAll("[data-type]").forEach((b) => b.onclick = () => {
    if (b.dataset.type === "trap") { if (S.post.trapIdx == null) S.post.trapIdx = 0; } else { S.post.trapIdx = null; S.post.type = b.dataset.type; }
    render();
  });
  $("#copyPost").onclick = () => {
    const t = $("#postText").value;
    navigator.clipboard?.writeText(t).then(() => { $("#copyPost").textContent = "Kopirano ✓"; setTimeout(() => ($("#copyPost").textContent = "Kopiraj"), 1500); });
  };
  if (S.post.scroll && window.innerWidth < 700) { $("#postText").scrollIntoView({ block: "center" }); }
  S.post.scroll = false;
  v.querySelectorAll(".pick-item").forEach((el) => el.addEventListener("click", () => { S.post.scroll = true; }, { capture: true }));
  $("#clearPost").onclick = () => { S.post.selected = []; S.post.trapIdx = null; S.post.alertIdx = null; S.post.type = "auto"; render(); };
}

load();

/* ---------- export for Claude ---------- */
/* current w per sport (p_final = w × model + (1 − w) × market) */
function wLine() {
  const w = S.data.meta.market_w || {};
  const names = { football: "nogomet", nhl: "NHL", nba: "NBA", euroleague: "Euroliga" };
  const parts = Object.keys(names).map((k) => `${names[k]} ${w[k] ?? 0.4}`);
  return `w (utež modela): ${parts.join(", ")}; tanek trg (< 4 stavnice): ${S.data.meta.market_w_thin ?? 0.6}`;
}
function claudeExport(day, sport, withPlayers, allMatches) {
  const inDay = (iso) => day === "all2" ? [todayKey(0), todayKey(1)].includes(dayKey(iso)) : dayKey(iso) === day;
  const spOk = (s) => sport === "all" || s === sport;
  const ms = S.data.matches.filter((m) => inDay(m.kickoff) && spOk(m.sport)).sort((a, b) => a.kickoff.localeCompare(b.kickoff));
  const hm = (iso) => new Date(iso).toLocaleTimeString("sl-SI", { hour: "2-digit", minute: "2-digit" });
  const L = [];
  const dayTxt = day === "all2" ? `${dayLabel(todayKey(0))} in ${dayLabel(todayKey(1)).toLowerCase()}` : dayLabel(day);
  L.push(`# The Odds Room – podatki za analizo (${dayTxt}, ${sport === "all" ? "vsi športi" : SPORTS[sport]?.name})`);
  L.push(`Generirano: ${new Date(S.data.meta.generated || Date.now()).toLocaleString("sl-SI")}`);
  // data coverage per competition in this export: injuries and backtest
  const comps = new Map();
  for (const x of ms) if (!comps.has(x.league)) comps.set(x.league, x);
  const inj = S.data.meta.injury_sources || {};
  const injKey = (x) => x.sport === "football" ? x.league : x.sport;
  const noInj = [...comps.values()].filter((x) => !(inj[injKey(x)] || []).length).map((x) => x.league_name);
  const notVal = [...comps.values()].filter((x) => !isValidated(x)).map((x) => x.league_name);
  L.push(`Podatki o poškodbah: ${[...comps.values()].filter((x) => (inj[injKey(x)] || []).length).map((x) => `${x.league_name} (${inj[injKey(x)].join(", ")})`).join(", ") || "–"}`);
  if (noInj.length) L.push(`injury data: none → ${noInj.join(", ")} (za nogomet razen Premier League ni brezplačnega vira poškodb za tekočo sezono; postave ~1 h pred tekmo so upoštevane)`);
  if (notVal.length) L.push(`NOT VALIDATED (backtest manjka ali premalo tekem): ${notVal.join(", ")}`);
  L.push(wLine());
  const elPd = plData("euroleague");
  if (ms.some((x) => x.sport === "euroleague")) {
    L.push(elPd?.ok ? `EuroLeague player data: EuroLeague API, sezona ${elPd.season} (${elPd.games} tekem)` : `player data: none → EuroLeague${elPd?.reason ? ` (${elPd.reason})` : ""}`);
  }
  L.push("");
  L.push("## Navodilo");
  L.push("Si analitik športnih stav. Spodaj so ocene modela The Odds Room. Pravilo picka: p_final × kvota bet365 > 1.00 (◆ = ≥ 1.10). Brez drugih oznak.");
  L.push("p_final = w × model + (1 − w) × trg brez marže (Pinnacle ali povprečje stavnic; tanek trg < 4 stavnice: w = 0.6). V oklepaju je čisti model.");
  L.push("Za vsak pick in kandidata: 1) ali je razlika do trga realna, 2) novice, postave, motivacija, ki jih model ne vidi, 3) igraj / manjši vložek / preskoči z enim stavkom.");
  L.push("Tekme NEZANESLJIVO in DATA MISMATCH niso za stave. Tekmovanja NOT VALIDATED jemlji previdno. Bodi kritičen; brez garancij.");
  L.push("");
  const st = S.data.picks.stats || {};
  const a = st.all || {};
  L.push("## Model shadow log (samodejni picki, ni uradni zapis)");
  L.push(`Picki: ${a.n ?? 0} (odprtih ${a.open ?? 0}) · Z–P–V ${a.won ?? 0}–${a.lost ?? 0}–${a.push ?? 0} · zadetki ${pct(a.hit, 1)} (pričakovano ${pct(a.avg_p, 1)}) · profit ${fmtNum(a.profit ?? 0, 2)}u · ROI ${a.roi == null ? "–" : (a.roi * 100).toFixed(1) + " %"}`);
  if (st.by_w) L.push("Po športu in w (zadetki / pričakovano, profit): " + Object.entries(st.by_w).flatMap(([sp, ws]) => Object.entries(ws).map(([w, k]) => `${SPORTS[sp]?.name || sp} ${w}: ${pct(k.hit, 1)}/${pct(k.avg_p, 1)}, ${fmtNum(k.profit, 2)}u (n${k.n})`)).join(" · "));
  const cal = st.calibration || [];
  if (cal.length) L.push("Kalibracija vseh ocen (napovedano → dejansko): " + cal.filter((b) => b.n >= 5).map((b) => `${pct(b.pred)}→${pct(b.act)} (n${b.n})`).join(", "));
  const pc = st.picks_calibration || [];
  if (pc.length) L.push("Kalibracija samo pickov: " + pc.map((b) => `${pct(b.pred)}→${pct(b.act)} (n${b.n})`).join(", "));
  const mine = settleMyPicks();
  { const k = pickSummary(mine); L.push(`URADNI DNEVNIK (moji bet365 picki): ${k.n} (odprtih ${k.open}) · ${k.won}–${k.lost} · zadetki ${pct(k.hit, 1)} (pričakovano ${pct(k.exp, 1)}) · ${k.profit.toFixed(2)}u${unsavedPicks().length ? ` · ${unsavedPicks().length} NI VARNOSTNO SHRANJENO` : ""}`); }
  L.push("");

  const picks = (S.data.picks.picks || []).filter((p) => p.status === "open" && inDay(p.kickoff) && spOk(p.sport));
  const myOpen = mine.filter((p) => p.status === "open" && inDay(p.kickoff) && spOk(p.sport));
  L.push(`## Odprti picki (${picks.length + myOpen.length})`);
  if (picks.length || myOpen.length) {
    L.push("| čas | tekma | pick | p_final (model) | kvota | vir | p × kvota | p zdaj | opozorilo |");
    L.push("|---|---|---|---|---|---|---|---|---|");
    for (const p of picks.sort((a, b) => a.kickoff.localeCompare(b.kickoff))) {
      const evv = p.p * p.odds;
      L.push(`| ${hm(p.kickoff)} | ${p.home} – ${p.away} (${p.league || ""}) | ${slLabel(p, p)}${evv >= STRONG_EV ? " ◆" : ""} | ${pct(p.p)}${p.p_model != null ? ` (${pct(p.p_model)})` : ""} | ${odds(p.odds)} | ${p.odds_src || "povprečje (staro pravilo)"} | ${evv.toFixed(3)} | ${p.p_now != null ? pct(p.p_now) : "–"} | ${p.alert ? (p.alert.level === "red" ? "RDEČE: " : "RUMENO: ") + p.alert.text : ""} |`);
    }
    for (const p of myOpen) L.push(`| ${hm(p.kickoff)} | ${p.home} – ${p.away} (${p.league || ""}) | ${p.label}${p.strong ? " ◆" : ""} | ${pct(p.p_final)} (${pct(p.p_model)}) | ${odds(p.odds)} | bet365 (moj vnos) | ${p.ev.toFixed(3)} | – | |`);
  } else L.push("Ni odprtih pickov.");
  L.push("");

  // to check on bet365: reliable matches, p_final x average price > 1
  const cand = [];
  for (const x of ms) {
    if (isBad(x)) continue;
    for (const m of x.markets) {
      const price = m.b365 || m.odds;
      if (!price || m.odds_assumed || m.p < 0.3 || m.p * price <= 1) continue;
      cand.push({ x, m, price, ev: m.p * price });
    }
  }
  cand.sort((a, b) => b.ev - a.ev);
  L.push(`## Za preveriti na bet365 (zanesljive tekme, p_final × kvota > 1; ${Math.min(30, cand.length)})`);
  if (cand.length) {
    L.push("| čas | tekma | trg | p_final (model) | min. kvota | kvota | vir kvote | p × kvota | trg brez marže |");
    L.push("|---|---|---|---|---|---|---|---|---|");
    for (const { x, m, price, ev } of cand.slice(0, 30)) L.push(`| ${hm(x.kickoff)} | ${x.home} – ${x.away} (${x.league_name})${isValidated(x) ? "" : " [not validated]"} | ${slLabel(m, x)} | ${pfm(m)} | ${(1 / m.p).toFixed(2)} | ${odds(price)} | ${m.b365 ? "bet365" : "povprečje – preveri bet365"} | ${ev.toFixed(3)}${ev >= STRONG_EV ? " ◆" : ""} | ${refFor(x, m)} |`);
  } else L.push("Ni kandidatov.");
  L.push("");

  const mm = (S.data.mismatches || []).filter((m) => inDay(m.kickoff) && spOk(m.sport));
  L.push(`## DATA MISMATCH (viri se ne ujemajo; ${mm.length})`);
  for (const m of mm) L.push(`- ${hm(m.kickoff)} ${m.league || ""}: ${m.home} – ${m.away} · ${m.reason}`);
  if (!mm.length) L.push("Ni neujemanj.");
  L.push("");

  const bad = ms.filter((x) => isBad(x) && !x.mismatch)
    .sort((a, b) => Math.max(b.market_ref?.gap_1x2 || 0, b.market_ref?.gap_ou || 0) - Math.max(a.market_ref?.gap_1x2 || 0, a.market_ref?.gap_ou || 0)).slice(0, 25);
  L.push(`## NEZANESLJIVO (razlika do trga > 10 o. t. na glavnem trgu → cela tekma; ${bad.length})`);
  for (const x of bad) L.push(`- ${hm(x.kickoff)} ${x.home} – ${x.away} (${x.league_name}): ${x.market_ref ? refShort(x) : ""}`);
  if (!bad.length) L.push("Ni takih tekem.");
  L.push("");

  const show = allMatches ? ms : ms.filter((x) => bestValue(x) || x.availability || x.adj || x.context?.length || picks.some((p) => p.match === x.id) || isBad(x));
  L.push(`## Tekme (${show.length}${allMatches ? "" : ` od ${ms.length}: s pickom, novico, prilagoditvijo ali opozorilom`})`);
  for (const x of show.slice(0, 80)) L.push(matchLine(x, hm));
  if (show.length > 80) L.push(`… in še ${show.length - 80} tekem.`);
  L.push("");

  if (withPlayers) {
    const pp = [];
    for (const [sp, list] of Object.entries(S.data.players)) {
      if (!Array.isArray(list) || !spOk(sp)) continue;
      for (const pl of list) {
        const x = pl.next && S.data.byId[pl.next.match];
        if (!x || !inDay(x.kickoff) || !pl.props || isBad(x)) continue;
        if (pl.next.status === "out" || pl.next.status === "doubt") continue;
        if (![x.home, x.away, ...Object.values(x.domestic || {}).map((d) => d?.[1])].includes(pl.team)) continue;
        for (const pr of pl.props) {
          if (pr.p < 0.55 || pr.p > 0.85) continue;
          if ((pr.stat === "reb" || pr.stat === "ast") && pr.line === 0.5) continue;
          pp.push({ pl, pr, x });
        }
      }
    }
    pp.sort((a, b) => b.pr.p - a.pr.p);
    L.push(`## Igralci: dogodki 55–85 % (top ${Math.min(25, pp.length)}; brez poškodovanih, kaznovanih in vprašljivih)`);
    const avgTxt = (pl, pr) => pl.l5 && pl[pr.stat] != null ? ` · sezona ${fmtNum(pl[pr.stat], 1)}, L5 ${fmtNum(pl.l5[pr.stat], 1)} (${pl.gp} tekem)` : "";
    for (const { pl, pr, x } of pp.slice(0, 25)) L.push(`- ${pl.name} (${pl.team}, ${pl.next.home ? "doma" : "v gosteh"} vs ${pl.next.opp}, ${hm(x.kickoff)}): ${pr.label} ${pct(pr.p)}, min. kvota ${(1 / pr.p).toFixed(2)}${avgTxt(pl, pr)}${(inj[injKey(x)] || []).length ? "" : " [injury data: none]"}`);
    if (spOk("euroleague") && ms.some((x) => x.sport === "euroleague") && elPd?.ok === false) L.push("- EuroLeague: player data: none");
    const few = (S.data.players.euroleague || []).filter((pl) => pl.props_note && pl.next && S.data.byId[pl.next.match] && inDay(S.data.byId[pl.next.match].kickoff));
    if (spOk("euroleague") && few.length) L.push(`- EuroLeague: ${few.length} igralcev ima premalo tekem (< 5) – too few games, brez verjetnosti: ${few.slice(0, 10).map((pl) => pl.name).join(", ")}${few.length > 10 ? " …" : ""}`);
    if (!pp.length) L.push("Ni podatkov o igralcih za ta dan.");
  }
  return L.join("\n");
}
function refFor(x, m) {
  const r = x.market_ref;
  if (!r) return "–";
  if (m.scope === "match" && r.p[m.kind] != null) return pct(r.p[m.kind]);
  const side = (q) => pct(m.kind === "over" ? q : 1 - q);
  if (m.scope === "total" && m.line === 2.5 && r.p["over2.5"] != null) return side(r.p["over2.5"]);
  if (m.scope === "total" && m.line === r.line && r.p.over != null) return side(r.p.over);
  return "–";
}
function refShort(x) {
  const r = x.market_ref;
  const k = (c) => r.p[c] != null ? `${c} ${pct(r.model[c])}/${pct(r.p[c])}` : null;
  return [k("1"), k("X"), k("2"), r.p["over2.5"] != null ? `O2.5 ${pct(r.model["over2.5"])}/${pct(r.p["over2.5"])}` : null,
    r.p.over != null ? `O${r.line} ${pct(r.model.over)}/${pct(r.p.over)}` : null].filter(Boolean).join(", ") + ` (model/trg, ${SRC_SL[r.src] || r.src})`;
}
function matchLine(x, hm) {
  const h = mkt(x, "match", "1"), d = mkt(x, "match", "X"), a = mkt(x, "match", "2");
  const parts = [`${hm(x.kickoff)} ${SPORTS[x.sport]?.name}/${x.league_name}${isValidated(x) ? "" : " [not validated]"}: ${x.home} – ${x.away}`];
  const dg = x.sport === "football" || x.sport === "nhl" ? 2 : 1;
  parts.push(`pričak. ${fmtNum(x.exp[0], dg)}–${fmtNum(x.exp[1], dg)}${x.exp_model ? ` (model ${fmtNum(x.exp_model[0], dg)}–${fmtNum(x.exp_model[1], dg)}, w=${x.blend?.w}${x.blend?.thin ? " tanek trg" : ""})` : ""}`);
  if (h && a) parts.push(`p_final ${pct(h.p)}/${d && x.sport === "football" ? pct(d.p) + "/" : ""}${pct(a.p)}${h.p_model != null ? ` (model ${pct(h.p_model)}/${d && x.sport === "football" ? pct(d.p_model) + "/" : ""}${pct(a.p_model)})` : ""}`);
  if (x.market_ref) parts.push(`trg ${refShort(x)}`);
  if (x.sport === "football") {
    const o = mkt(x, "total", "over", 2.5), b = mkt(x, "btts", "yes");
    if (o) parts.push(`O2.5 ${pfm(o)}`);
    if (b) parts.push(`BTTS ${pfm(b)}`);
  }
  const f = (side) => (x.form?.[side] || []).map((g) => g.r).join("");
  if (x.form?.home?.length) parts.push(`forma ${f("home")} / ${f("away")}`);
  const abs = ["home", "away"].map((s) => absList(x, s).filter((q) => q.status === "out").map((q) => q.name).slice(0, 4));
  if (abs[0].length || abs[1].length) parts.push(`manjkajo: ${abs[0].join(", ") || "–"} | ${abs[1].join(", ") || "–"}`);
  if (x.availability?.lineup) parts.push("postavi potrjeni");
  if (x.goalies) parts.push(`vratarja (ocena, ni potrjeno): ${["home", "away"].map((s) => { const g = x.goalies[s]; return g ? `${g.name} ${(g.sv * 100).toFixed(1)} %${g.b2b ? " B2B" : ""}` : "–"; }).join(" | ")}`);
  if (x.adj) parts.push(`prilagoditev (odsotnosti): ${adjText(x)}`);
  for (const c of x.context || []) parts.push(`prilagoditev (počitek): ${c}`);
  if (x.sport === "euroleague" && (x.stats?.home?.box || x.stats?.away?.box)) {
    const t = (side) => { const b = x.stats?.[side]?.box; return b ? `${b.season.pts}–${b.season.opp_pts} (L5 ${b.l5.pts}–${b.l5.opp_pts})` : "–"; };
    parts.push(`točke dane–prejete sezona: ${t("home")} | ${t("away")}`);
  }
  const v = bestValue(x);
  if (v) parts.push(`PICK: ${slLabel(v, x)} @ bet365 ${odds(v.b365)} (p ${pfm(v)}, p×kvota ${(v.p * v.b365).toFixed(3)}${v.p * v.b365 >= STRONG_EV ? " ◆" : ""})`);
  if (x.mismatch) parts.push(`DATA MISMATCH: ${x.mismatch}`);
  else if (isBad(x)) parts.push("NEZANESLJIVO");
  return "- " + parts.join(" · ");
}
function todayKey(offset) {
  const d = new Date(); d.setDate(d.getDate() + offset);
  return dayKey(d.toISOString());
}
function openClaude() {
  const m = $("#claudeModal");
  const days = [...new Set(S.data.matches.map((x) => dayKey(x.kickoff)))].sort().filter((d) => d >= todayKey(0)).slice(0, 7);
  $("#cmDay").innerHTML = days.map((d) => `<option value="${d}">${dayLabel(d)}</option>`).join("") + `<option value="all2">Danes + jutri</option>`;
  $("#cmSport").innerHTML = `<option value="all">Vsi</option>` + Object.entries(SPORTS).map(([k, v]) => `<option value="${k}" ${S.sport === k ? "selected" : ""}>${v.name}</option>`).join("");
  const upd = () => {
    const t = claudeExport($("#cmDay").value, $("#cmSport").value, $("#cmPlayers").checked, $("#cmAll").checked);
    $("#cmText").value = t;
    $("#cmCount").textContent = `${t.length.toLocaleString("sl-SI")} znakov`;
  };
  ["#cmDay", "#cmSport", "#cmPlayers", "#cmAll"].forEach((id) => ($(id).onchange = upd));
  upd();
  m.hidden = false;
}
$("#claudeBtn").onclick = () => openClaude();
$("#cmClose").onclick = () => ($("#claudeModal").hidden = true);
$("#claudeModal").addEventListener("click", (e) => { if (e.target.id === "claudeModal") e.target.hidden = true; });
$("#cmCopy").onclick = () => {
  const t = $("#cmText").value;
  const done = () => { $("#cmCopy").textContent = "Kopirano ✓"; setTimeout(() => ($("#cmCopy").textContent = "Kopiraj"), 1500); };
  if (navigator.clipboard?.writeText) navigator.clipboard.writeText(t).then(done, () => { $("#cmText").select(); document.execCommand("copy"); done(); });
  else { $("#cmText").select(); document.execCommand("copy"); done(); }
};
