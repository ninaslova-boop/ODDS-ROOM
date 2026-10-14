"""Adjust predictions for lineups and absences.

* Player props: a player who is out loses his props; a confirmed starter plays his usual
  minutes; a substitute gets about 20 minutes; a doubtful player is scaled by his chance.
* Team strength: the attack of a team goes down by the share of its expected output
  (football: xG + xA, NBA: points, NHL: points) that the missing players carry.
  Only part of it is lost, because a replacement plays (REPLACE).
"""
from __future__ import annotations

import logging

from . import football, models, players, soccer_espn, sports
from .availability import find_player

log = logging.getLogger("pipeline.adjust")

REPLACE = {"football": 0.45, "nba": 0.55, "nhl": 0.6}   # share of a missing player's output a replacement keeps
MIN_FACTOR = {"football": 0.72, "nba": 0.85, "nhl": 0.85}
SUB_MINUTES = 20


def _status(name: str, side_info: dict, lineup_side: dict | None) -> tuple[str, float]:
    """Return (status, chance 0..1) for a player: start / bench / out / doubt / ok."""
    if lineup_side:
        xi = [p["name"] for p in lineup_side["xi"]]
        bench = [p["name"] for p in lineup_side["bench"]]
        if find_player(name, xi):
            return "start", 1.0
        if find_player(name, bench):
            return "bench", 1.0
        return "out", 0.0
    for a in side_info:
        if find_player(name, [a["name"]]) or (a.get("short") and find_player(name, [a["short"]])):
            chance = (a.get("chance") if a.get("chance") is not None else (0 if a["status"] == "out" else 50)) / 100
            if chance >= 0.75:
                return "ok", 1.0           # likely to play: no change
            return ("out" if chance <= 0.1 else "doubt"), chance
    return "ok", 1.0


def _rebuild_football(m: dict, lh: float, la: float):
    M = models.score_matrix(lh, la, 10, football.DC_RHO)
    mk = models.goal_markets(M)
    if m["id"].startswith("fbe:"):
        soccer_espn.attach_odds(mk, m.get("odds_raw", {}))
    else:
        football.attach_odds(mk, m.get("odds_raw", {}))
    m["markets"] = mk + [x for x in m["markets"] if x["stat"] != "goals"]
    dist = models.total_distribution(M)
    m.update(exp=[round(lh, 2), round(la, 2)], top_scores=models.top_scores(M), total_dist=dist,
             total_mode=max(dist, key=lambda x: x["p"])["n"])


def _rebuild_nhl(m: dict, lh: float, la: float):
    M, mk = sports.nhl_markets(lh, la, m.get("odds_raw", {}))
    dist = models.total_distribution(M, 10)
    m.update(markets=mk, exp=[round(lh, 2), round(la, 2)], top_scores=models.top_scores(M), total_dist=dist,
             total_mode=max(dist, key=lambda x: x["p"])["n"])


def _rebuild_basketball(m: dict, ph: float, pa: float):
    odds = m.get("odds_raw", {})
    mk = models.basketball_markets(ph, pa, m["sd"][0], m["sd"][1], odds.get("total"), odds.get("spread_home"))
    sports._attach_us_odds(mk, odds)
    m.update(markets=mk, exp=[round(ph, 1), round(pa, 1)])


def _side_rows(m: dict, side: str, index: dict) -> list[dict]:
    """Player rows of one side of a match (any upcoming match, not only the player's next one)."""
    if m["sport"] == "football":
        dom = (m.get("domestic") or {}).get(side)
        div, team = (dom[0], dom[1]) if dom else (m["league"], m[side])
        return [r for r in index.get(("football", team), []) if r.get("league") == div]
    return index.get((m["sport"], m[side]), [])


def _lineup_usable(rows: list[dict], lineup_side: dict | None) -> bool:
    """A lineup is used only when most regulars can be found in it (names can differ between sources)."""
    if not lineup_side:
        return False
    regs = [r for r in rows if r.get("avail", 1) >= 0.6] or rows
    if not regs:
        return False
    names = [p["name"] for p in lineup_side["xi"] + lineup_side["bench"]]
    found = sum(1 for r in regs if find_player(r["name"], names))
    return found / len(regs) >= 0.5


def apply(matches: list[dict], player_out: dict[str, list], avail: dict[str, dict]) -> int:
    """Mutates matches and player rows. Returns the number of adjusted matches."""
    index: dict[tuple[str, str], list[dict]] = {}
    for sport, rows in player_out.items():
        for r in rows or []:
            index.setdefault((sport, r.get("team")), []).append(r)
    n_adj = 0
    for m in matches:
        info = avail.get(m["id"])
        if not info:
            continue
        m["availability"] = info
        sport = m["sport"]
        if sport not in REPLACE:
            continue
        factors = {}
        missing_names: dict[str, list[str]] = {"home": [], "away": []}
        for side in ("home", "away"):
            side_rows = _side_rows(m, side, index)
            lineup_side = (info.get("lineup") or {}).get(side)
            if not _lineup_usable(side_rows, lineup_side):
                if lineup_side:
                    log.info("lineup names do not match player data: %s %s", m["id"], side)
                lineup_side = None
            absent = (info.get("absent") or {}).get(side, [])
            total = lost = 0.0
            for r in side_rows:
                st, chance = _status(r["name"], absent, lineup_side)
                if sport == "football":
                    out = (r.get("xg90", 0) + r.get("xa90", 0)) * r.get("avail", 1)
                    regular = r.get("avail", 1) >= 0.5
                elif sport == "nba":
                    out = r.get("pts", 0)
                    regular = r.get("min", 0) >= 15
                else:
                    out = r.get("p_g", 0)
                    regular = True
                total += out
                if st == "out" and regular:
                    lost += out
                    missing_names[side].append(r["name"])
                elif st == "doubt" and regular:
                    lost += out * (1 - chance)
                elif st == "bench" and regular:
                    lost += out * 0.6
                if r.get("next") and r["next"]["match"] == m["id"]:
                    _adjust_props(r, sport, st, chance)
            if total > 0 and lost > 0:
                f = max(MIN_FACTOR[sport], 1 - (1 - REPLACE[sport]) * lost / total)
                if f < 0.995:
                    factors[side] = round(f, 3)
        if factors:
            fh, fa = factors.get("home", 1.0), factors.get("away", 1.0)
            lh, la = m["exp"]
            if sport == "football":
                _rebuild_football(m, lh * fh, la * fa)
            elif sport == "nhl":
                _rebuild_nhl(m, lh * fh, la * fa)
            elif sport == "nba" and m.get("sd"):
                _rebuild_basketball(m, lh * fh, la * fa)
            m["adj"] = {"factors": factors, "missing": missing_names}
            n_adj += 1
    return n_adj


def _adjust_props(r: dict, sport: str, st: str, chance: float):
    nxt = r["next"]
    nxt["status"] = st
    if st == "out":
        r["props"] = None
        return
    if sport == "football" and nxt.get("lam"):
        base_min = max(1.0, nxt.get("exp_min", 0))
        usual = min(90.0, r["min"] / max(1, r["gp"]))
        new_min = {"start": usual, "bench": SUB_MINUTES, "doubt": base_min * chance}.get(st, base_min)
        k = new_min / base_min
        if abs(k - 1) > 0.01:
            lam = [x * k for x in nxt["lam"]]
            nxt["exp_min"] = round(new_min)
            nxt["exp_shots"], nxt["exp_sot"] = round(lam[0], 2), round(lam[1], 2)
            r["props"] = players.football_props(*lam)
        elif st == "start" and not r.get("props"):
            r["props"] = players.football_props(*nxt["lam"])
    elif st == "doubt" and r.get("props"):
        # basketball / hockey: keep the line, scale probabilities by the chance to play
        for p in r["props"]:
            p["p"] = round(p["p"] * chance, 4)
