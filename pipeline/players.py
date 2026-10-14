"""Player stats and player props.

* Football: Understat season data (shots, xG, xA, key passes, minutes) for the top 5 leagues.
* NHL: official NHL stats REST API (shots, goals, points per game).
* NBA: ESPN box scores (points, rebounds, assists, threes), cached game by game.
"""
from __future__ import annotations

import codecs
import datetime as dt
import json
import logging
import math
import re
import time

from . import espn, models
from .util import CACHE_DIR, cached_get, is_quick, match_name, parse_iso, read_json, to_float, write_json

log = logging.getLogger("pipeline.players")


def p_at_least(lam: float, k: int) -> float:
    """P(X >= k) for X ~ Poisson(lam)."""
    if k <= 0:
        return 1.0
    return models.poisson_sf(lam, k - 0.5)


def prop(label: str, p: float, stat: str, line: float, kind: str = "over") -> dict:
    return {"label": label, "p": round(float(p), 4), "stat": stat, "line": line, "kind": kind}


def football_props(lam_sh: float, lam_sot: float, lam_g: float, lam_a: float) -> list[dict]:
    return [
        prop("1+ strel", p_at_least(lam_sh, 1), "shots", 0.5),
        prop("2+ strela", p_at_least(lam_sh, 2), "shots", 1.5),
        prop("3+ streli", p_at_least(lam_sh, 3), "shots", 2.5),
        prop("1+ strel v okvir", p_at_least(lam_sot, 1), "sot", 0.5),
        prop("2+ strela v okvir", p_at_least(lam_sot, 2), "sot", 1.5),
        prop("Da gol", 1 - math.exp(-lam_g), "goals", 0.5),
        prop("Poda za gol", 1 - math.exp(-lam_a), "assists", 0.5),
        prop("Gol ali podaja", 1 - math.exp(-(lam_g + lam_a)), "g+a", 0.5),
    ]


# --------------------------------------------------------------------------
# Football (Understat)
# --------------------------------------------------------------------------
UNDERSTAT_LEAGUES = {"E0": "EPL", "SP1": "La_liga", "D1": "Bundesliga", "I1": "Serie_A", "F1": "Ligue_1"}


def understat_players(league: str, season: int) -> list[dict]:
    """Season player table. Understat moved data behind an XHR endpoint in 2025;
    older pages embed it as JSON.parse('...'). Try both."""
    try:
        j = cached_get(f"https://understat.com/getLeagueData/{league}/{season}",
                     headers={"X-Requested-With": "XMLHttpRequest",
                              "Referer": f"https://understat.com/league/{league}/{season}"})
        players = j.get("players") if isinstance(j, dict) else None
        if players:
            return players
    except Exception as e:  # noqa: BLE001
        log.info("understat xhr %s %s: %s", league, season, e)
    html = cached_get(f"https://understat.com/league/{league}/{season}", as_json=False)
    m = re.search(r"playersData\s*=\s*JSON\.parse\('(.*?)'\)", html)
    if not m:
        raise RuntimeError("understat: playersData not found")
    return json.loads(codecs.decode(m.group(1), "unicode_escape"))


def _uf(p: dict, k: str) -> float:
    return to_float(p.get(k)) or 0.0


def football_players(today: dt.date, fb: dict) -> tuple[list[dict], list[str]]:
    """fb = output of football.build(). Return (players, errors)."""
    season = today.year if today.month >= 7 else today.year - 1
    errors = []
    out = []
    matches_by_team: dict[tuple[str, str], dict] = {}
    for m in sorted(fb["matches"] + (fb.get("extra_matches") or []), key=lambda x: x["kickoff"]):
        for side in ("home", "away"):
            dom = (m.get("domestic") or {}).get(side)      # UEFA cup match: [division, domestic team]
            key = tuple(dom) if dom else (m["league"], m[side])
            matches_by_team.setdefault(key, m)
    for div, us_league in UNDERSTAT_LEAGUES.items():
        try:
            cur = understat_players(us_league, season)
        except Exception as e:  # noqa: BLE001
            errors.append(f"Understat {us_league} {season}: {e}")
            continue
        prev_path = CACHE_DIR / "understat" / f"{us_league}_{season - 1}.json"
        prev = read_json(prev_path)
        if prev is None:
            try:
                prev = understat_players(us_league, season - 1)
                write_json(prev_path, prev)
            except Exception as e:  # noqa: BLE001
                prev = []
                errors.append(f"Understat {us_league} {season - 1}: {e}")
        time.sleep(1.0)
        prev_by_id = {str(p.get("id")): p for p in prev}
        rows = fb.get("cur_rows", {}).get(div, [])
        fd_teams = sorted({r["HomeTeam"] for r in rows} | {r["AwayTeam"] for r in rows})
        team_gp: dict[str, int] = {}
        for r in rows:
            if r.get("FTHG"):
                team_gp[r["HomeTeam"]] = team_gp.get(r["HomeTeam"], 0) + 1
                team_gp[r["AwayTeam"]] = team_gp.get(r["AwayTeam"], 0) + 1
        hs = sum(to_float(r.get("HS")) or 0 for r in rows)
        hst = sum(to_float(r.get("HST")) or 0 for r in rows)
        sot_ratio = (hst / hs) if hs > 200 else 0.34
        gr = fb.get("goal_ratings", {}).get(div)
        sr = fb.get("shot_ratings", {}).get(div)
        name_cache: dict[str, str | None] = {}
        for p in cur:
            team_us = (p.get("team_title") or "").split(",")[-1].strip()
            if team_us not in name_cache:
                name_cache[team_us] = match_name(team_us, fd_teams)
            team = name_cache[team_us]
            mins = _uf(p, "time"); games = _uf(p, "games")
            pp = prev_by_id.get(str(p.get("id")), {})
            pmins = _uf(pp, "time") * 0.5
            tot_min = mins + pmins
            if tot_min < 270 or games < 1:
                continue

            def rate(k):
                return (_uf(p, k) + _uf(pp, k) * 0.5) / tot_min * 90

            sh90, xg90, xa90, kp90, npxg90 = rate("shots"), rate("xG"), rate("xA"), rate("key_passes"), rate("npxG")
            row = {
                "name": p.get("player_name"), "team": team or team_us, "league": div, "pos": p.get("position"),
                "gp": int(games), "min": int(mins), "goals": int(_uf(p, "goals")), "assists": int(_uf(p, "assists")),
                "shots": int(_uf(p, "shots")), "xg": round(_uf(p, "xG"), 2), "xa": round(_uf(p, "xA"), 2),
                "kp": int(_uf(p, "key_passes")), "sh90": round(sh90, 2), "xg90": round(xg90, 2),
                "npxg90": round(npxg90, 2), "xa90": round(xa90, 2), "kp90": round(kp90, 2),
                "ycards": int(_uf(p, "yellow_cards")),
            }
            m = matches_by_team.get((div, team)) if team else None
            if m:
                home = m["home"] == team or (m.get("domestic") or {}).get("home") == [div, team]
                opp = m["away"] if home else m["home"]
                tgp = team_gp.get(team, 0)
                avail = min(1.0, games / tgp) if tgp >= 3 else 0.85
                exp_min = min(90.0, mins / games) * avail
                ratio_s = ratio_g = 1.0
                if sr and opp in sr.dfn:
                    ratio_s = math.exp(sr.dfn[opp] + sr.home * ((1 if home else 0) - 0.5))
                if gr and opp in gr.dfn:
                    ratio_g = math.exp(gr.dfn[opp] + gr.home * ((1 if home else 0) - 0.5))
                lam_sh = sh90 * exp_min / 90 * ratio_s
                lam_sot = lam_sh * sot_ratio
                lam_g = xg90 * exp_min / 90 * ratio_g
                lam_a = xa90 * exp_min / 90 * ratio_g
                row["avail"] = round(avail, 2)
                row["next"] = {"match": m["id"], "opp": opp, "home": home, "kickoff": m["kickoff"],
                               "exp_min": round(exp_min), "exp_shots": round(lam_sh, 2), "exp_sot": round(lam_sot, 2),
                               "lam": [round(x, 4) for x in (lam_sh, lam_sot, lam_g, lam_a)]}
                if avail >= 0.45:
                    row["props"] = football_props(lam_sh, lam_sot, lam_g, lam_a)
            out.append(row)
    out.sort(key=lambda r: -r["xg90"] * min(1, r["min"] / 900))
    return out, errors


# --------------------------------------------------------------------------
# NHL (api.nhle.com stats REST)
# --------------------------------------------------------------------------
NHL_ABBR = {
    "ANA": "Anaheim Ducks", "BOS": "Boston Bruins", "BUF": "Buffalo Sabres", "CGY": "Calgary Flames",
    "CAR": "Carolina Hurricanes", "CHI": "Chicago Blackhawks", "COL": "Colorado Avalanche",
    "CBJ": "Columbus Blue Jackets", "DAL": "Dallas Stars", "DET": "Detroit Red Wings", "EDM": "Edmonton Oilers",
    "FLA": "Florida Panthers", "LAK": "Los Angeles Kings", "MIN": "Minnesota Wild", "MTL": "Montreal Canadiens",
    "NSH": "Nashville Predators", "NJD": "New Jersey Devils", "NYI": "New York Islanders", "NYR": "New York Rangers",
    "OTT": "Ottawa Senators", "PHI": "Philadelphia Flyers", "PIT": "Pittsburgh Penguins", "SJS": "San Jose Sharks",
    "SEA": "Seattle Kraken", "STL": "St. Louis Blues", "TBL": "Tampa Bay Lightning", "TOR": "Toronto Maple Leafs",
    "UTA": "Utah Mammoth", "VAN": "Vancouver Canucks", "VGK": "Vegas Golden Knights", "WSH": "Washington Capitals",
    "WPG": "Winnipeg Jets",
}
NHL_STATS = "https://api.nhle.com/stats/rest/en"


def _nhl_rows(kind: str, season_id: str) -> list[dict]:
    exp = f"gameTypeId=2 and seasonId<={season_id} and seasonId>={season_id}"
    j = cached_get(f"{NHL_STATS}/{kind}/summary", params={"limit": -1, "start": 0, "cayenneExp": exp})
    return j.get("data", [])


def nhl_players(today: dt.date, nhl: dict) -> tuple[list[dict], list[str]]:
    y = today.year if today.month >= 9 else today.year - 1
    cur_id, prev_id = f"{y}{y + 1}", f"{y - 1}{y}"
    errors = []
    try:
        cur = _nhl_rows("skater", cur_id)
    except Exception as e:  # noqa: BLE001
        return [], [f"NHL stats skater: {e}"]
    prev_path = CACHE_DIR / "nhl" / f"skaters_{prev_id}.json"
    prev = read_json(prev_path)
    if prev is None:
        try:
            prev = _nhl_rows("skater", prev_id)
            write_json(prev_path, prev)
        except Exception as e:  # noqa: BLE001
            prev = []
            errors.append(f"NHL stats {prev_id}: {e}")
    try:
        teams_cur = _nhl_rows("team", cur_id)
    except Exception:  # noqa: BLE001
        teams_cur = []
    teams_prev = read_json(CACHE_DIR / "nhl" / f"teams_{prev_id}.json")
    if teams_prev is None:
        try:
            teams_prev = _nhl_rows("team", prev_id)
            write_json(CACHE_DIR / "nhl" / f"teams_{prev_id}.json", teams_prev)
        except Exception:  # noqa: BLE001
            teams_prev = []

    # shots against per game, blended
    sa: dict[str, list[float]] = {}
    for rows, wt in ((teams_cur, 1.0), (teams_prev, 0.5)):
        for t in rows:
            gp = to_float(t.get("gamesPlayed")) or 0
            v = to_float(t.get("shotsAgainstPerGame"))
            if gp and v:
                acc = sa.setdefault(t.get("teamFullName"), [0.0, 0.0])
                acc[0] += v * gp * wt; acc[1] += gp * wt
    sa_avg = {k: v[0] / v[1] for k, v in sa.items() if v[1]}
    league_sa = sum(sa_avg.values()) / len(sa_avg) if sa_avg else 30.0

    model = nhl.get("model")
    match_by_team = {}
    for m in nhl.get("matches", []):
        match_by_team.setdefault(m["home"], m)
        match_by_team.setdefault(m["away"], m)
    espn_teams = list(match_by_team)
    season_started = any(r.get("gamesPlayed", 0) >= 3 for r in cur)
    prev_by_id = {r.get("playerId"): r for r in prev}
    base = cur if season_started else prev
    out = []
    for r in base:
        pid = r.get("playerId")
        c = r if season_started else {}
        pv = prev_by_id.get(pid, {}) if season_started else r
        gp = (to_float(c.get("gamesPlayed")) or 0) + 0.5 * (to_float(pv.get("gamesPlayed")) or 0)
        if gp < 3:
            continue

        def rate(k):
            return ((to_float(c.get(k)) or 0) + 0.5 * (to_float(pv.get(k)) or 0)) / gp

        abbr = (r.get("teamAbbrevs") or "").split(",")[-1].strip()
        team_full = NHL_ABBR.get(abbr, abbr)
        team = match_name(team_full, espn_teams) if espn_teams else None
        row = {
            "name": r.get("skaterFullName"), "team": team or team_full, "abbr": abbr, "pos": r.get("positionCode"),
            "gp": int(to_float(r.get("gamesPlayed")) or 0), "goals": int(to_float(r.get("goals")) or 0),
            "assists": int(to_float(r.get("assists")) or 0), "points": int(to_float(r.get("points")) or 0),
            "shots": int(to_float(r.get("shots")) or 0),
            "toi": round((to_float(r.get("timeOnIcePerGame")) or 0) / 60, 1),
            "sog_g": round(rate("shots"), 2), "g_g": round(rate("goals"), 2), "p_g": round(rate("points"), 2),
        }
        m = match_by_team.get(team) if team else None
        if m:
            home = m["home"] == team
            opp = m["away"] if home else m["home"]
            opp_full = match_name(opp, list(sa_avg)) if sa_avg else None
            f_sh = (sa_avg.get(opp_full, league_sa) / league_sa) if opp_full else 1.0
            f_g = math.exp(model.dfn.get(opp, 0)) if model else 1.0
            hf = 1.03 if home else 0.97
            lam_s = rate("shots") * f_sh * hf
            lam_g = rate("goals") * f_g * hf
            lam_p = rate("points") * f_g * hf
            row["next"] = {"match": m["id"], "opp": opp, "home": home, "kickoff": m["kickoff"],
                           "exp_sog": round(lam_s, 2)}
            row["props"] = [
                prop("Strelov na gol 2+", p_at_least(lam_s, 2), "sog", 1.5),
                prop("Strelov na gol 3+", p_at_least(lam_s, 3), "sog", 2.5),
                prop("Strelov na gol 4+", p_at_least(lam_s, 4), "sog", 3.5),
                prop("Da gol", p_at_least(lam_g, 1), "goals", 0.5),
                prop("1+ točka", p_at_least(lam_p, 1), "points", 0.5),
                prop("2+ točki", p_at_least(lam_p, 2), "points", 1.5),
            ]
        out.append(row)
    out.sort(key=lambda r: -r["p_g"])
    return out, errors


# --------------------------------------------------------------------------
# NBA (ESPN box scores)
# --------------------------------------------------------------------------
def _made(v) -> float | None:
    if v is None:
        return None
    s = str(v)
    if "-" in s:
        s = s.split("-")[0]
    return to_float(s)


def _box_line(p: dict) -> list | None:
    st = p["stats"]
    mins = to_float(str(st.get("MIN", "0")).split(":")[0])
    if not mins:
        return None
    pts, reb, ast = to_float(st.get("PTS")), to_float(st.get("REB")), to_float(st.get("AST"))
    tpm = _made(st.get("3PT") or st.get("3PM"))
    if pts is None or reb is None or ast is None:
        return None
    return [p["id"], p["name"], p["team"], p.get("pos"), mins, pts, reb, ast, tpm or 0]


def update_nba_boxes(games: dict[str, dict], max_new: int = 250, league: str = "nba") -> dict:
    path = CACHE_DIR / f"{league}_box.json"
    cache = read_json(path, {"games": {}})
    done = [g for g in games.values() if g.get("done") and g.get("st") in (2, 3)]
    done.sort(key=lambda g: g["d"], reverse=True)
    if done:
        newest = parse_iso(done[0]["d"]).date()
        cutoff = (newest - dt.timedelta(days=150)).isoformat()
        done = [g for g in done if g["d"][:10] >= cutoff]
    new = 0
    if is_quick():
        max_new = 15   # quick runs only pick up the latest finished games
    for g in done:
        if g["id"] in cache["games"] or new >= max_new:
            continue
        try:
            lines = [ln for ln in (_box_line(p) for p in espn.parse_box_players(espn.summary("basketball", league, g["id"])))
                     if ln]
            cache["games"][g["id"]] = {"d": g["d"][:10], "p": lines}
            new += 1
            time.sleep(0.2)
        except Exception as e:  # noqa: BLE001
            log.warning("nba box %s: %s", g["id"], e)
    keep = {g["id"] for g in done}
    cache["games"] = {k: v for k, v in cache["games"].items() if k in keep}
    write_json(path, cache)
    log.info("%s box scores: %d cached, %d new", league, len(cache["games"]), new)
    return cache


def nba_players(nba: dict, box: dict) -> list[dict]:
    model = nba.get("model")
    per: dict[str, list] = {}
    for gid, g in box["games"].items():
        for ln in g["p"]:
            per.setdefault(ln[0], []).append((g["d"], ln))
    match_by_team = {}
    for m in nba.get("matches", []):
        match_by_team.setdefault(m["home"], m)
        match_by_team.setdefault(m["away"], m)
    out = []
    for pid, rows in per.items():
        rows.sort(key=lambda x: x[0])
        last = [ln for _, ln in rows[-15:]]
        if len(last) < 5:
            continue
        name, team, pos = last[-1][1], last[-1][2], last[-1][3]
        n = len(last)

        def avg(i):
            return sum(x[i] for x in last) / n

        def sd(i):
            mu = avg(i)
            return math.sqrt(sum((x[i] - mu) ** 2 for x in last) / max(1, n - 1))

        mins, pts, reb, ast, tpm = avg(4), avg(5), avg(6), avg(7), avg(8)
        if mins < 12:
            continue
        row = {"name": name, "team": team, "pos": pos, "gp": n, "min": round(mins, 1), "pts": round(pts, 1),
               "reb": round(reb, 1), "ast": round(ast, 1), "tpm": round(tpm, 1), "pra": round(pts + reb + ast, 1),
               "last_date": rows[-1][0]}
        m = match_by_team.get(team)
        if m:
            home = m["home"] == team
            opp = m["away"] if home else m["home"]
            f = 1.0
            if model:
                f = (model.mu + model.dfn.get(opp, 0)) / model.mu
            props = []
            specs = (("pts", "točk", pts * f, max(sd(5), 4.0)), ("reb", "skokov", reb, max(sd(6), 2.0)),
                     ("ast", "podaj", ast, max(sd(7), 1.6)),
                     ("pra", "T+S+P", (pts * f + reb + ast), max(math.sqrt(sd(5) ** 2 + sd(6) ** 2 + sd(7) ** 2), 5.0)))
            for stat, unit, mu, s in specs:
                for L in _lines(mu):
                    props.append(prop(f"Več kot {L:g} {unit}", 1 - models.norm_cdf((L - mu) / s), stat, L))
            if tpm >= 0.8:
                for k in (1, 2, 3, 4):
                    if k <= tpm + 2:
                        props.append(prop(f"{k}+ trojk", p_at_least(tpm, k), "tpm", k - 0.5))
            row["next"] = {"match": m["id"], "opp": opp, "home": home, "kickoff": m["kickoff"],
                           "proj_pts": round(pts * f, 1)}
            row["props"] = props
        out.append(row)
    out.sort(key=lambda r: -r["pts"])
    return out


def _lines(mu: float) -> list[float]:
    """Typical book-style lines around a projection: x.5 values within about +-25 %."""
    if mu < 2:
        return [0.5, 1.5]
    step = 1 if mu < 8 else (2 if mu < 16 else 3)
    c = math.floor(mu) + 0.5
    return sorted({max(0.5, c - 2 * step), c - step, c, c + step, c + 2 * step})
