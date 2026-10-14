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
from .util import CACHE_DIR, is_quick, match_name, now_utc, parse_iso, read_json

log = logging.getLogger("pipeline.soccer_espn")

COMPETITIONS = {
    # code: (ESPN slug, display name, kind)
    "BRA1": ("bra.1", "Brasileirão Série A", "league"),
    "UCL": ("uefa.champions", "Liga prvakov", "cup"),
    "UEL": ("uefa.europa", "Evropska liga", "cup"),
    "UECL": ("uefa.europa.conf", "Konferenčna liga", "cup"),
}

# Approximate UEFA country coefficients (5-season ranking, 2025/26).
UEFA_COEF = {
    "ENG": 100.0, "ITA": 89.0, "ESP": 87.0, "GER": 80.0, "FRA": 68.0, "NED": 60.0, "POR": 57.0,
    "BEL": 52.0, "CZE": 46.0, "TUR": 42.0, "GRE": 37.0, "POL": 36.0, "NOR": 34.5, "DEN": 33.5,
    "AUT": 32.0, "SUI": 31.0, "SCO": 30.5, "ISR": 28.0, "CYP": 27.0, "SWE": 25.5, "CRO": 25.0,
    "SRB": 23.5, "UKR": 22.0, "HUN": 21.5, "SVN": 20.5, "ROU": 20.0, "SVK": 19.5, "BUL": 18.5,
    "AZE": 18.0, "KAZ": 15.0, "IRL": 11.0,
}


def strength_from_coef(coef: float) -> float:
    """League strength on the log-goal scale. Fitted to the hand-set values of the top leagues:
    +0.36 for England (100 points) and -0.14 for Scotland (30.5 points)."""
    return round(0.36 + 0.42 * (math.log(coef) - math.log(100.0)), 3)


# Strength of the football-data.co.uk divisions (first tiers follow the formula closely;
# second tiers are set by hand below their first tier).
LEAGUE_STRENGTH = {
    "E0": 0.36, "SP1": 0.28, "I1": 0.26, "D1": 0.25, "F1": 0.16, "P1": 0.07, "N1": 0.06,
    "B1": -0.02, "T1": -0.04, "E1": -0.02, "G1": -0.12, "SC0": -0.14, "D2": -0.10,
    "I2": -0.18, "SP2": -0.14, "F2": -0.20,
}

# Domestic leagues read from ESPN only to rate their clubs for the UEFA model.
# code: (ESPN slug, country). Slugs that ESPN does not serve are skipped with a note.
ESPN_DOMESTIC = {
    "AUT": "aut.1", "NOR": "nor.1", "DEN": "den.1", "SWE": "swe.1", "SUI": "sui.1", "CZE": "cze.1",
    "POL": "pol.1", "CRO": "cro.1", "SRB": "srb.1", "UKR": "ukr.1", "CYP": "cyp.1", "ISR": "isr.1",
    "SVN": "svn.1", "ROU": "rou.1", "HUN": "hun.1", "BUL": "bul.1", "SVK": "svk.1", "AZE": "aze.1",
    "KAZ": "kaz.1", "IRL": "irl.1",
}
for _c in ESPN_DOMESTIC:
    LEAGUE_STRENGTH[_c] = strength_from_coef(UEFA_COEF[_c])
UNKNOWN_STRENGTH = strength_from_coef(13.0)     # club from a league we do not read (small nations)

# UEFA model settings (chosen by pipeline/backtest.py, see README)
CUP_RIDGE = 12.0          # pull towards the domestic prior; higher = cup results move it less
PRIOR_SCALE = 0.8         # share of a domestic rating that carries over to European games
CUP_HISTORY_DAYS = 800
DOMESTIC_HISTORY_DAYS = 430
DC_RHO = -0.06


def _windows(today: dt.date, days: int = 430) -> list[tuple[dt.date, dt.date]]:
    return [(today - dt.timedelta(days=days), today + dt.timedelta(days=8))]


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


def cup_priors(teams: list[str], goal_ratings: dict, espn_ratings: dict | None = None,
               k: float = PRIOR_SCALE, strengths: dict | None = None,
               unknown: float | None = None) -> tuple[dict, dict]:
    """Start values (att, def) for cup teams and the domestic team each one maps to.

    prior = k * domestic rating + league strength / 2 (attack up, defence down).
    Order: football-data.co.uk divisions (fuzzy names), then ESPN domestic leagues (same
    source as the cup data, so names match exactly), then a small-nation default."""
    strengths = LEAGUE_STRENGTH if strengths is None else strengths
    unknown = UNKNOWN_STRENGTH if unknown is None else unknown
    lookup = domestic_lookup(goal_ratings)
    names = list(lookup)
    espn_ratings = espn_ratings or {}
    espn_lookup = {t: code for code, r in espn_ratings.items() for t in r.teams}
    prior, mapped = {}, {}
    for t in teams:
        hit = match_name(t, names, cutoff=0.86)
        if hit:
            div, dom = lookup[hit]
            r = goal_ratings[div]
        elif t in espn_lookup:
            div, dom = espn_lookup[t], t
            r = espn_ratings[div]
        else:
            prior[t] = (unknown / 2, -unknown / 2)
            continue
        s = strengths.get(div, unknown)
        prior[t] = (k * r.att.get(dom, 0) + s / 2, k * r.dfn.get(dom, 0) - s / 2)
        mapped[t] = (div, dom)
    return prior, mapped


def fit_cup_model(hist: list[dict], teams: list[str], ref: dt.date, goal_ratings: dict,
                  espn_ratings: dict | None, ridge: float = CUP_RIDGE, k: float = PRIOR_SCALE,
                  strengths: dict | None = None, unknown: float | None = None):
    """One Poisson model over the three UEFA competitions, pulled towards domestic priors."""
    prior, mapped = cup_priors(teams, goal_ratings, espn_ratings, k, strengths, unknown)
    r = models.fit_poisson(hist, ref, half_life_days=300, ridge=ridge, prior=prior) if len(hist) >= 10 else None
    return r, prior, mapped


def predict_cup(r, prior: dict, h: str, a: str, neutral: bool = False) -> tuple[float, float]:
    if r and h in r.att and a in r.att:
        return r.expected(h, a, neutral=neutral)
    # a team without cup history: use the priors directly
    ph, pa = prior.get(h, (0, 0)), prior.get(a, (0, 0))
    mu = r.mu if r else math.log(1.35)
    hadv = 0 if neutral else (r.home if r else 0.2)
    return math.exp(mu + hadv + ph[0] + pa[1]), math.exp(mu + pa[0] + ph[1])


def fit_espn_domestic(today: dt.date, days: int = DOMESTIC_HISTORY_DAYS,
                      ref: dt.date | None = None) -> tuple[dict, dict, list[str]]:
    """Poisson ratings of the ESPN domestic leagues. Returns (ratings, history by code, errors)."""
    ratings, hists, errors = {}, {}, []
    for code, slug in ESPN_DOMESTIC.items():
        try:
            if is_quick():
                # quick runs: domestic form changes slowly, read the cache only
                games = read_json(CACHE_DIR / f"espn_{slug}.json", {"events": {}})["events"]
                if not games:
                    continue
            else:
                espn._fetch_range("soccer", slug, today - dt.timedelta(days=6), today)   # probe: is the slug served?
                games = espn.update_games("soccer", slug, today, windows=_windows(today, days))
        except Exception as e:  # noqa: BLE001
            errors.append(f"ESPN {slug}: {e}")
            continue
        hist = [x for x in _hist(games) if ref is None or x["date"] < ref]
        hists[code] = hist
        r = models.fit_poisson(hist, ref or today, half_life_days=200, ridge=5.0)
        if r:
            ratings[code] = r
    return ratings, hists, errors


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
            days = CUP_HISTORY_DAYS if kind == "cup" else 430
            games = espn.update_games("soccer", slug, today, windows=_windows(today, days))
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
    upcoming = _upcoming(cup_games)
    if not upcoming:
        return {"matches": sorted(matches, key=lambda m: m["kickoff"]), "results": results, "errors": errors}
    espn_ratings, espn_hists, e2 = fit_espn_domestic(today)
    errors += e2
    hist = _hist(cup_games)
    teams = sorted({x["home"] for x in hist} | {x["away"] for x in hist} |
                   {g["h"] for g in upcoming} | {g["a"] for g in upcoming})
    r, prior, mapped = fit_cup_model(hist, teams, today, goal_ratings, espn_ratings)
    cur_rows = (fb or {}).get("cur_rows", {})
    for g in upcoming:
        code = cup_code_of[g["id"]]
        h, a = g["h"], g["a"]
        lh, la = predict_cup(r, prior, h, a, neutral=g.get("neutral", False))
        form = {}
        for side, t in (("home", h), ("away", a)):
            if t in mapped and mapped[t][0] in espn_hists:
                form[side] = _form(espn_hists[mapped[t][0]], t)
            elif t in mapped:
                div, dom = mapped[t]
                form[side] = form_string(to_games(cur_rows.get(div, []), "FTHG", "FTAG"), dom)
            else:
                form[side] = _form(hist, t)
        extra = {"domestic": {side: (list(mapped[t]) if t in mapped else None) for side, t in (("home", h), ("away", a))}}
        matches.append(_match_entry(code, COMPETITIONS[code][1], g, lh, la, form, extra))
    unmapped = [t for t in teams if t not in mapped]
    log.info("uefa cups: %d games, %d teams, %d without domestic rating: %s", len(hist), len(teams),
             len(unmapped), ", ".join(unmapped[:30]))
    matches.sort(key=lambda m: m["kickoff"])
    return {"matches": matches, "results": results, "errors": errors}
