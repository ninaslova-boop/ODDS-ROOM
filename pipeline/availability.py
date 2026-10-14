"""Lineups and injuries.

Sources (all free):
* Fantasy Premier League API: injury / suspension status and "chance of playing" (Premier League).
* ESPN soccer summary: confirmed lineups, about 1 hour before kick-off (every league ESPN covers).
* ESPN injuries: NBA and NHL injury reports.
* API-Football (optional, secret API_FOOTBALL_KEY, free plan 100 calls/day): injuries for all leagues.

Output per match: {"lineup": {"home": [...], "away": [...], "bench": {...}} | None,
                   "absent": {"home": [{name, status, reason}], "away": [...]}}
"""
from __future__ import annotations

import datetime as dt
import logging
import os

from . import espn
from .util import cached_get, http_get, match_name, norm_name, now_utc, parse_iso

log = logging.getLogger("pipeline.availability")

ESPN_SLUG = {
    "E0": "eng.1", "E1": "eng.2", "SP1": "esp.1", "SP2": "esp.2", "I1": "ita.1", "I2": "ita.2",
    "D1": "ger.1", "D2": "ger.2", "F1": "fra.1", "F2": "fra.2", "N1": "ned.1", "P1": "por.1",
    "B1": "bel.1", "T1": "tur.1", "SC0": "sco.1", "G1": "gre.1",
}
LINEUP_BEFORE_MIN = 100   # ESPN publishes lineups about 60 minutes before kick-off


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def same_player(a: str, b: str) -> bool:
    """Loose player-name match: full name, or same surname and same first initial."""
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    ta, tb = na.split(), nb.split()
    if ta[-1] != tb[-1]:
        # FPL web names are often only the surname ("Saka")
        return (len(ta) == 1 and ta[0] in tb) or (len(tb) == 1 and tb[0] in ta)
    return len(ta) == 1 or len(tb) == 1 or ta[0][0] == tb[0][0]


def find_player(name: str, names: list[str]) -> str | None:
    hits = [n for n in names if same_player(name, n)]
    return hits[0] if len(hits) == 1 else None


# --------------------------------------------------------------------------
# Premier League: Fantasy Premier League
# --------------------------------------------------------------------------
FPL_STATUS = {"d": "dvomljiv", "i": "poškodovan", "s": "kazen", "u": "ni na voljo", "n": "ni na voljo"}


def fpl_absences() -> dict[str, list[dict]]:
    j = cached_get("https://fantasy.premierleague.com/api/bootstrap-static/", always_fresh=True)
    teams = {t["id"]: t["name"] for t in j.get("teams", [])}
    out: dict[str, list[dict]] = {}
    for p in j.get("elements", []):
        st = p.get("status", "a")
        if st == "a":
            continue
        chance = p.get("chance_of_playing_next_round")
        if st == "d" and chance is not None and chance >= 75:
            continue
        name = f"{p.get('first_name', '')} {p.get('second_name', '')}".strip()
        out.setdefault(teams.get(p.get("team"), "?"), []).append({
            "name": name, "short": p.get("web_name"), "status": "out" if st != "d" else "doubt",
            "chance": 0 if st in ("i", "s", "u", "n") and chance is None else chance,
            "reason": (p.get("news") or FPL_STATUS.get(st, ""))[:80], "src": "FPL"})
    return out


# --------------------------------------------------------------------------
# NBA / NHL: ESPN injuries
# --------------------------------------------------------------------------
def espn_injuries(sport: str, league: str) -> dict[str, list[dict]]:
    j = cached_get(f"{espn.BASE}/{sport}/{league}/injuries", always_fresh=True)
    out: dict[str, list[dict]] = {}
    for team in j.get("injuries", []) or []:
        tname = team.get("displayName") or (team.get("team") or {}).get("displayName")
        for inj in team.get("injuries", []) or []:
            ath = inj.get("athlete") or {}
            status = (inj.get("status") or (inj.get("type") or {}).get("description") or "").strip()
            low = status.lower()
            kind = "out" if low in ("out", "injured reserve", "ir", "suspension", "suspended") or "out" in low \
                else ("doubt" if low in ("day-to-day", "questionable", "doubtful", "game time decision") else None)
            if not kind:
                continue
            out.setdefault(tname, []).append({
                "name": ath.get("displayName"), "status": kind, "chance": 0 if kind == "out" else 50,
                "reason": (inj.get("shortComment") or status)[:80], "src": "ESPN"})
    return out


# --------------------------------------------------------------------------
# API-Football (optional)
# --------------------------------------------------------------------------
def api_football_injuries(dates: list[dt.date]) -> list[dict]:
    key = os.environ.get("API_FOOTBALL_KEY", "").strip()
    if not key:
        return []
    out = []
    for d in dates:
        j = cached_get("https://v3.football.api-sports.io/injuries", params={"date": d.isoformat()},
                       headers={"x-apisports-key": key})
        errs = j.get("errors")
        if errs and (len(errs) if isinstance(errs, (list, dict)) else 1):
            raise RuntimeError(f"API-Football: {errs}")
        for r in j.get("response", []):
            pl, tm = r.get("player") or {}, r.get("team") or {}
            typ = (pl.get("type") or "").lower()
            out.append({"team": tm.get("name"), "date": d.isoformat(), "name": pl.get("name"),
                        "status": "out" if "missing" in typ else "doubt",
                        "chance": 0 if "missing" in typ else 50, "reason": (pl.get("reason") or "")[:80],
                        "src": "API-Football"})
    return out


# --------------------------------------------------------------------------
# ESPN soccer lineups
# --------------------------------------------------------------------------
def parse_rosters(summ: dict) -> dict | None:
    """Starting XI and bench from an ESPN soccer summary. None when not announced yet."""
    out = {}
    for r in summ.get("rosters", []) or []:
        side = r.get("homeAway")
        if side not in ("home", "away"):
            continue
        starters, bench = [], []
        for p in r.get("roster", []) or []:
            a = p.get("athlete") or {}
            nm = a.get("displayName") or a.get("fullName")
            if not nm:
                continue
            (starters if p.get("starter") else bench).append(
                {"name": nm, "pos": (p.get("position") or {}).get("abbreviation")})
        if len(starters) >= 11:
            out[side] = {"xi": starters, "bench": bench}
    return out if len(out) == 2 else None


def _espn_event_for(match: dict, boards: dict) -> tuple[str, str] | None:
    if match.get("espn"):
        return match["espn"]["slug"], match["espn"]["id"]
    slug = ESPN_SLUG.get(match["league"])
    if not slug:
        return None
    day = match["kickoff"][:10]
    key = (slug, day)
    if key not in boards:
        try:
            d = dt.date.fromisoformat(day)
            boards[key] = espn._fetch_range("soccer", slug, d, d)[0]
        except Exception as e:  # noqa: BLE001
            log.info("espn board %s %s: %s", slug, day, e)
            boards[key] = []
    names = [g["h"] for g in boards[key]]
    h = match_name(match["home"], names)
    if not h:
        return None
    g = next(g for g in boards[key] if g["h"] == h)
    return slug, g["id"]


def lineups(matches: list[dict]) -> dict[str, dict]:
    now = now_utc()
    boards: dict = {}
    out = {}
    for m in matches:
        if m["sport"] != "football":
            continue
        t = parse_iso(m["kickoff"])
        if not (now - dt.timedelta(hours=2) <= t <= now + dt.timedelta(minutes=LINEUP_BEFORE_MIN)):
            continue
        ev = _espn_event_for(m, boards)
        if not ev:
            continue
        try:
            ro = parse_rosters(espn.summary("soccer", ev[0], ev[1]))
        except Exception as e:  # noqa: BLE001
            log.info("lineup %s: %s", m["id"], e)
            continue
        if ro:
            out[m["id"]] = ro
    return out


# --------------------------------------------------------------------------
def collect(matches: list[dict], today: dt.date) -> tuple[dict[str, dict], list[str]]:
    """Return ({match id: {"lineup": ..., "absent": {"home": [...], "away": [...]}}}, errors)."""
    errors = []
    by_team: dict[str, dict[str, list[dict]]] = {}     # sport -> team -> absences

    def safe(name, fn, *a):
        try:
            return fn(*a)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {e}")
            return None

    fpl = safe("FPL", fpl_absences) or {}
    by_team["E0"] = fpl
    by_team["nba"] = safe("ESPN NBA injuries", espn_injuries, "basketball", "nba") or {}
    by_team["nhl"] = safe("ESPN NHL injuries", espn_injuries, "hockey", "nhl") or {}
    apif = safe("API-Football injuries", api_football_injuries, [today + dt.timedelta(days=i) for i in range(4)]) or []
    lu = safe("ESPN lineups", lineups, matches) or {}

    out: dict[str, dict] = {}
    for m in matches:
        key = m["league"] if m["sport"] == "football" else m["sport"]
        pool = by_team.get(key, {})
        absent = {}
        for side in ("home", "away"):
            team = m[side]
            lst = []
            hit = match_name(team, list(pool)) if pool else None
            if hit:
                lst += pool[hit]
            if m["sport"] == "football" and apif:
                day = m["kickoff"][:10]
                cands = [r for r in apif if r["date"] == day]
                tn = match_name(team, list({r["team"] for r in cands if r["team"]}), cutoff=0.8) if cands else None
                for r in cands:
                    if tn and r["team"] == tn and not any(same_player(r["name"], x["name"]) for x in lst):
                        lst.append({k: r[k] for k in ("name", "status", "chance", "reason", "src")})
            absent[side] = lst
        entry = {}
        if any(absent.values()):
            entry["absent"] = absent
        if m["id"] in lu:
            entry["lineup"] = lu[m["id"]]
        if entry:
            out[m["id"]] = entry
    return out, errors
