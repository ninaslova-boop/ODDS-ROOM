/* The Odds Room Analytics — static front end. Data comes from data/*.json (built by GitHub Actions). */
"use strict";

const S = {
  data: { matches: [], players: {}, picks: { picks: [], stats: {} }, meta: {} },
  sport: "all",
  tab: "matches",
  open: new Set(),            // expanded match cards
  rank: { min: 0.55, max: 0.92, kind: "all", group: "all", q: "", onlyOdds: false },
  value: { minEdge: 0.03, inclAssumed: true },
  players: { sport: "football", league: "all", q: "", sort: null, dir: -1, minMin: 450 },
  matchDay: "all", league: "all",
  log: { filter: "all" },
  post: { selected: [], type: "auto" },
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
  const [matches, players, picks, meta] = await Promise.all(["matches.json", "players.json", "picks.json", "meta.json"].map(get));
  S.data.matches = matches?.matches || [];
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
  ({ matches: viewMatches, ranking: viewRanking, value: viewValue, players: viewPlayers, log: viewLog, posts: viewPosts })[S.tab](v);
}

function emptyState() {
  const errs = (S.data.meta.errors || []).slice(0, 8).map((e) => `<li>${esc(e)}</li>`).join("");
  return `<div class="card empty"><b>Še ni podatkov</b>
    Podatke zgradi GitHub Actions (workflow <i>Update data</i>). Zaženi ga ročno ali počakaj na naslednji zagon.
    ${errs ? `<ul style="text-align:left;font-size:12px">${errs}</ul>` : ""}</div>`;
}

function sourceWarnings() {
  const st = S.data.meta.status || {};
  const bad = Object.entries(st).filter(([k, v]) => !v.ok && (S.sport === "all" || S.sport === k));
  if (!bad.length) return "";
  return `<div class="warn-box">Ni svežih tekem ali modela za: ${bad.map(([k]) => SPORTS[k]?.name || k).join(", ")}.
    Lahko je premor sezone ali težava vira. <details><summary>Podrobnosti</summary>${(S.data.meta.errors || []).slice(0, 15).map(esc).join("<br>")}</details></div>`;
}

/* ---------- small components ---------- */
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

  v.innerHTML = `${sourceWarnings()}
    <div class="filters">
      <label>Dan<select id="fDay"><option value="all">Vsi dnevi</option>${days.map((d) => `<option value="${d}" ${S.matchDay === d ? "selected" : ""}>${dayLabel(d)}</option>`).join("")}</select></label>
      <label>Liga<select id="fLeague"><option value="all">Vse lige</option>${leagues.map((l) => `<option ${S.league === l ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>
      <span class="spacer"></span><span class="muted" style="font-size:12px">${list.length} tekem · klikni tekmo za podrobnosti</span>
    </div>
    ${Object.keys(groups).length ? Object.entries(groups).map(([d, ms]) => `<div class="day-h">${dayLabel(d)}</div>${ms.map(matchCard).join("")}`).join("")
      : `<div class="card empty"><b>Ni tekem</b>Za ta izbor v naslednjih 7 dneh ni tekem.</div>`}`;
  $("#fDay").onchange = (e) => { S.matchDay = e.target.value; render(); };
  $("#fLeague").onchange = (e) => { S.league = e.target.value; render(); };
}

function matchCard(x) {
  const sp = SPORTS[x.sport] || {};
  const isBall = x.sport === "nba" || x.sport === "euroleague";
  const vals = x.markets.filter((m) => m.edge != null && m.edge >= 0.05 && m.p >= 0.4);
  const top = topEvents(x);
  const unit = isBall ? "točk" : "golov";
  const head = isBall
    ? `<span>Pričakovano: <b>${fmtNum(x.exp[0], 1)} – ${fmtNum(x.exp[1], 1)}</b></span><span>Skupaj: <b>${fmtNum(x.exp[0] + x.exp[1], 1)}</b></span><span>Razlika: <b>${(x.exp[0] - x.exp[1] > 0 ? "+" : "") + fmtNum(x.exp[0] - x.exp[1], 1)}</b></span>`
    : `<span>Najverjetnejši izid: <b>${x.top_scores?.[0]?.s}</b> (${pct(x.top_scores?.[0]?.p)})</span><span>Najverjetneje skupaj: <b>${x.total_mode} ${unit}</b></span><span>Pričakovano skupaj: <b>${fmtNum(x.exp[0] + x.exp[1], 2)}</b></span>`;
  return `<div class="card match" data-id="${esc(x.id)}">
    <div class="meta"><span>${sp.icon || ""} ${esc(x.league_name)}</span><span>·</span><span>${fmtTime(x.kickoff)}</span>
      <span class="spacer"></span>${vals.length ? `<span class="badge amber">VALUE ×${vals.length}</span>` : ""}</div>
    <div class="teams">
      <div>${esc(x.home)}${formChips(x.form?.home)}</div><div class="xg num">${expLbl(x, 0)}</div>
      <div>${esc(x.away)}${formChips(x.form?.away)}</div><div class="xg num">${expLbl(x, 1)}</div>
    </div>
    <div class="headline">${head}</div>
    <div class="top-ev">${top.map((m) => `<span class="ev ${m.edge > 0.03 ? "val" : ""}">${esc(slLabel(m, x))} <b>${pct(m.p)}</b></span>`).join("")}</div>
    ${S.open.has(x.id) ? matchDetails(x) : ""}
  </div>`;
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
  const statTbl = `<div class="kv"><span class="h">${x.sport === "football" ? "Sezona / tekmo" : "Ta sezona"}</span><span class="h">${esc(x.home)}</span><span class="h">${esc(x.away)}</span>
    ${statRows.map(([l, k, isP]) => `<span class="muted">${l}</span><span class="num">${isP ? pct(st.home?.[k]) : st.home?.[k] ?? "–"}</span><span class="num">${isP ? pct(st.away?.[k]) : st.away?.[k] ?? "–"}</span>`).join("")}</div>`;
  const dist = x.total_dist ? `<h3>Porazdelitev skupnih golov</h3>${distChart(x.total_dist, x.total_mode)}
      <h3>Najverjetnejši izidi</h3><div class="top-ev">${x.top_scores.map((s) => `<span class="ev">${s.s} <b>${pct(s.p, 1)}</b></span>`).join("")}</div>` : `<h3>Model</h3><div class="muted" style="font-size:13px">Pričakovano ${fmtNum(x.exp[0], 1)} – ${fmtNum(x.exp[1], 1)}. Raztros: total ±${x.sd?.[0]}, razlika ±${x.sd?.[1]} (1 SD).</div>`;
  const extra = x.extra ? `<div class="muted" style="font-size:12.5px;margin-top:8px">${x.extra.corners ? `Pričakovani koti: <b>${x.extra.corners[0]} – ${x.extra.corners[1]}</b> · ` : ""}${x.extra.cards ? `kartoni: <b>${x.extra.cards[0]} – ${x.extra.cards[1]}</b> · ` : ""}${x.extra.sot ? `streli v okvir: <b>${x.extra.sot[0]} – ${x.extra.sot[1]}</b>` : ""}</div>` : "";
  const players = matchPlayers(x);
  return `<div class="details">
    <div class="grid2"><div>${dist}${extra}</div><div><h3>Statistika</h3>${statTbl}</div></div>
    ${Object.entries(groups).map(([g, ms]) => `<h3>${esc(g)}</h3><div class="scroll"><table class="tbl">
      <tr><th>Dogodek</th><th>Verjetnost</th><th class="r">Fer kvota</th><th class="r">Kvota</th><th class="r">Prednost</th></tr>
      ${ms.map((m) => `<tr class="${m.edge >= 0.05 ? "hl" : ""}"><td>${esc(slLabel(m, x))}</td><td>${pbar(m.p)}</td><td class="r num">${odds(m.fair)}</td><td class="r num">${odds(m.odds)}${m.odds_assumed ? "~" : ""}</td><td class="r num">${edgeCell(m.edge, m.odds_assumed)}</td></tr>`).join("")}
      </table></div>`).join("")}
    ${players}
  </div>`;
}

function matchPlayers(x) {
  const list = (S.data.players[x.sport] || []).filter((p) => p.next?.match === x.id && p.props);
  if (!list.length) return "";
  const key = { football: "1+ strel v okvir", nhl: "Strelov na gol 3+", nba: null }[x.sport];
  const sorted = list.slice().sort((a, b) => (b.next.exp_shots || b.next.exp_sog || b.next.proj_pts || 0) - (a.next.exp_shots || a.next.exp_sog || a.next.proj_pts || 0)).slice(0, 12);
  return `<h3>Igralci na tej tekmi</h3><div class="scroll"><table class="tbl"><tr><th>Igralec</th><th>Ekipa</th><th>Napoved</th><th>Top dogodki</th></tr>
    ${sorted.map((p) => {
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
      <label style="flex-direction:row;align-items:center;gap:6px;text-transform:none;font-size:13px"><input type="checkbox" id="fOdds" ${R.onlyOdds ? "checked" : ""}> samo s kvoto</label>
    </div>
    <div class="card scroll" style="padding:4px 6px"><table class="tbl">
      <tr><th>#</th><th>Dogodek</th><th>Tekma</th><th>Čas</th><th>Verjetnost</th><th class="r">Fer</th><th class="r">Kvota</th><th class="r">Prednost</th></tr>
      ${shown.map((e, i) => `<tr class="${e.edge >= 0.05 ? "hl" : ""}"><td class="faint num">${i + 1}</td><td>${SPORTS[e.sport]?.icon || ""} ${esc(e.title)}</td><td class="muted">${esc(e.sub)}</td><td class="muted" style="white-space:nowrap">${fmtTime(e.kickoff)}</td><td>${pbar(e.p)}</td><td class="r num">${odds(1 / e.p)}</td><td class="r num">${odds(e.odds)}</td><td class="r">${edgeCell(e.edge, e.assumed)}</td></tr>`).join("")}
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
  return allEvents().filter((e) => e.edge != null && e.edge >= S.value.minEdge && e.p >= 0.3 && (S.value.inclAssumed || !e.assumed))
    .sort((a, b) => b.edge - a.edge);
}
function viewValue(v) {
  const ev = valueEvents();
  v.innerHTML = `<h2>Value: model proti kvoti</h2>
    <p class="section-note">Prednost = verjetnost × kvota − 1. Pozitivna prednost pomeni, da je kvota višja od fer kvote modela. Kvota z "~" je privzeta (-110), ker vir ni dal cene.</p>
    <div class="filters">
      <label>Min. prednost <span class="g" id="eV">${(S.value.minEdge * 100).toFixed(0)}%</span><input type="range" id="fEdge" min="0" max="0.3" step="0.01" value="${S.value.minEdge}"></label>
      <label style="flex-direction:row;align-items:center;gap:6px;text-transform:none;font-size:13px"><input type="checkbox" id="fAss" ${S.value.inclAssumed ? "checked" : ""}> vključi privzete kvote</label>
    </div>
    <div class="card scroll" style="padding:4px 6px"><table class="tbl">
      <tr><th>Dogodek</th><th>Tekma</th><th>Čas</th><th>Model</th><th class="r">Kvota</th><th class="r">Implicirano</th><th class="r">Prednost</th><th></th></tr>
      ${ev.map((e, i) => `<tr><td>${SPORTS[e.sport]?.icon || ""} ${esc(e.title)}</td><td class="muted">${esc(e.sub)}</td><td class="muted" style="white-space:nowrap">${fmtTime(e.kickoff)}</td><td>${pbar(e.p)}</td><td class="r num">${odds(e.odds)}${e.assumed ? "~" : ""}</td><td class="r num">${pct(1 / e.odds)}</td><td class="r">${edgeCell(e.edge, e.assumed)}</td><td><button class="btn o s" data-post="${i}">Objava</button></td></tr>`).join("")}
    </table>${!ev.length ? `<div class="empty"><b>Ni value dogodkov</b>Ni kvot ali pa model ne vidi prednosti. "Tiho = malo pickov."</div>` : ""}</div>`;
  $("#fEdge").oninput = (e) => { S.value.minEdge = +e.target.value; $("#eV").textContent = (S.value.minEdge * 100).toFixed(0) + "%"; };
  $("#fEdge").onchange = () => render();
  $("#fAss").onchange = (e) => { S.value.inclAssumed = e.target.checked; render(); };
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
function pval(p, k) {
  if (k === "_p1" || k === "_p2") {
    const lbl = { football: { _p1: "1+ strel v okvir", _p2: "2+ strela" }, nhl: { _p1: "Strelov na gol 3+", _p2: "Da gol" } }[S.players.sport]?.[k];
    return p.props?.find((r) => r.label === lbl)?.p ?? null;
  }
  if (k === "_proj") return p.next?.proj_pts ?? null;
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
  const sk = P.sort || { football: "xg90", nhl: "p_g", nba: "pts" }[P.sport];
  list.sort((a, b) => {
    const x = pval(a, sk), y = pval(b, sk);
    if (typeof x === "string" || typeof y === "string") return P.dir * -String(x ?? "").localeCompare(String(y ?? ""));
    return P.dir * ((x ?? -1) - (y ?? -1));
  });
  const shown = list.slice(0, 200);
  const lgName = (c) => ({ E0: "Premier League", SP1: "LaLiga", D1: "Bundesliga", I1: "Serie A", F1: "Ligue 1" }[c] || c);
  v.innerHTML = `<h2>Lestvica igralcev</h2>
    <p class="section-note">${P.sport === "football" ? "Understat: tekoča sezona + pol uteži prejšnje sezone, na 90 minut. Verjetnosti upoštevajo nasprotnika, dom/gostovanje in pričakovane minute (brez potrjenih postav)." : P.sport === "nhl" ? "NHL stats API: na tekmo, tekoča sezona + pol uteži prejšnje. Verjetnosti upoštevajo strele, ki jih nasprotnik dopušča." : "ESPN box score: zadnjih 15 tekem igralca. Projekcija točk upošteva obrambo nasprotnika."}</p>
    <div class="filters">
      <label>Šport<select id="pSport">${Object.keys(PCOLS).map((k) => `<option value="${k}" ${P.sport === k ? "selected" : ""}>${SPORTS[k].name}</option>`).join("")}</select></label>
      ${P.sport === "football" ? `<label>Liga<select id="pLeague"><option value="all">Vse</option>${leagues.map((l) => `<option value="${l}" ${P.league === l ? "selected" : ""}>${lgName(l)}</option>`).join("")}</select></label>
      <label>Min. minut<input id="pMin" type="number" min="0" step="90" value="${P.minMin}" style="width:90px"></label>` : ""}
      <label>Išči<input id="pQ" value="${esc(P.q)}" placeholder="ime, ekipa…"></label>
    </div>
    <div class="card scroll" style="padding:4px 6px"><table class="tbl nowrap">
      <tr>${cols.map(([k, l, d]) => `<th class="sort ${d != null || k.startsWith("_p") ? "r" : ""} ${sk === k ? "act" : ""}" data-k="${k}">${l}${sk === k ? (P.dir < 0 ? " ↓" : " ↑") : ""}</th>`).join("")}</tr>
      ${shown.map((p) => `<tr>${cols.map(([k, , d]) => {
        if (k === "_next") return `<td class="muted" style="white-space:nowrap">${p.next ? `${p.next.home ? "vs" : "@"} ${esc(p.next.opp)} · ${fmtTime(p.next.kickoff)}` : "–"}</td>`;
        if (k === "_p1" || k === "_p2") { const val = pval(p, k); return `<td class="r">${val == null ? "–" : pbar(val)}</td>`; }
        if (k === "_proj") return `<td class="r num">${fmtNum(pval(p, k), 1)}</td>`;
        if (d != null) return `<td class="r num">${fmtNum(p[k], d)}</td>`;
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

/* ---------- view: pick log ---------- */
function viewLog(v) {
  const all = (S.data.picks.picks || []).filter((p) => S.sport === "all" || p.sport === S.sport);
  const st = S.data.picks.stats || {};
  const sum = S.sport === "all" ? st.all : st.by_sport?.[S.sport];
  const k = sum || {};
  const list = all.filter((p) => S.log.filter === "all" || (S.log.filter === "open" ? p.status === "open" : p.status !== "open"))
    .sort((a, b) => b.kickoff.localeCompare(a.kickoff));
  const statusBadge = (s) => ({ won: `<span class="badge">ZADETEK</span>`, lost: `<span class="badge red">ZGREŠEN</span>`, push: `<span class="badge grey">VRAČILO</span>`, void: `<span class="badge grey">RAZVELJ.</span>`, open: `<span class="badge amber">ODPRT</span>` }[s] || s);
  v.innerHTML = `<h2>Dnevnik pickov</h2>
    <p class="section-note">Model sam zabeleži en value pick na tekmo (prednost 5–25 %, verjetnost ≥ 40 %, samo prave kvote), preden se tekma začne. Nič se ne briše. Vložek 1 enota na pick.</p>
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
    </div>
    ${Object.keys(st.by_sport || {}).length ? `<div class="card scroll"><h3 style="margin-top:0">Po športih</h3><table class="tbl"><tr><th>Šport</th><th class="r">Picki</th><th class="r">Z–P</th><th class="r">Zadetki</th><th class="r">Profit</th><th class="r">ROI</th></tr>
      ${Object.entries(st.by_sport).map(([sp, s]) => `<tr><td>${SPORTS[sp]?.icon || ""} ${SPORTS[sp]?.name || sp}</td><td class="r num">${s.n}</td><td class="r num">${s.won}–${s.lost}</td><td class="r num">${pct(s.hit, 1)}</td><td class="r num">${fmtNum(s.profit, 2)}u</td><td class="r num">${s.roi == null ? "–" : (s.roi * 100).toFixed(1) + "%"}</td></tr>`).join("")}</table></div>` : ""}
    <div class="filters"><label>Prikaži<select id="lF"><option value="all">Vse</option><option value="open" ${S.log.filter === "open" ? "selected" : ""}>Odprte</option><option value="done" ${S.log.filter === "done" ? "selected" : ""}>Zaključene</option></select></label></div>
    <div class="card scroll" style="padding:4px 6px"><table class="tbl">
      <tr><th>Dan</th><th>Tekma</th><th>Pick</th><th class="r">Model</th><th class="r">Kvota</th><th class="r">Prednost</th><th>Status</th></tr>
      ${list.map((p) => `<tr><td class="muted" style="white-space:nowrap">${p.day}${p.totd ? ` <span class="badge" data-tip="Tip of the Day">TOTD</span>` : ""}</td><td>${SPORTS[p.sport]?.icon || ""} ${esc(p.home)} – ${esc(p.away)}</td>
        <td>${esc(slLabel(p, p))}</td><td class="r num">${pct(p.p)}</td><td class="r num">${odds(p.odds)}${p.odds_assumed ? "~" : ""}</td><td class="r">${edgeCell(p.edge, p.odds_assumed)}</td><td>${statusBadge(p.status)}</td></tr>`).join("")}
    </table>${!list.length ? `<div class="empty"><b>Dnevnik je prazen</b>Picki se zabeležijo samodejno ob naslednjih zagonih.</div>` : ""}</div>`;
  $("#lF").onchange = (e) => { S.log.filter = e.target.value; render(); };
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
const TYPES = {
  totd: { name: "Tip of the Day", head: "🎯 TIP OF THE DAY" },
  value: { name: "Value Pick", head: "💎 VALUE PICK" },
  goals: { name: "Goals Galore", head: "🔥 GOALS GALORE" },
  lockdown: { name: "Lockdown", head: "🔒 LOCKDOWN" },
  trap: { name: "Trap + FLIP", head: "🪤 TRAP + FLIP" },
  bold: { name: "Bold Play", head: "🎲 BOLD PLAY · HIGH RISK" },
  acca: { name: "Acca / Builder", head: "🧱 ACCA" },
};
function autoType(e, list) {
  if (list.length > 1) return "acca";
  if (e.odds && e.odds >= 3) return "bold";
  const m = e.m || e.prop;
  if (e.kind === "team") {
    if ((m.scope === "btts" && m.kind === "yes") || m.scope === "btts_over" || (m.kind === "over" && ["total", "home", "away"].includes(m.scope))) return "goals";
    if ((m.scope === "btts" && m.kind === "no") || (m.kind === "under")) return "lockdown";
  }
  const totd = (S.data.picks.picks || []).find((p) => p.totd && p.match === e.match.id && p.scope === m.scope && p.kind === m.kind && p.line === m.line);
  return totd ? "totd" : "value";
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
  const price = e.odds ? `Odds: ${odds(e.odds)} · Book implies ${pct(1 / e.odds)}` : `Fair price: ${odds(1 / e.p)} (check your book)`;
  const verdict = e.odds ? (e.edge >= 0.03 ? " → VALUE" : e.edge < 0 ? " → SKIP at this price" : " → thin") : "";
  return `${t.head}\n${SPORTS[x.sport].en} ${x.home} v ${x.away}\n🏆 ${x.league_name} · ${when(x.kickoff)}\n\n` +
    `Pick: ${evEn(e)}\n${price}\nEst. %: ${pct(e.p)}${verdict}\n\nThe read:\n${whyLines(e).map((l) => "• " + l).join("\n")}\n` +
    (type === "bold" ? "\nHigher odds, higher risk. Eyes open. Small stakes only.\n" : "") + foot;
}

function viewPosts(v) {
  const events = allEvents();
  const byKey = Object.fromEntries(events.map((e) => [evKey(e), e]));
  S.post.selected = S.post.selected.filter((k) => byKey[k]);
  const sel = S.post.selected.map((k) => byKey[k]);
  const vals = valueEvents().slice(0, 40);
  const strong = events.filter((e) => e.p >= 0.6 && e.p <= 0.88).sort((a, b) => b.p - a.p).slice(0, 40);
  const tr = traps().slice(0, 15);
  const type = S.post.trapIdx != null ? "trap" : (S.post.type === "auto" ? (sel[0] ? autoType(sel[0], sel) : "value") : S.post.type);
  const text = S.post.trapIdx != null && tr[S.post.trapIdx] ? buildPost([], "trap", tr[S.post.trapIdx]) : (sel.length ? buildPost(sel, type) : "");
  const item = (e) => { const k = evKey(e); return `<div class="pick-item ${S.post.selected.includes(k) ? "on" : ""}" data-k="${esc(k)}"><input type="checkbox" ${S.post.selected.includes(k) ? "checked" : ""}>
    <div style="flex:1"><div>${SPORTS[e.sport]?.icon || ""} ${esc(e.title)}</div><div class="faint" style="font-size:12px">${esc(e.sub)} · ${fmtTime(e.kickoff)}</div></div>
    <div class="r num"><b class="g">${pct(e.p)}</b>${e.odds ? `<div class="faint" style="font-size:12px">@ ${odds(e.odds)}</div>` : ""}</div></div>`; };
  v.innerHTML = `<h2>Generator objav</h2>
    <p class="section-note">Izberi en dogodek (ali več za Acca / Builder). Tip objave se izbere sam, lahko ga spremeniš. Besedilo je v angleščini, v slogu kanala.</p>
    <div class="grid2">
      <div>
        <h3>Value dogodki</h3><div class="pick-list">${vals.map(item).join("") || `<div class="empty" style="padding:16px">Ni value dogodkov.</div>`}</div>
        <h3>Močni dogodki (60–88 %)</h3><div class="pick-list">${strong.map(item).join("")}</div>
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
    const k = el.dataset.k; S.post.trapIdx = null;
    S.post.selected = S.post.selected.includes(k) ? S.post.selected.filter((x) => x !== k) : [...S.post.selected, k];
    render();
  });
  v.querySelectorAll(".pick-item[data-trap]").forEach((el) => el.onclick = () => { S.post.trapIdx = +el.dataset.trap; S.post.selected = []; render(); });
  v.querySelectorAll("[data-type]").forEach((b) => b.onclick = () => {
    if (b.dataset.type === "trap") { if (S.post.trapIdx == null) S.post.trapIdx = 0; } else { S.post.trapIdx = null; S.post.type = b.dataset.type; }
    render();
  });
  $("#copyPost").onclick = () => {
    const t = $("#postText").value;
    navigator.clipboard?.writeText(t).then(() => { $("#copyPost").textContent = "Kopirano ✓"; setTimeout(() => ($("#copyPost").textContent = "Kopiraj"), 1500); });
  };
  $("#clearPost").onclick = () => { S.post.selected = []; S.post.trapIdx = null; S.post.type = "auto"; render(); };
}

load();
