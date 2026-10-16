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
from .football import ESPN_SLUG
from .util import cached_get, http_get, match_name, norm_name, now_utc, parse_iso

log = logging.getLogger("pipeline.availability")

LINEUP_BEFORE_MIN = 100
# injury sources per league / sport of the last collect() run, e.g. {"E0": ["FPL"], "nba": ["ESPN"]}.
# A key that is missing has no injury data (the export says "injury data: none").
LAST_COVERAGE: dict[str, list[str]] = {}   # ESPN publishes lineups about 60 minutes before kick-off


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
            # chance to play by report status; "day-to-day" players usually play
            chance = {"day-to-day": 75, "probable": 85, "game time decision": 50, "questionable": 50,
                      "doubtful": 20}.get(low)
            if low in ("out", "injured reserve", "ir", "suspension", "suspended") or "out" in low:
                kind, chance = "out", 0
            elif chance is not None:
                kind = "doubt"
            else:
                continue
            out.setdefault(tname, []).append({
                "name": ath.get("displayName"), "status": kind, "chance": chance,
                "reason": (inj.get("shortComment") or status)[:80], "src": "ESPN"})
    return out


# --------------------------------------------------------------------------
# API-Football (optional)
# --------------------------------------------------------------------------
def clean_key(raw: str) -> str:
    """Remove quotes, spaces and a pasted header name ("x-apisports-key: ...")."""
    k = raw.strip().strip("\"'").strip()
    for sep in (":", "="):
        if sep in k:
            k = k.rsplit(sep, 1)[1].strip().strip("\"'")
    return k


def key_shape(key: str) -> str:
    """Length and character set of the key, never the key itself (for the error message)."""
    hexa = all(c in "0123456789abcdefABCDEF" for c in key)
    same = key == os.environ.get("ODDS_API_KEY", "").strip()
    return f"ključ: {len(key)} znakov, {'hex' if hexa else 'črke+številke'}" + (", ISTI kot ODDS_API_KEY" if same else "")


def api_football_injuries(dates: list[dt.date]) -> list[dict]:
    key = clean_key(os.environ.get("API_FOOTBALL_KEY", ""))
    if not key:
        return []
    out = []
    # keys from dashboard.api-football.com use the direct host, keys from RapidAPI use the RapidAPI host
    hosts = [("https://v3.football.api-sports.io/injuries", {"x-apisports-key": key}),
             ("https://api-football-v1.p.rapidapi.com/v3/injuries",
              {"x-rapidapi-key": key, "x-rapidapi-host": "api-football-v1.p.rapidapi.com"})]
    for d in dates:
        j, fails = None, []
        for (url, headers), name in zip(hosts, ("api-sports", "RapidAPI")):
            try:
                j = cached_get(url, params={"date": d.isoformat()}, headers=headers)
            except Exception as e:  # noqa: BLE001 - RapidAPI answers 401/403 to a wrong key
                fails.append(f"{name}: {str(e)[:60]}")
                j = None
                continue
            errs = j.get("errors") if isinstance(j, dict) else None
            if errs and (len(errs) if isinstance(errs, (list, dict)) else 1):
                fails.append(f"{name}: {str(errs)[:90]}")
                j = None
                continue
            break
        if j is None:
            raise RuntimeError(" | ".join(fails) + f" | {key_shape(key)}")
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
def collect(matches: list[dict], today: dt.date, extra: dict[str, dict] | None = None
            ) -> tuple[dict[str, dict], list[str]]:
    """Return ({match id: {"lineup": ..., "absent": {"home": [...], "away": [...]}}}, errors).
    extra: more absences by league / sport key, e.g. {"euroleague": {team: [...]}}."""
    errors = []
    by_team: dict[str, dict[str, list[dict]]] = {k: v for k, v in (extra or {}).items() if v}

    def safe(name, fn, *a):
        try:
            return fn(*a)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {e}")
            return None

    cov: dict[str, list[str]] = {k: ["EuroLeague (izpuščene tekme)" if k == "euroleague" else k]
                                 for k in (extra or {})}
    fpl = safe("FPL", fpl_absences)
    by_team["E0"] = fpl or {}
    if fpl is not None:
        cov.setdefault("E0", []).append("FPL")
    for sp, sport, league in (("nba", "basketball", "nba"), ("nhl", "hockey", "nhl")):
        got = safe(f"ESPN {league.upper()} injuries", espn_injuries, sport, league)
        by_team[sp] = got or {}
        if got is not None:
            cov.setdefault(sp, []).append("ESPN")
    n_err = len(errors)
    apif = safe("API-Football injuries", api_football_injuries, [today + dt.timedelta(days=i) for i in range(4)]) or []
    if len(errors) == n_err and os.environ.get("API_FOOTBALL_KEY", "").strip():
        for m in matches:
            if m["sport"] == "football":
                cov.setdefault(m["league"], [])
                if "API-Football" not in cov[m["league"]]:
                    cov[m["league"]].append("API-Football")
    LAST_COVERAGE.clear()
    LAST_COVERAGE.update(cov)
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
