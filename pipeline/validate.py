"""Validation backtest per sport and competition: python -m pipeline.validate

Walk-forward: the model is re-fitted every STEP days with data from before that day only, then
predicts the next STEP days. For each game:
* p_model from the production model, p_market = closing price without margin
  (football: Pinnacle closing, else the closing average; other sports: the ESPN price),
* p_final = w * p_model + (1 - w) * p_market, for several w (production w first),
* pick rule of the app: the best outcome with p_final x price > 1 (one per game).
  Price = bet365 closing price for football (football-data.co.uk B365C columns, else B365);
  NHL / NBA / UEFA: the ESPN price (no bet365 history exists for them). EuroLeague: no odds.

Scores: Brier (1X2 / moneyline), picks: hit rate against the expected hit rate (mean p_final),
profit and ROI at the price above. Writes data/validation.json (the app shows it and marks
every competition without a backtest as "not validated").
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import math

from . import espn, football, market, models, price_history, soccer_espn, sports
from .util import CACHE_DIR, ROOT, RUN, iso, now_utc, read_json, setup_logging, to_float, write_json

log = logging.getLogger("pipeline.validate")
OUT = ROOT / "data" / "validation.json"
STEP = 14
W_GRID = (0.4, 0.6, 0.2, 1.0)
MIN_GAMES_VALID = 150     # games, and games with market prices


class Book:
    """Scores of one competition for one w."""

    def __init__(self, w: float):
        self.w = w
        self.n = 0
        self.br_model = self.br_final = self.br_mkt = 0.0
        self.n_mkt = 0
        self.picks: list[tuple[float, float, int]] = []      # (p_final, price, won)

    def add(self, p_model: dict, p_mkt: dict | None, outcome: str, prices: dict, extra: list | None = None):
        """p_model / p_mkt: probabilities of the main outcomes (sum 1). prices: decimal prices or None.
        extra: more two-way markets [(p_model, p_mkt or None, won 0/1, price)] (e.g. O/U 2.5)."""
        keys = list(p_model)
        pf = {k: (self.w * p_model[k] + (1 - self.w) * p_mkt[k]) if p_mkt else p_model[k] for k in keys}
        self.n += 1
        self.br_model += sum((p_model[k] - (k == outcome)) ** 2 for k in keys)
        self.br_final += sum((pf[k] - (k == outcome)) ** 2 for k in keys)
        if p_mkt:
            self.n_mkt += 1
            self.br_mkt += sum((p_mkt[k] - (k == outcome)) ** 2 for k in keys)
        cands = [(pf[k] * prices[k], pf[k], prices[k], int(k == outcome)) for k in keys if prices.get(k)]
        for pm, pk, won, price in extra or []:
            if price:
                p = self.w * pm + (1 - self.w) * pk if pk is not None else pm
                cands.append((p * price, p, price, won))
        cands = [c for c in cands if c[0] > 1.0]
        if cands:
            _, p, price, won = max(cands)
            self.picks.append((p, price, won))

    def summary(self) -> dict:
        n_p = len(self.picks)
        won = sum(x[2] for x in self.picks)
        profit = sum((x[1] - 1) if x[2] else -1 for x in self.picks)
        strong = [x for x in self.picks if x[0] * x[1] >= 1.10]
        return {
            "w": self.w, "games": self.n, "games_with_market": self.n_mkt,
            "brier_model": round(self.br_model / self.n, 4) if self.n else None,
            "brier_final": round(self.br_final / self.n, 4) if self.n else None,
            "brier_market": round(self.br_mkt / self.n_mkt, 4) if self.n_mkt else None,
            "picks": n_p, "won": won,
            "hit": round(won / n_p, 4) if n_p else None,
            "expected": round(sum(x[0] for x in self.picks) / n_p, 4) if n_p else None,
            "profit": round(profit, 2), "roi": round(profit / n_p, 4) if n_p else None,
            "strong_picks": len(strong),
            "strong_roi": round(sum((x[1] - 1) if x[2] else -1 for x in strong) / len(strong), 4) if strong else None,
        }


def history(sport: str, done_ids: set[str], league: str | None = None) -> dict[str, dict]:
    """Recorded closing prices (price_history.py) of finished games, used only with MIN_GAMES of them."""
    h = {k: v for k, v in price_history.load(sport, league).items() if k in done_ids}
    return h if len(h) >= price_history.MIN_GAMES else {}


def _result(hg: float, ag: float) -> str:
    return "1" if hg > ag else ("2" if hg < ag else "X")


def _entry(name: str, sport: str, books: dict[float, Book], start: dt.date, end: dt.date, odds_src: str,
           market_src: str, prod_w: float) -> dict:
    res = {f"{w:g}": b.summary() for w, b in books.items()}
    n = books[prod_w].n
    return {"name": name, "sport": sport, "from": start.isoformat(), "to": end.isoformat(), "games": n,
            "prod_w": prod_w, "odds_src": odds_src, "market_src": market_src,
            # the pick rule can only be validated with prices: games without a market do not count
            "validated": n >= MIN_GAMES_VALID and books[prod_w].n_mkt >= MIN_GAMES_VALID, "by_w": res}


def checkpoints(start: dt.date, end: dt.date) -> list[dt.date]:
    out, d = [], start
    while d < end:
        out.append(d)
        d += dt.timedelta(days=STEP)
    return out


# --------------------------------------------------------------------------
# football leagues (football-data.co.uk)
# --------------------------------------------------------------------------
def _price(r: dict, *cols) -> float | None:
    for c in cols:
        v = to_float(r.get(c))
        if v and v > 1:
            return v
    return None


def football_leagues(today: dt.date, divs=None) -> dict:
    from .backtest import FDHistory
    divs = list(divs or football.LEAGUES)
    fd = FDHistory(divs, today, n_seasons=3)
    out = {}
    w0 = market.W["football"]
    for div in divs:
        seasons = fd.codes[-2:]
        rows = sorted([r for c in seasons for r in fd.rows[div].get(c, []) if r.get("FTHG")], key=lambda r: r["_date"])
        if not rows:
            continue
        start, end = rows[0]["_date"], today
        books = {w: Book(w) for w in W_GRID}
        for t in checkpoints(start, end):
            test = [r for r in rows if t <= r["_date"] < t + dt.timedelta(days=STEP)]
            if not test:
                continue
            rg = fd.fit(div, t, ridge=football.DOMESTIC_RIDGE.get(div, 10.0))
            if not rg:
                continue
            for r in test:
                h, a = r["HomeTeam"], r["AwayTeam"]
                if h not in rg.att or a not in rg.att:
                    continue
                hg, ag = to_float(r["FTHG"]), to_float(r["FTAG"])
                pm = market.model_probs(models.score_matrix(*rg.expected(h, a), 10, football.DC_RHO))
                mk = (market.no_vig(football.pinnacle_from_row(r, closing=True), ("1", "X", "2"))
                      or market.no_vig({"1": to_float(r.get("AvgCH")), "X": to_float(r.get("AvgCD")),
                                        "2": to_float(r.get("AvgCA"))}, ("1", "X", "2"))
                      or market.no_vig(football.odds_from_row(r), ("1", "X", "2")))
                ouk = (market.no_vig({"o": to_float(r.get("PC>2.5")), "u": to_float(r.get("PC<2.5"))}, ("o", "u"))
                       or market.no_vig({"o": to_float(r.get("AvgC>2.5")), "u": to_float(r.get("AvgC<2.5"))}, ("o", "u")))
                prices = {"1": _price(r, "B365CH", "B365H"), "X": _price(r, "B365CD", "B365D"),
                          "2": _price(r, "B365CA", "B365A")}
                over = int(hg + ag > 2.5)
                po = pm["over2.5"]
                ex = [(po, ouk["o"] if ouk else None, over, _price(r, "B365C>2.5", "B365>2.5")),
                      (1 - po, ouk["u"] if ouk else None, 1 - over, _price(r, "B365C<2.5", "B365<2.5"))]
                p3 = {k: pm[k] for k in ("1", "X", "2")}
                for b in books.values():
                    b.add(p3, mk, _result(hg, ag), prices, ex)
        out[div] = _entry(football.LEAGUES[div], "football", books, start, end,
                          "bet365 zaključne (football-data.co.uk)", "Pinnacle zaključne brez marže", w0)
        log.info("validate %s: %d games", div, books[w0].n)
    return out


# --------------------------------------------------------------------------
# NHL / NBA (ESPN history with ESPN prices), EuroLeague (no prices)
# --------------------------------------------------------------------------
def _us(sport: str, league: str, today: dt.date) -> dict | None:
    games = read_json(CACHE_DIR / f"espn_{league}.json", {"events": {}})["events"] or \
        espn.update_games("hockey" if sport == "nhl" else "basketball", league, today)
    hist = sorted(sports._history(games), key=lambda g: g["date"])
    if len(hist) < 200:
        return None
    odds = {g["id"]: g.get("odds") or {} for g in games.values()}
    ph = history(sport, {f"{sport}:{g['id']}" for g in hist})
    w0 = market.W[sport]
    w_thin = market.W_THIN
    books = {w: Book(w) for w in sorted({w_thin, *W_GRID})}
    start = hist[0]["date"] + dt.timedelta(days=150)
    for t in checkpoints(start, today):
        train = [dict(g, w=1.0 if g["date"] >= sports._season_start(t) else 0.6) for g in hist if g["date"] < t]
        test = [g for g in hist if t <= g["date"] < t + dt.timedelta(days=STEP)]
        if not test or len(train) < 100:
            continue
        if sport == "nhl":
            r = models.fit_poisson(train, t, half_life_days=120, ridge=6.0)
        else:
            r = models.fit_gauss(train, t, half_life_days=75, ridge=4.0)
        if not r:
            continue
        for g in test:
            h, a = g["home"], g["away"]
            if sport == "nhl":
                if h not in r.att or a not in r.att:
                    continue
                p1 = market.nhl_probs(*r.expected(h, a), None)["1"]
            else:
                if h not in r.off or a not in r.off:
                    continue
                ph, pa = r.expected(h, a)
                p1 = models.norm_cdf((ph - pa) / r.sd_margin)
            o = odds.get(g["id"]) or {}
            mk = market.no_vig({"1": o.get("ml_home"), "2": o.get("ml_away")}, ("1", "2"))
            prices = {"1": o.get("ml_home"), "2": o.get("ml_away")}
            snap = ph.get(f"{sport}:{g['id']}")
            if snap:
                mk, prices = {"1": snap["p"]["1"], "2": snap["p"]["2"]}, snap["prices"]
            win = "1" if g["hc"] > g["ac"] else "2"
            for b in books.values():
                b.add({"1": p1, "2": 1 - p1}, mk, win, prices)
    if ph:
        ent = _entry(sport.upper(), sport, books, start, today, "zgodovina cen (Pinnacle / povprečje)",
                     "shranjene zaključne cene brez marže", w0)
        ent["note"] = f"Kvote iz data/price_history ({len(ph)} tekem)."
        return ent
    ent = _entry(sport.upper(), sport, books, start, today, "ESPN (ne bet365)", "ESPN brez marže (1 stavnica)", w_thin)
    ent["note"] = "Trg ESPN je ena stavnica (tanek trg), zato produkcija uporablja w = 0.6."
    return ent


def euroleague(today: dt.date) -> dict | None:
    y = today.year if today.month >= 9 else today.year - 1
    games = {}
    for yr in (y - 1, y):
        try:
            for raw in sports._el_games(f"E{yr}"):
                g = sports._el_parse(raw)
                if g and g["done"]:
                    if not g["d"].endswith("Z") and "+" not in g["d"][10:]:
                        g["d"] = g["d"][:19] + "Z"
                    games[f"{yr}-{g['id']}"] = {**g, "id": f"{yr}-{g['id']}"}
        except Exception as e:  # noqa: BLE001
            log.warning("EuroLeague E%s: %s", yr, e)
    hist = sorted(sports._history(games), key=lambda g: g["date"])
    if len(hist) < 100:
        return None
    ph = history("euroleague", {f"euroleague:{g['id']}" for g in hist})
    books = {w: Book(w) for w in W_GRID} if ph else {1.0: Book(1.0)}
    start = hist[0]["date"] + dt.timedelta(days=60)
    for t in checkpoints(start, today):
        train = [dict(g, w=1.0 if g["date"] >= sports._season_start(t) else 0.5) for g in hist if g["date"] < t]
        test = [g for g in hist if t <= g["date"] < t + dt.timedelta(days=STEP)]
        r = models.fit_gauss(train, t, half_life_days=90, ridge=4.0) if len(train) >= 60 else None
        if not r or not test:
            continue
        for g in test:
            if g["home"] not in r.off or g["away"] not in r.off:
                continue
            ph, pa = r.expected(g["home"], g["away"])
            p1 = models.norm_cdf((ph - pa) / r.sd_margin)
            snap = ph.get(f"euroleague:{g['id']}")
            mk, prices = ({"1": snap["p"]["1"], "2": snap["p"]["2"]}, snap["prices"]) if snap else (None, {})
            for b in books.values():
                b.add({"1": p1, "2": 1 - p1}, mk, "1" if g["hc"] > g["ac"] else "2", prices)
    if ph:
        ent = _entry("EuroLeague", "euroleague", books, start, today, "zgodovina cen (Pinnacle / povprečje)",
                     "shranjene zaključne cene brez marže", market.W["euroleague"])
        ent["note"] = f"Kvote iz data/price_history ({len(ph)} tekem)."
        return ent
    ent = _entry("EuroLeague", "euroleague", books, start, today, "ni kvot", "ni kvot", 1.0)
    ent["note"] = "Za Evroligo še ni zgodovinskih kvot (zbiranje teče od 16. 10. 2026): samo Brier modela, brez ROI."
    return ent


# --------------------------------------------------------------------------
# UEFA cups (ESPN games and prices, the production cup model)
# --------------------------------------------------------------------------
def uefa(today: dt.date) -> dict:
    from .backtest import FDHistory
    days = soccer_espn.CUP_HISTORY_DAYS
    cup_games, code_of = {}, {}
    for code, (slug, _, kind) in soccer_espn.COMPETITIONS.items():
        if kind != "cup":
            continue
        g = espn.update_games("soccer", slug, today, windows=soccer_espn._windows(today, days))
        cup_games.update(g)
        code_of.update({k: code for k in g})
    hist = sorted(soccer_espn._hist(cup_games), key=lambda x: x["date"])
    if len(hist) < 100:
        return {}
    _, dom_hists, _ = soccer_espn.fit_espn_domestic(today)
    fd = FDHistory(list(football.LEAGUES), today, n_seasons=3)
    odds = {g["id"]: g.get("odds") or {} for g in cup_games.values()}
    ph = {code: history("football", {f"fbe:{code}:{x['id']}" for x in hist}, code) for code in ("UCL", "UEL", "UECL")}
    books = {code: {w: Book(w) for w in sorted({market.W_THIN, *W_GRID})} for code in ("UCL", "UEL", "UECL")}
    start = hist[0]["date"] + dt.timedelta(days=120)
    for t in checkpoints(start, today):
        train = [x for x in hist if t - dt.timedelta(days=days) <= x["date"] < t]
        test = [x for x in hist if t <= x["date"] < t + dt.timedelta(days=STEP)]
        if not test or len(train) < 50:
            continue
        goal_ratings = {d: r for d in football.LEAGUES if (r := fd.fit(d, t, ridge=football.DOMESTIC_RIDGE.get(d, 10.0)))}
        espn_ratings = {c: r for c, gs in dom_hists.items()
                        if (r := models.fit_poisson([x for x in gs if t - dt.timedelta(days=430) <= x["date"] < t],
                                                    t, half_life_days=200, ridge=5.0))}
        teams = sorted({x["home"] for x in train + test} | {x["away"] for x in train + test})
        r, prior, _ = soccer_espn.fit_cup_model(train, teams, t, goal_ratings, espn_ratings)
        for x in test:
            code = code_of.get(x["id"])
            if code not in books:
                continue
            pm = market.model_probs(models.score_matrix(*soccer_espn.predict_cup(r, prior, x["home"], x["away"],
                                                                                   x.get("neutral", False)), 10, -0.06))
            o = odds.get(x["id"]) or {}
            mk = market.no_vig({"1": o.get("ml_home"), "X": o.get("ml_draw"), "2": o.get("ml_away")}, ("1", "X", "2"))
            prices = {"1": o.get("ml_home"), "X": o.get("ml_draw"), "2": o.get("ml_away")}
            snap = ph[code].get(f"fbe:{code}:{x['id']}")
            if snap:
                mk = {k: snap["p"][k] for k in ("1", "X", "2")}
                prices = {k: snap["prices"].get(k) for k in ("1", "X", "2")}
            for b in books[code].values():
                b.add({k: pm[k] for k in ("1", "X", "2")}, mk, _result(x["hc"], x["ac"]), prices)
    out = {}
    for code, bk in books.items():
        name = soccer_espn.COMPETITIONS[code][1]
        ent = _entry(name, "football", bk, start, today, "ESPN (ne bet365)", "ESPN brez marže (1 stavnica)", market.W_THIN)
        ent["note"] = "Pokali: trg ESPN je ena stavnica (tanek trg)."
        out[code] = ent
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="football,nhl,nba,euroleague,uefa")
    args = ap.parse_args(argv)
    setup_logging()
    RUN["mode"] = "full"
    today = now_utc().date()
    data = read_json(OUT, {}) or {}
    comps = data.get("competitions", {})
    for part in args.part.split(","):
        try:
            if part == "football":
                comps.update(football_leagues(today))
            elif part in ("nhl", "nba"):
                e = _us(part, part, today)
                if e:
                    comps[part.upper()] = e
            elif part == "euroleague":
                e = euroleague(today)
                if e:
                    comps["EL"] = e
            elif part == "uefa":
                comps.update(uefa(today))
        except Exception as e:  # noqa: BLE001
            log.exception("validate %s: %s", part, e)
        write_json(OUT, {"generated": iso(now_utc()), "step_days": STEP, "competitions": comps}, compact=False)
    for k, v in comps.items():
        b = v["by_w"][f"{v['prod_w']:g}"]
        log.info("%s: %s games, brier %s (model %s, market %s), picks %s, hit %s vs %s, roi %s",
                 k, v["games"], b["brier_final"], b["brier_model"], b["brier_market"], b["picks"], b["hit"],
                 b["expected"], b["roi"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
