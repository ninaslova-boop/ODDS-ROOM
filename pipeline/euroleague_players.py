"""EuroLeague players: box scores from the official API (no key) and likely absences.

The EuroLeague does not publish lineups or injury reports before a game. A regular player
(MIN_REGULAR minutes on average) who missed the last games of his team is probably injured:
* 2 missed games in a row: "doubt", chance CHANCE_2,
* 3 or more: "out".
"""
from __future__ import annotations

import datetime as dt
import logging
from concurrent.futures import ThreadPoolExecutor

from .players import nba_players
from .sports import EL_BASES
from .util import CACHE_DIR, cached_get, is_quick, read_json, to_float, write_json

log = logging.getLogger("pipeline.euroleague_players")

MIN_REGULAR = 15.0
CHANCE_2 = 30
WORKERS = 6


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


def _fetch(g: dict) -> list[list] | None:
    last = None
    for base in EL_BASES:
        try:
            j = cached_get(f"{base}/competitions/E/seasons/E{g['yr']}/games/{g['code']}/stats")
            lines = parse_stats(j)
            return lines if lines else None
        except Exception as e:  # noqa: BLE001
            last = e
    log.info("euroleague box %s: %s", g["id"], last)
    return None


def update_boxes(games: dict[str, dict], max_new: int = 400) -> dict:
    """Same cache format as the NBA box scores: {"games": {id: {"d", "p": [...], "t": [home, away]}}}."""
    path = CACHE_DIR / "euroleague_box.json"
    cache = read_json(path, {"games": {}})
    done = sorted([g for g in games.values() if g.get("done") and g.get("code") is not None],
                  key=lambda g: g["d"], reverse=True)
    if is_quick():
        max_new = 10
    todo = [g for g in done if g["id"] not in cache["games"]][:max_new]
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for g, lines in zip(todo, ex.map(_fetch, todo)):
            if lines:
                cache["games"][g["id"]] = {"d": g["d"][:10], "p": lines, "t": [g["h"], g["a"]]}
    keep = {g["id"] for g in done}
    cache["games"] = {k: v for k, v in cache["games"].items() if k in keep}
    write_json(path, cache)
    log.info("euroleague box scores: %d cached, %d new", len(cache["games"]), len(todo))
    return cache


def players(el: dict, box: dict, season_start: dt.date) -> list[dict]:
    """Player rows like the NBA ones; only players who played in the current season."""
    rows = nba_players(el, box)
    for r in rows:   # names cached before the hyphen fix
        r["name"] = " ".join("-".join(x[:1].upper() + x[1:] for x in w.split("-")) for w in r["name"].split())
    return [r for r in rows if r["last_date"] >= season_start.isoformat()]


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
