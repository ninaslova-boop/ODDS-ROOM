"""NHL: likely starting goalie and his effect on the expected goals.

The NHL does not publish the starting goalie before the game. We estimate it:
* the goalie with most starts this season is the No. 1, the next one the No. 2;
* normally the No. 1 starts with probability = his share of starts (shrunk to 0.7);
* on the second night of a back-to-back the No. 1 starts only with B2B_P1.

Goalie quality = save percentage, current season + PREV_W of the previous season, shrunk to the
league average with SHRINK_SHOTS shots (save percentage is a noisy statistic).
The team rating already contains the usual goalie mix of the team, so only the difference
between the expected goalie today and that usual mix changes the opponent's goals:
    factor = (1 - sv today) / (1 - sv usual),  limited to [1 - MAX_EFFECT, 1 + MAX_EFFECT].
"""
from __future__ import annotations

import datetime as dt
import logging

from .players import NHL_ABBR, _nhl_rows
from .util import CACHE_DIR, match_name, parse_iso, read_json, write_json

log = logging.getLogger("pipeline.goalies")

SHRINK_SHOTS = 2000
PREV_W = 0.6
B2B_P1 = 0.35
P1_PRIOR = 0.7
MAX_EFFECT = 0.10


def _team_of(row: dict) -> str | None:
    ab = (row.get("teamAbbrevs") or "").split(",")[-1].strip()
    return NHL_ABBR.get(ab)


def load(today: dt.date) -> tuple[list[dict], list[dict]]:
    y = today.year if today.month >= 9 else today.year - 1
    cur_id, prev_id = f"{y}{y + 1}", f"{y - 1}{y}"
    cur = _nhl_rows("goalie", cur_id)
    path = CACHE_DIR / "nhl" / f"goalies_{prev_id}.json"
    prev = read_json(path)
    if prev is None:
        try:
            prev = _nhl_rows("goalie", prev_id)
            write_json(path, prev)
        except Exception as e:  # noqa: BLE001
            log.info("goalies %s: %s", prev_id, e)
            prev = []
    return cur, prev


def ratings(cur: list[dict], prev: list[dict]) -> tuple[dict[str, list[dict]], float]:
    """{team: [goalies sorted by starts]}, league save percentage."""
    shots = sum(r.get("shotsAgainst") or 0 for r in cur) + PREV_W * sum(r.get("shotsAgainst") or 0 for r in prev)
    saves = sum(r.get("saves") or 0 for r in cur) + PREV_W * sum(r.get("saves") or 0 for r in prev)
    lg = saves / shots if shots > 500 else 0.900
    prev_by = {r.get("playerId"): r for r in prev}
    teams: dict[str, list[dict]] = {}
    for r in cur:
        team = _team_of(r)
        if not team:
            continue
        p = prev_by.get(r.get("playerId")) or {}
        sh = (r.get("shotsAgainst") or 0) + PREV_W * (p.get("shotsAgainst") or 0)
        sv = (r.get("saves") or 0) + PREV_W * (p.get("saves") or 0)
        teams.setdefault(team, []).append({
            "name": r.get("goalieFullName"), "gs": int(r.get("gamesStarted") or 0),
            "sv": (sv + lg * SHRINK_SHOTS) / (sh + SHRINK_SHOTS),
            "sv_raw": r.get("savePct")})
    for lst in teams.values():
        lst.sort(key=lambda g: -g["gs"])
    return teams, lg


def played_yesterday(hist: list[dict], team: str, day: dt.date) -> bool:
    y = day - dt.timedelta(days=1)
    return any(g["date"] == y and team in (g["home"], g["away"]) for g in hist)


def expect(goalies: list[dict], b2b: bool) -> dict | None:
    """Expected save percentage today and the usual one of the team."""
    gs = [g for g in goalies if g["gs"] > 0]
    if not gs:
        return None
    total = sum(g["gs"] for g in gs)
    usual = sum(g["sv"] * g["gs"] for g in gs) / total
    g1 = gs[0]
    if len(gs) == 1:
        return {"name": g1["name"], "p": 1.0, "sv": round(g1["sv"], 4), "today": g1["sv"], "usual": usual,
                "b2b": b2b}
    share = (g1["gs"] + P1_PRIOR * 10) / (total + 10)
    p1 = B2B_P1 if b2b else share
    rest = gs[1:]
    rest_total = sum(g["gs"] for g in rest)
    today = p1 * g1["sv"] + (1 - p1) * sum(g["sv"] * g["gs"] for g in rest) / rest_total
    likely = g1 if p1 >= 0.5 else rest[0]
    return {"name": likely["name"], "p": round(p1 if likely is g1 else (1 - p1) * rest[0]["gs"] / rest_total, 2),
            "sv": round(likely["sv"], 4), "today": today, "usual": usual, "b2b": b2b,
            "alt": rest[0]["name"] if likely is g1 else g1["name"]}


def factor(e: dict) -> float:
    f = (1 - e["today"]) / (1 - e["usual"])
    return max(1 - MAX_EFFECT, min(1 + MAX_EFFECT, f))


def apply(matches: list[dict], today: dt.date, hist: list[dict], rebuild) -> int:
    """Add m["goalies"] to NHL matches and scale the opponent's goals. Returns adjusted matches."""
    nhl = [m for m in matches if m["sport"] == "nhl"]
    if not nhl:
        return 0
    cur, prev = load(today)
    teams, _ = ratings(cur, prev)
    names = list(teams)
    n = 0
    for m in nhl:
        day = parse_iso(m["kickoff"]).date()
        info, f = {}, {}
        for side in ("home", "away"):
            t = match_name(m[side], names) if m[side] not in teams else m[side]
            if not t:
                continue
            e = expect(teams[t], played_yesterday(hist, m[side], day))
            if not e:
                continue
            f[side] = factor(e)
            info[side] = {k: e[k] for k in ("name", "p", "sv", "b2b") if k in e}
            if e.get("alt"):
                info[side]["alt"] = e["alt"]
            info[side]["factor"] = round(f[side], 3)
        if not info:
            continue
        m["goalies"] = info
        # the home goalie changes the goals of the away team and the other way round
        fh, fa = f.get("away", 1.0), f.get("home", 1.0)
        if abs(fh - 1) > 0.01 or abs(fa - 1) > 0.01:
            lh, la = m["exp"]
            rebuild(m, lh * fh, la * fa)
            n += 1
    return n
