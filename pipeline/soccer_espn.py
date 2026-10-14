"""Football competitions that football-data.co.uk does not cover, from ESPN (no key):
Brasileirão and the three UEFA club competitions.

* Leagues: the usual Poisson team model on ESPN results.
* UEFA cups: one Poisson model over all three cups. Each team starts from its domestic
  rating (football-data.co.uk model) shifted by the strength of its league, so a team
  with few cup games is still rated sensibly.
"""
from __future__ import annotations

import datetime as dt
import logging
import math

from . import espn, models
from .football import form_string, to_games
from .util import match_name, now_utc, parse_iso

log = logging.getLogger("pipeline.soccer_espn")

COMPETITIONS = {
    # code: (ESPN slug, display name, kind)
    "BRA1": ("bra.1", "Brasileirão Série A", "league"),
    "UCL": ("uefa.champions", "Liga prvakov", "cup"),
    "UEL": ("uefa.europa", "Evropska liga", "cup"),
    "UECL": ("uefa.europa.conf", "Konferenčna liga", "cup"),
}

# Rough league strength on the log-goal scale (from UEFA club coefficients).
LEAGUE_STRENGTH = {
    "E0": 0.36, "SP1": 0.28, "I1": 0.26, "D1": 0.25, "F1": 0.16, "P1": 0.07, "N1": 0.06,
    "B1": -0.02, "T1": -0.04, "E1": -0.02, "G1": -0.12, "SC0": -0.14, "D2": -0.10,
    "I2": -0.18, "SP2": -0.14, "F2": -0.20,
}
UNKNOWN_STRENGTH = -0.30
DC_RHO = -0.06


def _windows(today: dt.date) -> list[tuple[dt.date, dt.date]]:
    return [(today - dt.timedelta(days=430), today + dt.timedelta(days=8))]


def _hist(games: dict) -> list[dict]:
    out = []
    for g in games.values():
        if g.get("done") and g.get("hs") is not None and g.get("as") is not None:
            out.append({"home": g["h"], "away": g["a"], "hc": int(g["hs"]), "ac": int(g["as"]),
                        "date": parse_iso(g["d"]).date(), "id": g["id"], "neutral": g.get("neutral", False)})
    return out


def _upcoming(games: dict, days: int = 7) -> list[dict]:
    now = now_utc()
    out = [g for g in games.values() if not g.get("done") and g.get("d")
           and now - dt.timedelta(hours=3) <= parse_iso(g["d"]) <= now + dt.timedelta(days=days)]
    return sorted(out, key=lambda g: g["d"])


def _form(hist: list[dict], team: str, n: int = 6) -> list[dict]:
    rows = sorted([g for g in hist if team in (g["home"], g["away"])], key=lambda g: g["date"])[-n:]
    out = []
    for g in rows:
        home = g["home"] == team
        f, a = (g["hc"], g["ac"]) if home else (g["ac"], g["hc"])
        out.append({"r": "W" if f > a else ("L" if f < a else "D"), "s": f"{f}-{a}",
                    "o": g["away"] if home else g["home"], "h": home, "d": g["date"].isoformat()})
    return out


def attach_odds(markets: list[dict], odds: dict):
    for m in markets:
        if m["stat"] != "goals":
            continue
        if m["scope"] == "match":
            o = {"1": odds.get("ml_home"), "X": odds.get("ml_draw"), "2": odds.get("ml_away")}.get(m["kind"])
            if o:
                m["odds"] = o
        elif m["scope"] == "total" and odds.get("total") == m["line"] and odds.get(m["kind"]):
            m["odds"] = odds[m["kind"]]


def domestic_lookup(goal_ratings: dict) -> dict[str, tuple[str, str]]:
    """{team name: (division, team)} over all football-data.co.uk divisions."""
    out = {}
    for div, r in goal_ratings.items():
        for t in r.teams:
            out.setdefault(t, (div, t))
    return out


def cup_priors(teams: list[str], goal_ratings: dict) -> tuple[dict, dict]:
    """Start values (att, def) for cup teams and the domestic team each one maps to."""
    lookup = domestic_lookup(goal_ratings)
    names = list(lookup)
    prior, mapped = {}, {}
    for t in teams:
        hit = match_name(t, names, cutoff=0.86)
        if hit:
            div, dom = lookup[hit]
            r = goal_ratings[div]
            s = LEAGUE_STRENGTH.get(div, UNKNOWN_STRENGTH)
            prior[t] = (r.att.get(dom, 0) + s / 2, r.dfn.get(dom, 0) - s / 2)
            mapped[t] = (div, dom)
        else:
            prior[t] = (UNKNOWN_STRENGTH / 2, -UNKNOWN_STRENGTH / 2)
    return prior, mapped


def _match_entry(code: str, name: str, g: dict, lh: float, la: float, form: dict, extra: dict | None = None) -> dict:
    M = models.score_matrix(lh, la, 10, DC_RHO)
    mk = models.goal_markets(M)
    attach_odds(mk, g.get("odds", {}))
    dist = models.total_distribution(M)
    return {
        "id": f"fbe:{code}:{g['id']}", "sport": "football", "league": code, "league_name": name,
        "kickoff": g["d"], "home": g["h"], "away": g["a"], "exp": [round(lh, 2), round(la, 2)],
        "top_scores": models.top_scores(M), "total_dist": dist,
        "total_mode": max(dist, key=lambda x: x["p"])["n"], "markets": mk, "odds_raw": g.get("odds", {}),
        "form": form, "stats": {"home": {}, "away": {}}, "espn": {"slug": COMPETITIONS[code][0], "id": g["id"]},
        **(extra or {}),
    }


def build(today: dt.date, fb: dict | None) -> dict:
    goal_ratings = (fb or {}).get("goal_ratings", {})
    errors, matches, results = [], [], {}
    cup_games: dict[str, dict] = {}
    cup_code_of: dict[str, str] = {}
    for code, (slug, name, kind) in COMPETITIONS.items():
        try:
            games = espn.update_games("soccer", slug, today, windows=_windows(today))
        except Exception as e:  # noqa: BLE001
            errors.append(f"ESPN {slug}: {e}")
            continue
        for g in games.values():
            if g.get("done") and g.get("hs") is not None:
                results[f"fbe:{code}:{g['id']}"] = {"goals": [g["hs"], g["as"]]}
        if kind == "cup":
            for k, g in games.items():
                cup_games[k] = g
                cup_code_of[k] = code
            continue
        hist = _hist(games)
        r = models.fit_poisson(hist, today, half_life_days=200, ridge=5.0)
        if not r:
            errors.append(f"{name}: premalo tekem za model")
            continue
        for g in _upcoming(games):
            if g["h"] not in r.att or g["a"] not in r.att:
                continue
            lh, la = r.expected(g["h"], g["a"], neutral=g.get("neutral", False))
            matches.append(_match_entry(code, name, g, lh, la,
                                        {"home": _form(hist, g["h"]), "away": _form(hist, g["a"])}))

    # one model for all UEFA competitions
    hist = _hist(cup_games)
    teams = sorted({x["home"] for x in hist} | {x["away"] for x in hist} |
                   {g["h"] for g in _upcoming(cup_games)} | {g["a"] for g in _upcoming(cup_games)})
    prior, mapped = cup_priors(teams, goal_ratings)
    for x in hist:
        x["w"] = 1.0
    r = models.fit_poisson(hist, today, half_life_days=300, ridge=3.0, prior=prior) if hist else None
    cur_rows = (fb or {}).get("cur_rows", {})
    for g in _upcoming(cup_games):
        code = cup_code_of[g["id"]]
        h, a = g["h"], g["a"]
        if r and h in r.att and a in r.att:
            lh, la = r.expected(h, a, neutral=g.get("neutral", False))
        else:
            # no cup history: use the priors directly
            ph, pa = prior.get(h, (0, 0)), prior.get(a, (0, 0))
            mu = r.mu if r else math.log(1.35)
            hadv = 0 if g.get("neutral") else (r.home if r else 0.2)
            lh, la = math.exp(mu + hadv + ph[0] + pa[1]), math.exp(mu + pa[0] + ph[1])
        form = {}
        for side, t in (("home", h), ("away", a)):
            if t in mapped:
                div, dom = mapped[t]
                form[side] = form_string(to_games(cur_rows.get(div, []), "FTHG", "FTAG"), dom)
            else:
                form[side] = _form(hist, t)
        extra = {"domestic": {side: (list(mapped[t]) if t in mapped else None) for side, t in (("home", h), ("away", a))}}
        matches.append(_match_entry(code, COMPETITIONS[code][1], g, lh, la, form, extra))
    unmapped = [t for t in teams if t not in mapped]
    log.info("uefa cups: %d games, %d teams, %d without domestic rating", len(hist), len(teams), len(unmapped))
    matches.sort(key=lambda m: m["kickoff"])
    return {"matches": matches, "results": results, "errors": errors}
