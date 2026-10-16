"""Walk-forward backtests: python -m pipeline.backtest [--part cups,e1,nhl]

Every prediction uses only data from before the match (models are re-fitted at regular
checkpoints). Scores: log-loss and Brier score (lower is better).

* cups: UEFA competitions, last two seasons. Old cup model (ridge 3, full domestic rating,
  football-data.co.uk leagues only, one strength for every other club) against new settings
  (ESPN domestic leagues, league strength from UEFA coefficients, prior scale k, ridge).
  Market reference: ESPN prices without margin, where the cached games have them.
* e1: Championship, last two seasons + this one, against Pinnacle closing prices without
  margin. Also checks for a home / away bias and tests variants of the domestic model.
* nhl: the logged NHL picks and the calibration of the tracked NHL probabilities.

Writes backtest_report.md and prints it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
from collections import defaultdict

from . import espn, football, market, models, soccer_espn
from .util import ROOT, now_utc, read_json, setup_logging

log = logging.getLogger("pipeline.backtest")
EPS = 1e-12
OUT = []


def say(line: str = ""):
    """Print and keep the report on disk after every line (a cancelled run still leaves a report)."""
    OUT.append(line)
    print(line, flush=True)
    (ROOT / "backtest_report.md").write_text("\n".join(OUT), encoding="utf-8")


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------
class Score:
    def __init__(self):
        self.n = 0; self.ll = 0.0; self.br = 0.0; self.n_ou = 0; self.ll_ou = 0.0; self.br_ou = 0.0

    def add(self, p: dict, hg: int, ag: int):
        o = "1" if hg > ag else ("2" if hg < ag else "X")
        self.n += 1
        self.ll += -math.log(max(p[o], EPS))
        self.br += sum((p[k] - (k == o)) ** 2 for k in ("1", "X", "2"))
        if p.get("over2.5") is not None:
            y = hg + ag > 2.5
            q = p["over2.5"]
            self.n_ou += 1
            self.ll_ou += -math.log(max(q if y else 1 - q, EPS))
            self.br_ou += (q - y) ** 2

    def row(self, name: str) -> str:
        if not self.n:
            return f"| {name} | 0 | – | – | – | – |"
        ou = (f"{self.ll_ou / self.n_ou:.4f} | {self.br_ou / self.n_ou:.4f}") if self.n_ou else "– | –"
        return f"| {name} | {self.n} | {self.ll / self.n:.4f} | {self.br / self.n:.4f} | {ou} |"


HEAD = "| model | n | 1X2 log-loss | 1X2 Brier | O/U 2.5 log-loss | O/U 2.5 Brier |\n|---|---|---|---|---|---|"


def probs(lh: float, la: float) -> dict:
    return market.model_probs(models.score_matrix(lh, la, 10, -0.06))


def checkpoints(start: dt.date, end: dt.date, step: int) -> list[dt.date]:
    out, d = [], start
    while d <= end:
        out.append(d)
        d += dt.timedelta(days=step)
    return out


# --------------------------------------------------------------------------
# domestic ratings at a date (football-data.co.uk)
# --------------------------------------------------------------------------
class FDHistory:
    def __init__(self, divs, today: dt.date, n_seasons: int = 4):
        code = football.season_code(today)
        self.codes = [code]
        for _ in range(n_seasons - 1):
            self.codes.insert(0, football.prev_season_code(self.codes[0]))
        self.rows: dict[str, dict[str, list]] = defaultdict(dict)
        for div in divs:
            for c in self.codes:
                try:
                    self.rows[div][c] = football.load_season(div, c, current=(c == code))
                except Exception as e:  # noqa: BLE001
                    log.warning("FD %s %s: %s", div, c, e)
                    self.rows[div][c] = []

    def split(self, div: str, t: dt.date):
        code = football.season_code(t)
        prev = football.prev_season_code(code)
        cur = [r for r in self.rows[div].get(code, []) if r["_date"] < t]
        old = self.rows[div].get(prev, [])
        return cur, old, prev

    def fit(self, div: str, t: dt.date, prev_w: float = 0.5, hl: float = 180, ridge: float = 5.0,
            use_prior: bool = True):
        cur, old, prev = self.split(div, t)
        upper = football.UPPER.get(div)
        upper_prev = self.rows.get(upper, {}).get(prev) if upper else None
        prior = football.newcomer_prior(cur, old, upper_prev) if use_prior else {}
        games = football.to_games(cur, "FTHG", "FTAG") + (football.to_games(old, "FTHG", "FTAG", prev_w) if prev_w else [])
        return models.fit_poisson(games, t, half_life_days=hl, ridge=ridge, prior=prior)


# --------------------------------------------------------------------------
# UEFA cups
# --------------------------------------------------------------------------
OLD_STRENGTH = {
    "E0": 0.36, "SP1": 0.28, "I1": 0.26, "D1": 0.25, "F1": 0.16, "P1": 0.07, "N1": 0.06,
    "B1": -0.02, "T1": -0.04, "E1": -0.02, "G1": -0.12, "SC0": -0.14, "D2": -0.10,
    "I2": -0.18, "SP2": -0.14, "F2": -0.20,
}


def cups(today: dt.date):
    say("## UEFA tekmovanja (walk-forward, zadnji 2 sezoni)")
    days = 800
    cup_games = {}
    for code, (slug, _, kind) in soccer_espn.COMPETITIONS.items():
        if kind != "cup":
            continue
        g = espn.update_games("soccer", slug, today, windows=soccer_espn._windows(today, days), max_requests=2500)
        cup_games.update(g)
    hist = sorted(soccer_espn._hist(cup_games), key=lambda x: x["date"])
    odds_by_id = {g["id"]: g.get("odds") or {} for g in cup_games.values()}
    say(f"Pokalne tekme v predpomnilniku: {len(hist)} ({hist[0]['date'] if hist else '-'} – {hist[-1]['date'] if hist else '-'})")

    # ESPN domestic leagues over the same period
    dom_games, dom_err = {}, []
    for c, slug in soccer_espn.ESPN_DOMESTIC.items():
        try:
            espn._fetch_range("soccer", slug, today - dt.timedelta(days=6), today)
            dom_games[c] = soccer_espn._hist(espn.update_games("soccer", slug, today, max_requests=2500,
                                                               windows=soccer_espn._windows(today, days)))
        except Exception as e:  # noqa: BLE001
            dom_err.append(f"{slug}: {e}")
    say(f"ESPN domače lige: {len(dom_games)} od {len(soccer_espn.ESPN_DOMESTIC)}" +
        (f" (ni na voljo: {', '.join(dom_err)})" if dom_err else ""))

    fd = FDHistory(list(football.LEAGUES), today)
    start = max(today - dt.timedelta(days=days - 365), hist[0]["date"] + dt.timedelta(days=60)) if hist else today
    cps = checkpoints(start, today, 14)

    configs = {"staro (ridge 3, k 1, samo FD lige, neznani -0.30)": dict(ridge=3.0, k=1.0, strengths=OLD_STRENGTH,
                                                                       unknown=-0.30, espn=False, g=1.0)}
    configs["novo ridge 15, k 0.6, g 1 (produkcija)"] = dict(ridge=15.0, k=0.6, espn=True, g=1.0)
    for rg in (8.0, 15.0):
        for k in (0.6, 1.0):
            for gg in (1.5, 2.0, 2.5):
                configs[f"novo ridge {rg:g}, k {k}, g {gg}"] = dict(ridge=rg, k=k, espn=True, g=gg)
    scores = {name: Score() for name in configs}
    mkt = Score()
    mkt_pairs = defaultdict(Score)       # each config on the games that have market prices
    per_cp = []
    for t in cps:
        train = [x for x in hist if t - dt.timedelta(days=days) <= x["date"] < t]
        test = [x for x in hist if t <= x["date"] < t + dt.timedelta(days=14)]
        if not test or len(train) < 50:
            continue
        goal_ratings = {div: r for div in football.LEAGUES if (r := fd.fit(div, t))}
        espn_ratings = {}
        for c, games in dom_games.items():
            g = [x for x in games if t - dt.timedelta(days=430) <= x["date"] < t]
            if (r := models.fit_poisson(g, t, half_life_days=200, ridge=5.0)):
                espn_ratings[c] = r
        teams = sorted({x["home"] for x in train + test} | {x["away"] for x in train + test})
        per_cp.append((t, train, test, goal_ratings, espn_ratings, teams))
        for name, cfg in configs.items():
            r, prior, _ = soccer_espn.fit_cup_model(train, teams, t, goal_ratings,
                                                    espn_ratings if cfg["espn"] else None,
                                                    ridge=cfg["ridge"], k=cfg["k"],
                                                    strengths=cfg.get("strengths"), unknown=cfg.get("unknown"),
                                                    g=cfg.get("g"))
            for x in test:
                p = probs(*soccer_espn.predict_cup(r, prior, x["home"], x["away"], x.get("neutral", False)))
                scores[name].add(p, x["hc"], x["ac"])
                o = odds_by_id.get(x["id"]) or {}
                mp = market.no_vig({"1": o.get("ml_home"), "X": o.get("ml_draw"), "2": o.get("ml_away")}, ("1", "X", "2"))
                if mp:
                    mkt_pairs[name].add({k: p[k] for k in ("1", "X", "2")}, x["hc"], x["ac"])
                    if name == next(iter(configs)):
                        mkt.add(mp, x["hc"], x["ac"])
    say(f"Kontrolne točke: {len(per_cp)} (vsakih 14 dni), napovedanih tekem: {scores[next(iter(scores))].n}")
    say()
    say(HEAD)
    for name, sc in scores.items():
        say(sc.row(name))
    say()
    if mkt.n:
        say(f"Primerjava s trgom (ESPN brez marže) na {mkt.n} tekmah, ki imajo kvote:")
        say(HEAD)
        say(mkt.row("trg (ESPN brez marže)"))
        for name, sc in mkt_pairs.items():
            say(sc.row(name))
    else:
        say("Za pretekle pokalne tekme v predpomnilniku ni kvot, zato tržne reference ni.")
    # agreement with the market on the coming games (ESPN prices without margin)
    up = [g for g in cup_games.values() if not g.get("done") and g.get("odds", {}).get("ml_home")]
    if up and per_cp:
        goal_ratings = {div: r for div in football.LEAGUES if (r := fd.fit(div, today))}
        espn_ratings = {c: r for c, games in dom_games.items()
                        if (r := models.fit_poisson([x for x in games if x["date"] >= today - dt.timedelta(days=430)],
                                                    today, half_life_days=200, ridge=5.0))}
        train = [x for x in hist if x["date"] >= today - dt.timedelta(days=days)]
        teams = sorted({x["home"] for x in train} | {x["away"] for x in train} | {g["h"] for g in up} | {g["a"] for g in up})
        say()
        say(f"Ujemanje s trgom na {len(up)} prihodnjih pokalnih tekmah (ESPN brez marže), povprečna |model − trg| pri 1X2:")
        for name, cfg in configs.items():
            r, prior, _ = soccer_espn.fit_cup_model(train, teams, today, goal_ratings, espn_ratings if cfg["espn"] else None,
                                                    ridge=cfg["ridge"], k=cfg["k"], strengths=cfg.get("strengths"),
                                                    unknown=cfg.get("unknown"), g=cfg.get("g"))
            gaps, big = [], 0
            for g in up:
                o = g["odds"]
                mp = market.no_vig({"1": o.get("ml_home"), "X": o.get("ml_draw"), "2": o.get("ml_away")}, ("1", "X", "2"))
                if not mp:
                    continue
                p = probs(*soccer_espn.predict_cup(r, prior, g["h"], g["a"], g.get("neutral", False)))
                gap = max(abs(p[k] - mp[k]) for k in ("1", "X", "2"))
                gaps.append(gap); big += gap > 0.10
            if gaps:
                say(f"- {name}: povprečje {sum(gaps) / len(gaps) * 100:.1f} o. t., nad 10 o. t.: {big} od {len(gaps)}")

    def total(n):
        sc_ = scores[n]
        return sc_.ll / max(1, sc_.n) + (sc_.ll_ou / sc_.n_ou if sc_.n_ou else 0)
    best = min((n for n in scores if n.startswith("novo")), key=total)
    say()
    say("Skupna ocena = 1X2 log-loss + O/U 2.5 log-loss (nižje je bolje):")
    for n in scores:
        say(f"- {n}: {total(n):.4f}")
    say()
    say(f"Najboljša nastavitev: **{best}**")
    rg, best_k, best_g = configs[best]["ridge"], configs[best]["k"], configs[best]["g"]
    return {"ridge": rg, "k": best_k, "g": best_g}


# --------------------------------------------------------------------------
# Championship
# --------------------------------------------------------------------------
def e1(today: dt.date, div: str = "E1"):
    say(f"## {football.LEAGUES[div]} ({div}) proti Pinnacle (walk-forward)")
    fd = FDHistory([div, football.UPPER.get(div, div)], today, n_seasons=4)
    start = dt.date(int("20" + fd.codes[1][:2]), 8, 1)         # two seasons back
    cps = checkpoints(start, today, 7)
    variants = {
        "produkcija (lani 0.5, hl 180, ridge 5, prior novincev)": dict(),
        "lani 0.25": dict(prev_w=0.25),
        "brez lanske sezone": dict(prev_w=0.0),
        "brez priorja novincev": dict(use_prior=False),
        "hl 90": dict(hl=90),
        "ridge 10": dict(ridge=10.0),
    }
    sc = {n: Score() for n in variants}
    pin_close, pin_open, ref_avg = Score(), Score(), Score()
    say("Pokritost s kvotami po sezonah (vrstice z rezultatom):")
    say("| sezona | tekme | Pinnacle ob objavi (PSH) | Pinnacle zaključne (PSCH) | povprečje zaključne (AvgCH) |\n|---|---|---|---|---|")
    for c in fd.codes:
        rows = [r for r in fd.rows[div].get(c, []) if r.get("FTHG")]
        cnt = lambda col: sum(1 for r in rows if (football.to_float(r.get(col)) or 0) > 1)
        say(f"| 20{c[:2]}/{c[2:]} | {len(rows)} | {cnt('PSH')} | {cnt('PSCH')} | {cnt('AvgCH')} |")
    say()
    bias = defaultdict(lambda: defaultdict(list))   # variant -> season -> list of (model - pinnacle) for 1, X, 2
    actual = defaultdict(lambda: [0, 0, 0, 0])      # season -> counts 1, X, 2, n
    rows_all = []
    for c in fd.codes:
        rows_all += [(c, r) for r in fd.rows[div].get(c, [])]
    for t in cps:
        week = [(c, r) for c, r in rows_all if t <= r["_date"] < t + dt.timedelta(days=7) and r.get("FTHG")]
        if not week:
            continue
        fits = {n: fd.fit(div, t, **kw) for n, kw in variants.items()}
        for c, r in week:
            hg, ag = int(float(r["FTHG"])), int(float(r["FTAG"]))
            pc = market.no_vig(football.pinnacle_from_row(r, closing=True), ("1", "X", "2"))
            po = market.no_vig(football.pinnacle_from_row(r), ("1", "X", "2"))
            if not pc:
                # no Pinnacle in the file: use the closing market average (or the average) without margin
                avg = {"1": football.to_float(r.get("AvgCH")) or football.to_float(r.get("AvgH")),
                       "X": football.to_float(r.get("AvgCD")) or football.to_float(r.get("AvgD")),
                       "2": football.to_float(r.get("AvgCA")) or football.to_float(r.get("AvgA"))}
                pc = market.no_vig(avg, ("1", "X", "2"))
                if not pc:
                    continue
                ref_avg.add(pc, int(float(r["FTHG"])), int(float(r["FTAG"])))
            ouc = market.no_vig(football.pinnacle_from_row(r, closing=True), ("over2.5", "under2.5"))
            if ouc:
                pc["over2.5"] = ouc["over2.5"]
            if football.pinnacle_from_row(r, closing=True):
                pin_close.add(pc, hg, ag)
            if po:
                pin_open.add(po, hg, ag)
            o = 0 if hg > ag else (1 if hg == ag else 2)
            actual[c][o] += 1; actual[c][3] += 1
            for n, fit in fits.items():
                if not fit or r["HomeTeam"] not in fit.att or r["AwayTeam"] not in fit.att:
                    continue
                p = probs(*fit.expected(r["HomeTeam"], r["AwayTeam"]))
                sc[n].add(p, hg, ag)
                bias[n][c].append((p["1"] - pc["1"], p["X"] - pc["X"], p["2"] - pc["2"], p["1"], pc["1"]))
    say(HEAD)
    say(pin_close.row("Pinnacle zaključne kvote (brez marže)"))
    say(pin_open.row("Pinnacle kvote ob objavi (brez marže)"))
    say(ref_avg.row("povprečje stavnic, zaključne (brez marže), kjer Pinnacla ni"))
    for n, s in sc.items():
        say(s.row(n))
    say()
    say("Pristranskost: povprečna razlika model − trg (Pinnacle zaključne, sicer povprečje zaključnih), v odstotnih točkah:")
    say("| model | sezona | n | domači | remi | gostje | dejanski delež zmag domačih |\n|---|---|---|---|---|---|---|")
    for n in variants:
        for c in fd.codes:
            v = bias[n].get(c)
            if not v:
                continue
            m = [sum(x[i] for x in v) / len(v) * 100 for i in range(3)]
            a = actual[c]
            share = a[0] / a[3] * 100 if a[3] else float("nan")
            say(f"| {n} | 20{c[:2]}/{c[2:]} | {len(v)} | {m[0]:+.1f} | {m[1]:+.1f} | {m[2]:+.1f} | {share:.1f} % |")
    # is the bias of the production model bigger than chance? (standard error of the mean)
    prod = next(iter(variants))
    for c in fd.codes:
        v = bias[prod].get(c)
        if not v or len(v) < 20:
            continue
        d = [x[2] for x in v]
        mean = sum(d) / len(d)
        sd = math.sqrt(sum((x - mean) ** 2 for x in d) / (len(d) - 1))
        se = sd / math.sqrt(len(d))
        say(f"- 20{c[:2]}/{c[2:]}: model daje gostom {mean * 100:+.1f} o. t. več kot Pinnacle "
            f"(± {1.96 * se * 100:.1f} o. t. pri 95 %), n = {len(d)}")


# --------------------------------------------------------------------------
# all football-data.co.uk divisions: domestic ridge
# --------------------------------------------------------------------------
RIDGES = (5.0, 8.0, 10.0, 15.0)


def domestic(today: dt.date):
    say("## Domače lige: ridge (walk-forward, zadnji 2 sezoni + tekoča)")
    divs = list(football.LEAGUES)
    fd = FDHistory(divs, today, n_seasons=4)
    start = dt.date(int("20" + fd.codes[1][:2]), 8, 1)
    cps = checkpoints(start, today, 7)
    tot = {rg: Score() for rg in RIDGES}
    mkt_all = Score()
    say("| liga | n | " + " | ".join(f"ridge {rg:g}" for rg in RIDGES) + " | trg |")
    say("|---|---|" + "---|" * (len(RIDGES) + 1))
    per_div = {}
    for div in divs:
        sc = {rg: Score() for rg in RIDGES}
        mk = Score()
        rows_all = [r for c in fd.codes for r in fd.rows[div].get(c, []) if r.get("FTHG")]
        for t in cps:
            week = [r for r in rows_all if t <= r["_date"] < t + dt.timedelta(days=7)]
            if not week:
                continue
            fits = {rg: fd.fit(div, t, ridge=rg) for rg in RIDGES}
            for r in week:
                hg, ag = int(float(r["FTHG"])), int(float(r["FTAG"]))
                ok = all(f and r["HomeTeam"] in f.att and r["AwayTeam"] in f.att for f in fits.values())
                if not ok:
                    continue
                for rg, f in fits.items():
                    p = probs(*f.expected(r["HomeTeam"], r["AwayTeam"]))
                    sc[rg].add(p, hg, ag); tot[rg].add(p, hg, ag)
                ref = market.no_vig(football.pinnacle_from_row(r, closing=True), ("1", "X", "2")) or market.no_vig(
                    {"1": football.to_float(r.get("AvgCH")) or football.to_float(r.get("AvgH")),
                     "X": football.to_float(r.get("AvgCD")) or football.to_float(r.get("AvgD")),
                     "2": football.to_float(r.get("AvgCA")) or football.to_float(r.get("AvgA"))}, ("1", "X", "2"))
                if ref:
                    mk.add(ref, hg, ag); mkt_all.add(ref, hg, ag)
        per_div[div] = sc
        cells = []
        best = min(RIDGES, key=lambda rg: sc[rg].ll / max(1, sc[rg].n) + sc[rg].ll_ou / max(1, sc[rg].n_ou))
        for rg in RIDGES:
            v = sc[rg].ll / max(1, sc[rg].n) + sc[rg].ll_ou / max(1, sc[rg].n_ou)
            cells.append(f"**{v:.4f}**" if rg == best else f"{v:.4f}")
        say(f"| {div} | {sc[RIDGES[0]].n} | " + " | ".join(cells) + f" | {mk.ll / max(1, mk.n):.4f} (1X2) |")
    say()
    say("Vse lige skupaj (1X2 + O/U 2.5 log-loss):")
    say(HEAD)
    for rg in RIDGES:
        say(tot[rg].row(f"ridge {rg:g}"))
    say(mkt_all.row("trg (Pinnacle ali povprečje, brez marže)"))
    best = min(RIDGES, key=lambda rg: tot[rg].ll / tot[rg].n + tot[rg].ll_ou / tot[rg].n_ou)
    say()
    say(f"Najboljši skupni ridge: **{best:g}**")
    return {"ridge": best}


# --------------------------------------------------------------------------
# NHL
# --------------------------------------------------------------------------
def nhl():
    say("## NHL: picki in kalibracija")
    d = read_json(ROOT / "data" / "picks_log.json", {}) or {}
    picks = [p for p in d.get("picks", []) if p["sport"] == "nhl"]
    done = [p for p in picks if p["status"] in ("won", "lost")]
    say(f"Picki: {len(picks)}, zaključeni {len(done)}: {sum(p['status'] == 'won' for p in done)} zadetkov, "
        f"profit {sum((p['odds'] - 1) if p['status'] == 'won' else -1 for p in done):+.2f} enot")
    say("| dan | tekma | pick | model | kvota | implicirano | izid |\n|---|---|---|---|---|---|---|")
    for p in sorted(picks, key=lambda p: p["kickoff"]):
        say(f"| {p['day']} | {p['home']} – {p['away']} | {p['label']} | {p['p'] * 100:.0f} % | {p['odds']:.2f} | "
            f"{100 / p['odds']:.0f} % | {p['status']} |")
    kinds = defaultdict(list)
    for p in done:
        k = "izid" if p["scope"] == "match" else ("hendikep" if p["scope"] == "spread" else "skupaj golov")
        kinds[k].append(p)
    say()
    say("| vrsta | n | zadetki | pričakovano (model) | implicirano (kvota) |\n|---|---|---|---|---|")
    for k, lst in kinds.items():
        say(f"| {k} | {len(lst)} | {sum(p['status'] == 'won' for p in lst)} | "
            f"{sum(p['p'] for p in lst):.1f} | {sum(1 / p['odds'] for p in lst):.1f} |")
    tr = [t for t in d.get("tracked", []) if t[1] == "nhl" and t[3] in (0, 1)]
    say()
    say(f"Kalibracija vseh spremljanih NHL verjetnosti (n = {len(tr)}):")
    say("| skupina | n | napovedano | dejansko |\n|---|---|---|---|")
    for lo in range(0, 100, 10):
        b = [t for t in tr if lo / 100 <= t[2] < (lo + 10) / 100]
        if b:
            say(f"| {lo}–{lo + 10} % | {len(b)} | {sum(t[2] for t in b) / len(b) * 100:.1f} % | "
                f"{sum(t[3] for t in b) / len(b) * 100:.1f} % |")
    if len(done) < 50:
        say()
        say(f"Opomba: {len(done)} zaključenih pickov je premalo za zanesljivo oceno. Pri ~1.8 kvoti je "
            f"standardni odklon dobička na 15 pickih okoli ±3.5 enote.")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="nhl,e1,domestic,cups")
    args = ap.parse_args(argv)
    setup_logging()
    today = now_utc().date()
    say(f"# Backtest {today.isoformat()}")
    say()
    parts = args.part.split(",")
    if "nhl" in parts:
        nhl(); say()
    if "e1" in parts:
        e1(today); say()
    if "domestic" in parts:
        say(f"```json\n{json.dumps(domestic(today))}\n```"); say()
    if "cups" in parts:
        rec = cups(today); say()
        say(f"```json\n{json.dumps(rec)}\n```")
    (ROOT / "backtest_report.md").write_text("\n".join(OUT), encoding="utf-8")


if __name__ == "__main__":
    main()
