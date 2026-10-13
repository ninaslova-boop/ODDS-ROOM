"""NHL, NBA and EuroLeague: team models, match markets and odds."""
from __future__ import annotations

import datetime as dt
import logging
import math

from . import espn, models
from .util import http_get, match_name, now_utc, parse_iso, to_float

log = logging.getLogger("pipeline.sports")

ASSUMED_PRICE = 1.909  # -110, the usual US price on totals / spreads when ESPN gives only the line


def _history(games: dict[str, dict], include_post: bool = True) -> list[dict]:
    out = []
    for g in games.values():
        if not g.get("done") or g.get("hs") is None:
            continue
        allowed = (2, 3) if include_post else (2,)
        if g.get("st") not in allowed:
            continue
        out.append({"home": g["h"], "away": g["a"], "hc": g["hs"], "ac": g["as"],
                    "date": parse_iso(g["d"]).date(), "id": g["id"]})
    return out


def _upcoming(games: dict[str, dict], days: int = 7) -> list[dict]:
    now = now_utc()
    out = []
    for g in games.values():
        if g.get("done") or not g.get("d") or g.get("st") == 1:
            continue
        t = parse_iso(g["d"])
        if now - dt.timedelta(hours=3) <= t <= now + dt.timedelta(days=days):
            out.append(g)
    return sorted(out, key=lambda g: g["d"])


def _form(hist: list[dict], team: str, n: int = 6) -> list[dict]:
    rows = sorted([g for g in hist if team in (g["home"], g["away"])], key=lambda g: g["date"])[-n:]
    out = []
    for g in rows:
        home = g["home"] == team
        f, a = (g["hc"], g["ac"]) if home else (g["ac"], g["hc"])
        out.append({"r": "W" if f > a else ("L" if f < a else "D"), "s": f"{int(f)}-{int(a)}",
                    "o": g["away"] if home else g["home"], "h": home, "d": g["date"].isoformat()})
    return out


def _team_avgs(hist: list[dict], team: str, since: dt.date) -> dict:
    rows = [g for g in hist if team in (g["home"], g["away"]) and g["date"] >= since]
    if not rows:
        return {}
    f = sum(g["hc"] if g["home"] == team else g["ac"] for g in rows) / len(rows)
    a = sum(g["ac"] if g["home"] == team else g["hc"] for g in rows) / len(rows)
    w = sum((g["hc"] > g["ac"]) == (g["home"] == team) for g in rows)
    return {"gp": len(rows), "for": round(f, 2), "against": round(a, 2), "wins": w}


def _attach_us_odds(markets: list[dict], odds: dict):
    for m in markets:
        if m["scope"] == "match" and m["kind"] == "1" and odds.get("ml_home"):
            m["odds"] = odds["ml_home"]
        elif m["scope"] == "match" and m["kind"] == "2" and odds.get("ml_away"):
            m["odds"] = odds["ml_away"]
        elif m["scope"] == "total" and odds.get("total") and m["line"] == odds["total"]:
            price = odds.get(m["kind"])
            m["odds"] = price or ASSUMED_PRICE
            if not price:
                m["odds_assumed"] = True
        elif m["scope"] == "spread" and odds.get("spread_home") is not None:
            sh = odds["spread_home"]
            if m["kind"] == "home" and m["line"] == sh:
                m["odds"] = odds.get("spread_home_odds") or ASSUMED_PRICE
                m["odds_assumed"] = "spread_home_odds" not in odds
            elif m["kind"] == "away" and m["line"] == -sh:
                m["odds"] = odds.get("spread_away_odds") or ASSUMED_PRICE
                m["odds_assumed"] = "spread_away_odds" not in odds


def _season_start(today: dt.date) -> dt.date:
    y = today.year if today.month >= 9 else today.year - 1
    return dt.date(y, 9, 1)


# --------------------------------------------------------------------------
def build_nhl(today: dt.date) -> dict:
    games = espn.update_games("hockey", "nhl", today)
    hist = _history(games)
    for g in hist:
        g["w"] = 1.0 if g["date"] >= _season_start(today) else 0.6
    r = models.fit_poisson(hist, today, half_life_days=120, ridge=6.0)
    matches, results = [], {}
    for g in games.values():
        if g.get("done") and g.get("hs") is not None:
            results[f"nhl:{g['id']}"] = {"goals": [g["hs"], g["as"]]}
    if not r:
        return {"matches": [], "results": results, "errors": ["NHL: premalo tekem za model"], "ratings": None}
    start = _season_start(today)
    for g in _upcoming(games):
        h, a = g["h"], g["a"]
        if h not in r.att or a not in r.att:
            continue
        lh, la = r.expected(h, a, neutral=g.get("neutral", False))
        M = models.score_matrix(lh, la, 12, 0.0)
        odds = g.get("odds", {})
        lines = sorted({4.5, 5.5, 6.5, 7.5} | ({odds["total"]} if odds.get("total") else set()))
        mk = models.goal_markets(M, unit="golov", total_lines=lines, hockey=True)
        mk += [models.mk("goals", s, "over", L, models.poisson_sf(lam, L), f"{nm} več kot {L}", "Goli ekip")
               for s, lam, nm in (("home", lh, "Domači"), ("away", la, "Gostje")) for L in (1.5, 2.5, 3.5)]
        _attach_us_odds(mk, odds)
        dist = models.total_distribution(M, 10)
        matches.append({
            "id": f"nhl:{g['id']}", "sport": "nhl", "league": "NHL", "league_name": "NHL",
            "kickoff": g["d"], "home": h, "away": a, "exp": [round(lh, 2), round(la, 2)],
            "top_scores": models.top_scores(M), "total_dist": dist,
            "total_mode": max(dist, key=lambda x: x["p"])["n"], "markets": mk, "odds_raw": odds,
            "form": {"home": _form(hist, h), "away": _form(hist, a)},
            "stats": {"home": _team_avgs(hist, h, start), "away": _team_avgs(hist, a, start)},
            "abbr": [g.get("ha"), g.get("aa")],
        })
    return {"matches": matches, "results": results, "errors": [],
            "ratings": {"home_adv": round(math.exp(r.home), 3), "avg_goals": round(r.league_avg * 2, 2),
                        "table": r.table()},
            "model": r, "hist": hist}


def build_basketball(today: dt.date, league: str = "nba") -> dict:
    games = espn.update_games("basketball", league, today)
    hist = _history(games)
    start = _season_start(today)
    for g in hist:
        g["w"] = 1.0 if g["date"] >= start else 0.5
    r = models.fit_gauss(hist, today, half_life_days=75, ridge=4.0)
    results = {f"{league}:{g['id']}": {"points": [g["hs"], g["as"]]} for g in games.values()
               if g.get("done") and g.get("hs") is not None}
    if not r:
        return {"matches": [], "results": results, "errors": [f"{league.upper()}: premalo tekem za model"],
                "ratings": None}
    matches = []
    for g in _upcoming(games):
        h, a = g["h"], g["a"]
        if h not in r.off or a not in r.off:
            continue
        ph, pa = r.expected(h, a)
        odds = g.get("odds", {})
        mk = models.basketball_markets(ph, pa, r.sd_total, r.sd_margin, odds.get("total"), odds.get("spread_home"))
        _attach_us_odds(mk, odds)
        matches.append({
            "id": f"{league}:{g['id']}", "sport": league, "league": league.upper(), "league_name": league.upper(),
            "kickoff": g["d"], "home": h, "away": a, "exp": [round(ph, 1), round(pa, 1)],
            "sd": [round(r.sd_total, 1), round(r.sd_margin, 1)], "markets": mk, "odds_raw": odds,
            "form": {"home": _form(hist, h), "away": _form(hist, a)},
            "stats": {"home": _team_avgs(hist, h, start), "away": _team_avgs(hist, a, start)},
            "abbr": [g.get("ha"), g.get("aa")],
        })
    return {"matches": matches, "results": results, "errors": [],
            "ratings": {"home_adv": round(r.home, 2), "avg_points": round(r.mu * 2, 1), "table": r.table()},
            "model": r, "hist": hist}


# --------------------------------------------------------------------------
# EuroLeague (official public API, no key)
# --------------------------------------------------------------------------
EL_BASES = ["https://api-live.euroleague.net/v2", "https://feeds.incrowdsports.com/provider/euroleague-feeds/v2"]


def _el_games(season_code: str) -> list[dict]:
    last = None
    for base in EL_BASES:
        try:
            j = http_get(f"{base}/competitions/E/seasons/{season_code}/games")
            data = j.get("data", j) if isinstance(j, dict) else j
            if isinstance(data, list) and data:
                return data
        except Exception as e:  # noqa: BLE001
            last = e
    if last:
        raise last
    return []


def _el_parse(g: dict) -> dict | None:
    try:
        loc, road = g["local"], g["road"]
        h = (loc.get("club") or {}).get("name") or (loc.get("club") or {}).get("editorialName")
        a = (road.get("club") or {}).get("name") or (road.get("club") or {}).get("editorialName")
        d = g.get("utcDate") or g.get("date")
        if not (h and a and d):
            return None
        played = bool(g.get("played"))
        return {"id": str(g.get("identifier") or g.get("gameCode") or g.get("id")), "d": d,
                "h": h, "a": a, "hs": to_float(loc.get("score")) if played else None,
                "as": to_float(road.get("score")) if played else None, "done": played,
                "st": 2, "phase": (g.get("phaseType") or {}).get("code")}
    except (KeyError, TypeError, AttributeError):
        return None


def build_euroleague(today: dt.date) -> dict:
    y = today.year if today.month >= 9 else today.year - 1
    games: dict[str, dict] = {}
    errors = []
    for yr in (y - 1, y):
        try:
            for raw in _el_games(f"E{yr}"):
                g = _el_parse(raw)
                if g:
                    if not g["d"].endswith("Z") and "+" not in g["d"][10:]:
                        g["d"] = g["d"][:19] + "Z"
                    games[f"{yr}-{g['id']}"] = {**g, "id": f"{yr}-{g['id']}"}
        except Exception as e:  # noqa: BLE001
            errors.append(f"EuroLeague E{yr}: {e}")
    if not games:
        return {"matches": [], "results": {}, "errors": errors or ["EuroLeague: ni podatkov"], "ratings": None}
    hist = _history(games)
    start = _season_start(today)
    for g in hist:
        g["w"] = 1.0 if g["date"] >= start else 0.5
    r = models.fit_gauss(hist, today, half_life_days=90, ridge=4.0)
    results = {f"euroleague:{g['id']}": {"points": [g["hs"], g["as"]]} for g in games.values() if g["done"]}
    if not r:
        return {"matches": [], "results": results, "errors": errors + ["EuroLeague: premalo tekem"], "ratings": None}
    matches = []
    for g in _upcoming(games):
        h, a = g["h"], g["a"]
        hh = h if h in r.off else match_name(h, list(r.off))
        aa = a if a in r.off else match_name(a, list(r.off))
        if not hh or not aa:
            continue
        ph, pa = r.expected(hh, aa)
        mk = models.basketball_markets(ph, pa, r.sd_total, r.sd_margin)
        matches.append({
            "id": f"euroleague:{g['id']}", "sport": "euroleague", "league": "EL", "league_name": "EuroLeague",
            "kickoff": g["d"], "home": h, "away": a, "exp": [round(ph, 1), round(pa, 1)],
            "sd": [round(r.sd_total, 1), round(r.sd_margin, 1)], "markets": mk, "odds_raw": {},
            "form": {"home": _form(hist, hh), "away": _form(hist, aa)},
            "stats": {"home": _team_avgs(hist, hh, start), "away": _team_avgs(hist, aa, start)},
        })
    return {"matches": matches, "results": results, "errors": errors,
            "ratings": {"home_adv": round(r.home, 2), "avg_points": round(r.mu * 2, 1), "table": r.table()}}
