"""Football: results, fixtures and odds from football-data.co.uk (free CSV files).

Data: https://www.football-data.co.uk/data.php (results + shots + corners + cards + odds)
      https://www.football-data.co.uk/fixtures.csv (next fixtures with bookmaker odds)
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import math

from . import espn, models
from .util import CACHE_DIR, cached_get, match_name, parse_iso, to_float

log = logging.getLogger("pipeline.football")

BASE = "https://www.football-data.co.uk"

LEAGUES = {
    "E0": "Premier League", "E1": "Championship", "SP1": "LaLiga", "I1": "Serie A",
    "D1": "Bundesliga", "F1": "Ligue 1", "N1": "Eredivisie", "P1": "Primeira Liga",
    "B1": "Jupiler Pro League", "T1": "Süper Lig", "SC0": "Scottish Premiership",
    "G1": "Super League Greece", "D2": "2. Bundesliga", "I2": "Serie B", "SP2": "LaLiga 2", "F2": "Ligue 2",
}

DC_RHO = -0.06

# ESPN league slugs, used for fixtures that football-data.co.uk has not published yet and for lineups
ESPN_SLUG = {
    "E0": "eng.1", "E1": "eng.2", "SP1": "esp.1", "SP2": "esp.2", "I1": "ita.1", "I2": "ita.2",
    "D1": "ger.1", "D2": "ger.2", "F1": "fra.1", "F2": "fra.2", "N1": "ned.1", "P1": "por.1",
    "B1": "bel.1", "T1": "tur.1", "SC0": "sco.1", "G1": "gre.1",
}


def espn_fixtures(div: str, teams: list[str], today: dt.date, horizon_days: int) -> tuple[list[dict], list[str]]:
    """Upcoming fixtures of a division from ESPN, as football-data.co.uk-like rows (team names mapped)."""
    slug = ESPN_SLUG.get(div)
    if not slug:
        return [], []
    games, _, _ = espn._fetch_range("soccer", slug, today, today + dt.timedelta(days=horizon_days))
    rows, unmatched = [], []
    for g in games:
        if g.get("done") or not g.get("d"):
            continue
        h = match_name(g["h"], teams)
        a = match_name(g["a"], teams)
        if not h or not a or h == a:
            unmatched.append(f"{g['h']} - {g['a']}")
            continue
        o = g.get("odds", {})
        row = {"Div": div, "HomeTeam": h, "AwayTeam": a, "_date": parse_iso(g["d"]).date(),
               "_kickoff": parse_iso(g["d"]).strftime("%Y-%m-%dT%H:%M:%SZ"), "_espn": {"slug": slug, "id": g["id"]},
               "AvgH": o.get("ml_home"), "AvgD": o.get("ml_draw"), "AvgA": o.get("ml_away")}
        if o.get("total") == 2.5:
            row["Avg>2.5"], row["Avg<2.5"] = o.get("over"), o.get("under")
        rows.append(row)
    return rows, unmatched


def merge_fixtures(csv_rows: list[dict], espn_rows: list[dict]) -> list[dict]:
    """football-data.co.uk rows win (better odds); ESPN adds missing games and its event id."""
    out = list(csv_rows)
    for e in espn_rows:
        same = next((r for r in out if r["HomeTeam"] == e["HomeTeam"] and r["AwayTeam"] == e["AwayTeam"]
                     and abs((r["_date"] - e["_date"]).days) <= 1), None)
        if same:
            same.setdefault("_espn", e["_espn"])
        else:
            out.append(e)
    return out


def season_code(d: dt.date) -> str:
    y = d.year if d.month >= 7 else d.year - 1
    return f"{y % 100:02d}{(y + 1) % 100:02d}"


def prev_season_code(code: str) -> str:
    a = int(code[:2]) - 1
    return f"{a % 100:02d}{(a + 1) % 100:02d}"


def parse_date(s: str) -> dt.date | None:
    s = (s or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def parse_csv(text: str) -> list[dict]:
    rows = []
    reader = csv.DictReader(io.StringIO(text))
    for r in reader:
        r = {(k or "").strip().lstrip("﻿"): (v or "").strip() for k, v in r.items() if k}
        if not r.get("HomeTeam") or not r.get("AwayTeam"):
            continue
        d = parse_date(r.get("Date", ""))
        if not d:
            continue
        r["_date"] = d
        rows.append(r)
    return rows


def ints(r: dict, *keys):
    out = []
    for k in keys:
        v = to_float(r.get(k))
        out.append(None if v is None else int(v))
    return out


def load_season(div: str, code: str, current: bool) -> list[dict]:
    """Load one season CSV. Past seasons are cached on disk (they do not change)."""
    cache = CACHE_DIR / "football" / f"{div}_{code}.csv"
    if not current and cache.exists():
        text = cache.read_text(encoding="utf-8")
    else:
        text = cached_get(f"{BASE}/mmz4281/{code}/{div}.csv", as_json=False)
        if not current:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(text, encoding="utf-8")
    return parse_csv(text)


def odds_from_row(r: dict) -> dict:
    """Best available market-average odds from a football-data.co.uk row."""
    def first(*keys):
        for k in keys:
            v = to_float(r.get(k))
            if v and v > 1.0:
                return v
        return None
    o = {
        "1": first("AvgH", "B365H", "PSH", "BWH", "MaxH"),
        "X": first("AvgD", "B365D", "PSD", "BWD", "MaxD"),
        "2": first("AvgA", "B365A", "PSA", "BWA", "MaxA"),
        "over2.5": first("Avg>2.5", "B365>2.5", "P>2.5", "Max>2.5"),
        "under2.5": first("Avg<2.5", "B365<2.5", "P<2.5", "Max<2.5"),
        "ahh": to_float(r.get("AHh")),
        "ah_home": first("AvgAHH", "B365AHH", "PAHH"),
        "ah_away": first("AvgAHA", "B365AHA", "PAHA"),
    }
    out = {k: v for k, v in o.items() if v is not None}
    pin = pinnacle_from_row(r)
    if pin:
        out["pin"] = pin
    return out


def pinnacle_from_row(r: dict, closing: bool = False) -> dict:
    """Pinnacle prices (the sharpest book in the file): opening/current or closing."""
    keys = ({"1": "PSCH", "X": "PSCD", "2": "PSCA", "over2.5": "PC>2.5", "under2.5": "PC<2.5"} if closing else
            {"1": "PSH", "X": "PSD", "2": "PSA", "over2.5": "P>2.5", "under2.5": "P<2.5"})
    out = {}
    for k, col in keys.items():
        v = to_float(r.get(col))
        if v and v > 1.0:
            out[k] = v
    return out if all(k in out for k in ("1", "X", "2")) else {}


def attach_odds(markets: list[dict], odds: dict):
    for m in markets:
        if m["stat"] != "goals":
            continue
        key = None
        if m["scope"] == "match" and m["kind"] in ("1", "X", "2"):
            key = m["kind"]
        elif m["scope"] == "total" and m["line"] == 2.5:
            key = f"{m['kind']}2.5"
        if key and key in odds:
            m["odds"] = round(odds[key], 3)


def form_string(games: list[dict], team: str, n: int = 6) -> list[dict]:
    rows = [g for g in games if g["home"] == team or g["away"] == team]
    rows.sort(key=lambda g: g["date"])
    out = []
    for g in rows[-n:]:
        home = g["home"] == team
        gf, ga = (g["hc"], g["ac"]) if home else (g["ac"], g["hc"])
        res = "W" if gf > ga else ("L" if gf < ga else "D")
        out.append({"r": res, "s": f"{gf}-{ga}", "o": g["away"] if home else g["home"], "h": home,
                    "d": g["date"].isoformat()})
    return out


def team_season_stats(rows: list[dict], team: str) -> dict:
    n = gf = ga = sf = sa = sot = cf = ca = btts = o25 = cs = 0
    for r in rows:
        h, a = r["HomeTeam"], r["AwayTeam"]
        if team not in (h, a):
            continue
        fthg, ftag, hs, as_, hst, ast, hc, ac = ints(r, "FTHG", "FTAG", "HS", "AS", "HST", "AST", "HC", "AC")
        if fthg is None:
            continue
        home = h == team
        n += 1
        my, op = (fthg, ftag) if home else (ftag, fthg)
        gf += my; ga += op
        btts += my > 0 and op > 0
        o25 += my + op > 2.5
        cs += op == 0
        if hs is not None:
            sf += hs if home else as_; sa += as_ if home else hs
        if hst is not None:
            sot += hst if home else ast
        if hc is not None:
            cf += hc if home else ac; ca += ac if home else hc
    if not n:
        return {}
    return {"gp": n, "gf": round(gf / n, 2), "ga": round(ga / n, 2), "sf": round(sf / n, 1),
            "sa": round(sa / n, 1), "sot": round(sot / n, 1), "cf": round(cf / n, 1), "ca": round(ca / n, 1),
            "btts": round(btts / n, 2), "o25": round(o25 / n, 2), "cs": round(cs / n, 2)}


def to_games(rows: list[dict], h_key: str, a_key: str, w: float = 1.0) -> list[dict]:
    out = []
    for r in rows:
        hv, av = ints(r, h_key, a_key)
        if hv is None or av is None:
            continue
        out.append({"home": r["HomeTeam"], "away": r["AwayTeam"], "hc": hv, "ac": av, "date": r["_date"], "w": w})
    return out


def card_games(rows: list[dict], w: float) -> list[dict]:
    out = []
    for r in rows:
        hy, ay, hr, ar = ints(r, "HY", "AY", "HR", "AR")
        if hy is None or ay is None:
            continue
        out.append({"home": r["HomeTeam"], "away": r["AwayTeam"], "hc": hy + (hr or 0), "ac": ay + (ar or 0),
                    "date": r["_date"], "w": w})
    return out


UPPER = {"E1": "E0", "D2": "D1", "I2": "I1", "SP2": "SP1", "F2": "F1"}


def _teams(rows: list[dict]) -> set[str]:
    return {r["HomeTeam"] for r in rows} | {r["AwayTeam"] for r in rows}


def newcomer_prior(cur_rows: list[dict], prev_rows: list[dict], upper_prev_rows: list[dict] | None = None) -> dict:
    """Start values for teams that are new in the division.
    Promoted teams are usually weaker than average, relegated teams usually stronger."""
    prev_teams = _teams(prev_rows)
    if not prev_teams:
        return {}
    relegated = _teams(upper_prev_rows or [])
    return {t: ((0.15, -0.15) if t in relegated else (-0.15, 0.15)) for t in _teams(cur_rows) - prev_teams}


def build(today: dt.date, horizon_days: int = 7) -> dict:
    """Return {"matches": [...], "results": {...}, "ratings": {...}, "team_stats": {...}, "errors": [...]}"""
    code = season_code(today)
    prev = prev_season_code(code)
    errors: list[str] = []
    data: dict[str, dict] = {}
    for div in LEAGUES:
        try:
            cur = load_season(div, code, current=True)
        except Exception as e:  # noqa: BLE001
            cur = []
            errors.append(f"{div} {code}: {e}")
        try:
            old = load_season(div, prev, current=False)
        except Exception as e:  # noqa: BLE001
            old = []
            errors.append(f"{div} {prev}: {e}")
        data[div] = {"cur": cur, "prev": old}
        log.info("football %s: %d current, %d previous rows", div, len(cur), len(old))

    try:
        fx_rows = parse_csv(cached_get(f"{BASE}/fixtures.csv", as_json=False))
    except Exception as e:  # noqa: BLE001
        fx_rows = []
        errors.append(f"fixtures.csv: {e}")

    matches, ratings_out, team_stats, results = [], {}, {}, {}
    goal_ratings: dict[str, models.PoissonRatings] = {}
    shot_ratings: dict[str, models.PoissonRatings] = {}
    for div, d in data.items():
        cur, old = d["cur"], d["prev"]
        # results for settlement
        for r in cur:
            fthg, ftag, hc, ac, hy, ay, hr, ar, hst, ast = ints(r, "FTHG", "FTAG", "HC", "AC", "HY", "AY", "HR", "AR",
                                                               "HST", "AST")
            if fthg is None:
                continue
            key = match_key(div, r["_date"], r["HomeTeam"], r["AwayTeam"])
            results[key] = {"goals": [fthg, ftag], "corners": [hc, ac],
                            "cards": [None if hy is None else hy + (hr or 0), None if ay is None else ay + (ar or 0)],
                            "sot": [hst, ast]}
        prior = newcomer_prior(cur, old, data.get(UPPER.get(div, ""), {}).get("prev"))
        # previous season counts half (on top of time decay)
        goals = to_games(cur, "FTHG", "FTAG") + to_games(old, "FTHG", "FTAG", 0.5)
        rg = models.fit_poisson(goals, today, half_life_days=180, ridge=5.0, prior=prior)
        if not rg:
            continue
        goal_ratings[div] = rg
        rc = models.fit_poisson(to_games(cur, "HC", "AC") + to_games(old, "HC", "AC", 0.5), today, 180, 8.0)
        rk = models.fit_poisson(card_games(cur, 1.0) + card_games(old, 0.5), today, 180, 8.0)
        rsh = models.fit_poisson(to_games(cur, "HS", "AS") + to_games(old, "HS", "AS", 0.5), today, 180, 6.0,
                                 prior=prior)
        if rsh:
            shot_ratings[div] = rsh
        rs = models.fit_poisson(to_games(cur, "HST", "AST") + to_games(old, "HST", "AST", 0.5), today, 180, 6.0,
                                prior=prior)
        ratings_out[div] = {"home_adv": round(math.exp(rg.home), 3),
                            "avg_goals": round(rg.league_avg * 2, 2), "table": rg.table()}
        all_games = to_games(cur, "FTHG", "FTAG")
        team_stats[div] = {}
        fixtures = [r for r in fx_rows if r.get("Div") == div
                    and today <= r["_date"] <= today + dt.timedelta(days=horizon_days)]
        try:
            e_rows, unmatched = espn_fixtures(div, rg.teams, today, horizon_days)
            fixtures = merge_fixtures(fixtures, e_rows)
            if unmatched:
                errors.append(f"{div}: ESPN names not matched: {', '.join(unmatched[:4])}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{div} ESPN fixtures: {e}")
        for r in fixtures:
            h, a = r["HomeTeam"], r["AwayTeam"]
            if h not in rg.att or a not in rg.att:
                errors.append(f"{div}: no data for {h} or {a}")
                continue
            lh, la = rg.expected(h, a)
            M = models.score_matrix(lh, la, 10, DC_RHO)
            mk = models.goal_markets(M)
            extra = {}
            if rc and h in rc.att and a in rc.att:
                ch, ca = rc.expected(h, a)
                mk += models.count_markets(ch, ca, "corners", "Koti", "Koti", (7.5, 8.5, 9.5, 10.5, 11.5), (3.5, 4.5, 5.5))
                extra["corners"] = [round(ch, 2), round(ca, 2)]
            if rk and h in rk.att and a in rk.att:
                kh, ka = rk.expected(h, a)
                mk += models.count_markets(kh, ka, "cards", "Kartoni", "Kartoni", (2.5, 3.5, 4.5, 5.5), (1.5, 2.5))
                extra["cards"] = [round(kh, 2), round(ka, 2)]
            if rs and h in rs.att and a in rs.att:
                sh, sa = rs.expected(h, a)
                mk += models.count_markets(sh, sa, "sot", "Streli v okvir", "Streli v okvir",
                                           (6.5, 7.5, 8.5, 9.5), (2.5, 3.5, 4.5, 5.5))
                extra["sot"] = [round(sh, 2), round(sa, 2)]
            odds = odds_from_row(r)
            attach_odds(mk, odds)
            dist = models.total_distribution(M)
            kickoff = r.get("_kickoff") or kickoff_iso(r)
            for t in (h, a):
                if t not in team_stats[div]:
                    team_stats[div][t] = team_season_stats(cur, t)
            matches.append({
                "id": match_key(div, r["_date"], h, a),
                "sport": "football", "league": div, "league_name": LEAGUES[div],
                "kickoff": kickoff, "home": h, "away": a, **({"espn": r["_espn"]} if r.get("_espn") else {}),
                "exp": [round(lh, 2), round(la, 2)],
                "extra": extra,
                "top_scores": models.top_scores(M),
                "total_dist": dist,
                "total_mode": max(dist, key=lambda x: x["p"])["n"],
                "markets": mk,
                "odds_raw": odds,
                "form": {"home": form_string(all_games, h), "away": form_string(all_games, a)},
                "stats": {"home": team_stats[div][h], "away": team_stats[div][a]},
            })
    matches.sort(key=lambda m: m["kickoff"])
    return {"matches": matches, "results": results, "ratings": ratings_out, "errors": errors,
            "goal_ratings": goal_ratings, "shot_ratings": shot_ratings, "season_rows": {d: v["cur"] + v["prev"] for d, v in data.items()},
            "cur_rows": {d: v["cur"] for d, v in data.items()}}


def kickoff_iso(r: dict) -> str:
    """football-data.co.uk times are UK local time. Convert to UTC."""
    t = (r.get("Time") or "15:00").strip() or "15:00"
    try:
        hh, mm = (int(x) for x in t.split(":")[:2])
    except ValueError:
        hh, mm = 15, 0
    d = r["_date"]
    try:
        from zoneinfo import ZoneInfo
        local = dt.datetime(d.year, d.month, d.day, hh, mm, tzinfo=ZoneInfo("Europe/London"))
        u = local.astimezone(dt.timezone.utc)
    except Exception:  # noqa: BLE001
        u = dt.datetime(d.year, d.month, d.day, hh, mm, tzinfo=dt.timezone.utc)
    return u.strftime("%Y-%m-%dT%H:%M:%SZ")


def match_key(div: str, d: dt.date, h: str, a: str) -> str:
    return f"fb:{div}:{d.isoformat()}:{h}:{a}"

