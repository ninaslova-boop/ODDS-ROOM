"""EuroLeague player and team stats from the official public EuroLeague API (no key).

Source: https://api-live.euroleague.net/v2 (the API behind euroleaguebasketball.net), competition "E",
season code E<year> of the CURRENT season only (E2026 = 2026-27).
* Season game list:  /competitions/E/seasons/E2026/games
* Box score per game: /competitions/E/seasons/E2026/games/{gameCode}/stats
Every response is cached for the day (data/cache/euroleague_api/<date>/). A finished game's box score
is also kept in data/cache/euroleague_box_v2.json, so it is downloaded once.

Players: season averages and last-5 averages of points, rebounds, assists, 3PM and PRA. Prop
probabilities use mu = (season + last 5) / 2 and the spread of the player's season games.
Teams: season and last-5 averages of the team totals (points, rebounds, assists, 3PM, points allowed).

No silent fallback: when the API does not return the current season (error, empty list or no game
played yet), there is no EuroLeague player data. build.py writes "none" with the reason, and the
export prints "player data: none". Last season is never used instead.

Likely absences (the EuroLeague publishes no injury report): a regular (MIN_REGULAR minutes) who
missed the last 2 games of his team is "doubt" (chance CHANCE_2), after 3 or more games "out".
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import math
import shutil
from concurrent.futures import ThreadPoolExecutor

from . import models
from .players import _lines, p_at_least, prop
from .util import CACHE_DIR, http_get, is_quick, now_utc, read_json, to_float, write_json

log = logging.getLogger("pipeline.euroleague_players")

API = "https://api-live.euroleague.net/v2"
COMP = "E"
DAY_CACHE = CACHE_DIR / "euroleague_api"
BOX = CACHE_DIR / "euroleague_box_v2.json"
KEEP_DAYS = 3
MIN_REGULAR = 15.0
CHANCE_2 = 30
MIN_GAMES_PROPS = 3
MIN_MINUTES_PROPS = 12.0
WORKERS = 6
STATS = ("pts", "reb", "ast", "tpm", "pra")


def season_year(today: dt.date) -> int:
    """2026 for the 2026-27 season (the season starts in autumn)."""
    return today.year if today.month >= 8 else today.year - 1


def season_code(today: dt.date) -> str:
    return f"E{season_year(today)}"


# --------------------------------------------------------------------------
# HTTP with a cache per day
# --------------------------------------------------------------------------
def api_get(path: str, today: dt.date | None = None):
    today = today or now_utc().date()
    day_dir = DAY_CACHE / today.isoformat()
    f = day_dir / (hashlib.sha1(path.encode()).hexdigest()[:20] + ".json")
    hit = read_json(f)
    if hit is not None:
        return hit["body"]
    body = http_get(f"{API}{path}")
    write_json(f, {"path": path, "body": body})
    _prune(today)
    return body


def _prune(today: dt.date):
    if not DAY_CACHE.exists():
        return
    cutoff = (today - dt.timedelta(days=KEEP_DAYS)).isoformat()
    for d in DAY_CACHE.iterdir():
        if d.is_dir() and d.name < cutoff:
            shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------
def person_name(raw: str | None) -> str | None:
    """'ABALDE, ALBERTO' -> 'Alberto Abalde'."""
    if not raw:
        return None
    if "," in raw:
        last, first = [x.strip() for x in raw.split(",", 1)]
        raw = f"{first} {last}"

    def cap(w: str) -> str:
        return "-".join(x.capitalize() for x in w.split("-")) if w.isupper() or w.islower() else w
    return " ".join(cap(w) for w in raw.split())


def parse_stats(j: dict) -> list[list]:
    """Box lines [id, name, team, pos, min, pts, reb, ast, 3pm] of the players who played."""
    out = []
    for side in ("local", "road"):
        for p in (j.get(side) or {}).get("players") or []:
            pl, st = p.get("player") or {}, p.get("stats") or {}
            person, club = pl.get("person") or {}, pl.get("club") or {}
            secs = to_float(st.get("timePlayed")) or 0
            if secs <= 0:
                continue
            name = person_name(person.get("name"))
            if not name or not club.get("name"):
                continue
            out.append([person.get("code") or name, name, club["name"], pl.get("positionName"), round(secs / 60, 1),
                        to_float(st.get("points")) or 0, to_float(st.get("totalRebounds")) or 0,
                        to_float(st.get("assistances")) or 0, to_float(st.get("fieldGoalsMade3")) or 0])
    return out


def parse_totals(j: dict) -> list[list[float]] | None:
    """Team totals [[pts, reb, ast, 3pm] local, [...] road]."""
    out = []
    for side in ("local", "road"):
        t = (j.get(side) or {}).get("total") or {}
        vals = [to_float(t.get(k)) for k in ("points", "totalRebounds", "assistances", "fieldGoalsMade3")]
        if vals[0] is None:
            return None
        out.append([v or 0 for v in vals])
    return out


def parse_games(j) -> list[dict]:
    """Played games of the season list: {"code", "d", "h", "a"}."""
    data = j.get("data", j) if isinstance(j, dict) else j
    out = []
    for g in data or []:
        try:
            if not g.get("played"):
                continue
            h, a = (g["local"].get("club") or {}).get("name"), (g["road"].get("club") or {}).get("name")
            d = (g.get("utcDate") or g.get("date") or "")[:10]
            if h and a and d and g.get("gameCode") is not None:
                out.append({"code": g["gameCode"], "d": d, "h": h, "a": a})
        except (KeyError, TypeError, AttributeError):
            continue
    return out


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------
def load(today: dt.date, max_new: int = 400) -> dict:
    """{"ok", "season", "reason", "games": {code: {"d", "p", "t", "tt"}}} for the current season only."""
    season = season_code(today)
    try:
        played = parse_games(api_get(f"/competitions/{COMP}/seasons/{season}/games", today))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "season": season, "reason": f"API ne vrne sezone {season}: {e}"[:160], "games": {}}
    if not played:
        return {"ok": False, "season": season, "reason": f"API za sezono {season} nima odigranih tekem", "games": {}}
    cache = read_json(BOX, {}) or {}
    if cache.get("season") != season:
        cache = {"season": season, "games": {}}
    games = cache["games"]
    if is_quick():
        max_new = 10
    todo = [g for g in sorted(played, key=lambda g: g["d"], reverse=True) if str(g["code"]) not in games][:max_new]

    def fetch(g):
        try:
            j = api_get(f"/competitions/{COMP}/seasons/{season}/games/{g['code']}/stats", today)
            return g, parse_stats(j), parse_totals(j)
        except Exception as e:  # noqa: BLE001
            log.info("euroleague box %s %s: %s", season, g["code"], e)
            return g, None, None

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for g, lines, tt in ex.map(fetch, todo):
            if lines:
                games[str(g["code"])] = {"d": g["d"], "p": lines, "t": [g["h"], g["a"]], "tt": tt}
    write_json(BOX, cache)
    log.info("euroleague %s: %d played, %d box scores (%d new)", season, len(played), len(games), len(todo))
    if not games:
        return {"ok": False, "season": season, "reason": f"API ne vrne box scorov sezone {season}", "games": {}}
    return {"ok": True, "season": season, "reason": "", "games": games, "played": len(played)}


# --------------------------------------------------------------------------
# averages
# --------------------------------------------------------------------------
def _avg(rows: list[list[float]]) -> list[float]:
    n = len(rows)
    return [sum(r[i] for r in rows) / n for i in range(len(rows[0]))] if n else []


def player_lines(box: dict) -> dict[str, list[tuple[str, list]]]:
    per: dict[str, list[tuple[str, list]]] = {}
    for g in box["games"].values():
        for ln in g["p"]:
            per.setdefault(str(ln[0]), []).append((g["d"], ln))
    for v in per.values():
        v.sort(key=lambda x: x[0])
    return per


def averages(lines: list[list]) -> dict:
    """{"min", "pts", "reb", "ast", "tpm", "pra"} from box lines."""
    a = _avg([[ln[4], ln[5], ln[6], ln[7], ln[8]] for ln in lines])
    return {"min": round(a[0], 1), "pts": round(a[1], 1), "reb": round(a[2], 1), "ast": round(a[3], 1),
            "tpm": round(a[4], 1), "pra": round(a[1] + a[2] + a[3], 1)}


def _sd(vals: list[float]) -> float:
    n = len(vals)
    if n < 2:
        return 0.0
    mu = sum(vals) / n
    return math.sqrt(sum((v - mu) ** 2 for v in vals) / (n - 1))


SD_FLOOR = {"pts": 4.0, "reb": 2.0, "ast": 1.6, "pra": 5.0}
UNIT = {"pts": "točk", "reb": "skokov", "ast": "podaj", "pra": "T+S+P"}


def props_for(season: dict, l5: dict, lines: list[list], opp_factor: float = 1.0) -> list[dict]:
    """Prop probabilities: mu = (season + last 5) / 2; points (and PRA) scaled by the opponent's defence."""
    vals = {"pts": [ln[5] for ln in lines], "reb": [ln[6] for ln in lines], "ast": [ln[7] for ln in lines]}
    vals["pra"] = [p + r + a for p, r, a in zip(vals["pts"], vals["reb"], vals["ast"])]
    out = []
    for stat in ("pts", "reb", "ast", "pra"):
        mu = (season[stat] + l5[stat]) / 2
        if stat == "pts":
            mu *= opp_factor
        elif stat == "pra":
            mu += (opp_factor - 1) * (season["pts"] + l5["pts"]) / 2
        s = max(_sd(vals[stat]), SD_FLOOR[stat])
        for L in _lines(mu):
            if L == 0.5 and stat in ("reb", "ast"):
                continue            # bookmakers rarely offer 0.5 rebounds / assists
            out.append(prop(f"Več kot {L:g} {UNIT[stat]}", 1 - models.norm_cdf((L - mu) / s), stat, L))
    tpm = (season["tpm"] + l5["tpm"]) / 2
    if tpm >= 0.8:
        for k in (1, 2, 3, 4):
            if k <= tpm + 2:
                out.append(prop(f"{k}+ trojk", p_at_least(tpm, k), "tpm", k - 0.5))
    return out


def players(el: dict, box: dict) -> list[dict]:
    """Player rows (current season only) with season and last-5 averages and props for the next match."""
    model = el.get("model")
    match_by_team = {}
    for m in el.get("matches", []):
        match_by_team.setdefault(m["home"], m)
        match_by_team.setdefault(m["away"], m)
    out = []
    for pid, rows in player_lines(box).items():
        lines = [ln for _, ln in rows]
        last = lines[-1]
        season, l5 = averages(lines), averages(lines[-5:])
        row = {"name": last[1], "team": last[2], "pos": last[3], "gp": len(lines), **season,
               "l5": {k: l5[k] for k in ("min", *STATS)}, "last_date": rows[-1][0], "src": "EuroLeague API"}
        m = match_by_team.get(row["team"])
        if m and len(lines) >= MIN_GAMES_PROPS and season["min"] >= MIN_MINUTES_PROPS:
            home = m["home"] == row["team"]
            opp = m["away"] if home else m["home"]
            f = (model.mu + model.dfn.get(opp, 0)) / model.mu if model else 1.0
            row["next"] = {"match": m["id"], "opp": opp, "home": home, "kickoff": m["kickoff"],
                           "proj_pts": round((season["pts"] + l5["pts"]) / 2 * f, 1)}
            row["props"] = props_for(season, l5, lines, f)
        out.append(row)
    out.sort(key=lambda r: -r["pts"])
    return out


def team_stats(box: dict) -> dict[str, dict]:
    """{team: {"gp", "season": {...}, "l5": {...}}} from the official team totals."""
    per: dict[str, list[tuple[str, list[float]]]] = {}
    for g in box["games"].values():
        tt = g.get("tt")
        if not tt:
            continue
        (h, a), (th, ta) = g["t"], tt
        per.setdefault(h, []).append((g["d"], th + [ta[0]]))
        per.setdefault(a, []).append((g["d"], ta + [th[0]]))
    out = {}
    keys = ("pts", "reb", "ast", "tpm", "opp_pts")
    for team, rows in per.items():
        rows.sort(key=lambda x: x[0])
        vals = [v for _, v in rows]
        out[team] = {"gp": len(vals), "season": dict(zip(keys, (round(x, 1) for x in _avg(vals)))),
                     "l5": dict(zip(keys, (round(x, 1) for x in _avg(vals[-5:]))))}
    return out


def attach_team_stats(matches: list[dict], ts: dict[str, dict]):
    for m in matches:
        if m["sport"] != "euroleague":
            continue
        for side in ("home", "away"):
            if m[side] in ts:
                m.setdefault("stats", {}).setdefault(side, {})["box"] = ts[m[side]]


def absences(rows: list[dict], box: dict) -> dict[str, list[dict]]:
    """{team: [{name, status, chance, reason, src}]} for regulars who missed the last games of their team."""
    team_dates: dict[str, list[str]] = {}
    for g in box["games"].values():
        for t in g.get("t") or []:
            team_dates.setdefault(t, []).append(g["d"])
    out: dict[str, list[dict]] = {}
    for r in rows:
        if r["min"] < MIN_REGULAR:
            continue
        dates = sorted(set(team_dates.get(r["team"], [])))
        missed = sum(1 for d in dates if d > r["last_date"])
        if missed < 2:
            continue
        status, chance = ("doubt", CHANCE_2) if missed == 2 else ("out", 0)
        out.setdefault(r["team"], []).append({
            "name": r["name"], "status": status, "chance": chance,
            "reason": f"ni igral zadnjih {missed} tekem", "src": "EuroLeague"})
    return out
