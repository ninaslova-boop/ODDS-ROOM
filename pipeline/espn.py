"""ESPN public site API (no key): schedules, scores, odds and box scores for NBA and NHL.

Endpoints (unofficial but public):
  https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard?dates=YYYYMMDD[-YYYYMMDD]
  https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/summary?event={id}
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from .util import CACHE_DIR, american_to_decimal, http_get, is_quick, read_json, to_float, write_json

log = logging.getLogger("pipeline.espn")
BASE = "https://site.api.espn.com/apis/site/v2/sports"
WORKERS = 8


def parse_event(ev: dict) -> dict | None:
    try:
        comp = ev["competitions"][0]
        teams = {c["homeAway"]: c for c in comp["competitors"]}
        h, a = teams["home"], teams["away"]
    except (KeyError, IndexError, TypeError):
        return None
    status = (comp.get("status") or ev.get("status") or {}).get("type", {})
    done = bool(status.get("completed"))
    season_type = (ev.get("season") or {}).get("type")

    def score(c):
        v = c.get("score")
        if isinstance(v, dict):
            v = v.get("value", v.get("displayValue"))
        return to_float(v)

    out = {
        "id": str(ev.get("id")), "d": ev.get("date"), "st": season_type,
        "h": h["team"].get("displayName"), "a": a["team"].get("displayName"),
        "ha": h["team"].get("abbreviation"), "aa": a["team"].get("abbreviation"),
        "hs": score(h) if done else None, "as": score(a) if done else None, "done": done,
        "neutral": bool(comp.get("neutralSite")),
    }
    odds = parse_odds(comp.get("odds") or [], out["ha"], out["aa"])
    if odds:
        out["odds"] = odds
    return out


def _num(s):
    if s is None:
        return None
    m = re.search(r"[-+]?\d+(\.\d+)?", str(s))
    return float(m.group()) if m else None


def parse_odds(odds_list: list, home_abbr: str | None, away_abbr: str | None) -> dict:
    """Return {total, over, under, ml_home, ml_away, spread_home, spread_home_odds, spread_away_odds}
    with decimal prices. ESPN has used several shapes over time; read what is present."""
    if not odds_list:
        return {}
    o = odds_list[0] or {}
    res: dict = {}
    total = to_float(o.get("overUnder"))
    if total:
        res["total"] = total
    # moneyline - old shape
    hto, ato = o.get("homeTeamOdds") or {}, o.get("awayTeamOdds") or {}
    if hto.get("moneyLine") is not None:
        res["ml_home"] = american_to_decimal(hto.get("moneyLine"))
    if ato.get("moneyLine") is not None:
        res["ml_away"] = american_to_decimal(ato.get("moneyLine"))
    if (o.get("drawOdds") or {}).get("moneyLine") is not None:          # soccer 3-way
        res["ml_draw"] = american_to_decimal(o["drawOdds"]["moneyLine"])
    for side in ("over", "under"):
        if o.get(f"{side}Odds") is not None:
            res[side] = american_to_decimal(o[f"{side}Odds"])
    # newer shape
    ml = o.get("moneyline") or {}
    for side, key in (("home", "ml_home"), ("away", "ml_away"), ("draw", "ml_draw")):
        node = (ml.get(side) or {})
        node = node.get("close") or node.get("open") or {}
        if node.get("odds") and key not in res:
            res[key] = american_to_decimal(node["odds"])
    tot = o.get("total") or {}
    for side in ("over", "under"):
        node = (tot.get(side) or {})
        node = node.get("close") or node.get("open") or {}
        if node.get("odds"):
            res[side] = american_to_decimal(node["odds"])
        if "total" not in res and node.get("line"):
            res["total"] = _num(node["line"])
    ps = o.get("pointSpread") or {}
    hnode = ((ps.get("home") or {}).get("close") or (ps.get("home") or {}).get("open") or {})
    anode = ((ps.get("away") or {}).get("close") or (ps.get("away") or {}).get("open") or {})
    if hnode.get("line") is not None:
        res["spread_home"] = _num(hnode["line"])
        if hnode.get("odds"):
            res["spread_home_odds"] = american_to_decimal(hnode["odds"])
        if anode.get("odds"):
            res["spread_away_odds"] = american_to_decimal(anode["odds"])
    elif o.get("details") and home_abbr:
        # "BOS -5.5": favourite abbreviation and its spread
        m = re.match(r"\s*([A-Za-z.&]+)\s+([-+]?\d+(\.\d+)?)", o["details"])
        if m:
            fav, val = m.group(1), float(m.group(2))
            res["spread_home"] = val if fav == home_abbr else (-val if fav == away_abbr else None)
            if res["spread_home"] is None:
                res.pop("spread_home")
    return {k: v for k, v in res.items() if v is not None}


def _fetch_day(sport: str, league: str, d: dt.date) -> list[dict]:
    j = http_get(f"{BASE}/{sport}/{league}/scoreboard", params={"dates": d.strftime("%Y%m%d"), "limit": 300})
    return [e for e in (parse_event(ev) for ev in j.get("events", [])) if e]


def fetch_days(sport: str, league: str, days: list[dt.date], workers: int = WORKERS) -> tuple[list[dict], set[dt.date]]:
    """Fetch single days in parallel (ESPN rejects date ranges with 400).
    Returns (games, days that failed)."""
    games, failed = [], set()

    def one(d):
        try:
            return d, _fetch_day(sport, league, d)
        except Exception as e:  # noqa: BLE001
            log.info("espn %s %s: %s", league, d, e)
            return d, None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for d, got in ex.map(one, days):
            if got is None:
                failed.add(d)
            else:
                games += got
    return games, failed


def _fetch_range(sport: str, league: str, d1: dt.date, d2: dt.date) -> tuple[list[dict], int, bool]:
    """All games from d1 to d2. Returns (games, number of requests, True when every day was fetched).
    Raises when a single requested day fails (so a probe of an unknown league fails)."""
    days = [d1 + dt.timedelta(days=i) for i in range((d2 - d1).days + 1)]
    if len(days) == 1:
        return _fetch_day(sport, league, d1), 1, True
    games, failed = fetch_days(sport, league, days)
    if len(failed) == len(days):
        raise RuntimeError(f"ESPN {league}: no day could be fetched")
    return games, len(days), not failed


def _season_windows(today: dt.date, start_month: int, end_month: int) -> list[tuple[dt.date, dt.date]]:
    """Current and previous season windows (season starts in start_month, ends in end_month next year)."""
    y = today.year if today.month >= start_month else today.year - 1
    cur = (dt.date(y, start_month, 1), today + dt.timedelta(days=8))
    prev = (dt.date(y - 1, start_month, 1), dt.date(y, end_month, 30))
    return [prev, cur]


def update_games(sport: str, league: str, today: dt.date, start_month: int = 10, end_month: int = 6,
                 max_requests: int = 900, windows: list[tuple[dt.date, dt.date]] | None = None) -> dict[str, dict]:
    """Keep a disk cache of all games in the windows (default: current and previous season).
    Days already complete are not fetched again; the newest missing days go first, so a long
    history fills in over a few runs when max_requests is small.
    In quick mode only the days near today are fetched."""
    path = CACHE_DIR / f"espn_{league}.json"
    cache = read_json(path, {"events": {}, "done": []})
    events: dict = cache["events"]
    done_days = set(cache["done"])
    full_windows = windows or _season_windows(today, start_month, end_month)
    use = [(today - dt.timedelta(days=2), today + dt.timedelta(days=8))] if is_quick() else full_windows
    recent_from = today - dt.timedelta(days=2)
    wanted = set()
    for w0, w1 in use:
        d = w0
        while d <= w1:
            if d >= recent_from or d.isoformat() not in done_days:
                wanted.add(d)
            d += dt.timedelta(days=1)
    todo = sorted(wanted, reverse=True)[:max_requests]
    games, failed = fetch_days(sport, league, todo)
    for g in games:
        events[g["id"]] = g
    by_day: dict[str, list] = {}
    for g in games:
        by_day.setdefault((g["d"] or "")[:10], []).append(g)
    for d in todo:
        if d in failed or d >= recent_from:
            continue
        if all(g["done"] for g in by_day.get(d.isoformat(), [])):
            done_days.add(d.isoformat())
    oldest = min(w[0] for w in full_windows).isoformat()
    events = {k: v for k, v in events.items() if (v.get("d") or "")[:10] >= oldest}
    done_days = {x for x in done_days if x >= oldest}
    write_json(path, {"events": events, "done": sorted(done_days)})
    log.info("espn %s: %d games cached, %d days fetched (%d failed), %d days still missing", league, len(events),
             len(todo), len(failed), max(0, len(wanted) - len(todo)))
    return events


def summary(sport: str, league: str, event_id: str) -> dict:
    return http_get(f"{BASE}/{sport}/{league}/summary", params={"event": event_id})


def parse_box_players(summ: dict) -> list[dict]:
    """Player lines from an ESPN summary box score. Returns [{team, id, name, pos, stats{LABEL: value}}]."""
    out = []
    for team_block in (summ.get("boxscore") or {}).get("players", []) or []:
        team = (team_block.get("team") or {}).get("displayName")
        for grp in team_block.get("statistics", []) or []:
            labels = grp.get("labels") or grp.get("names") or []
            for ath in grp.get("athletes", []) or []:
                if ath.get("didNotPlay"):
                    continue
                stats = ath.get("stats") or []
                if not stats or len(stats) != len(labels):
                    continue
                a = ath.get("athlete") or {}
                out.append({
                    "team": team, "id": str(a.get("id")), "name": a.get("displayName"),
                    "pos": (a.get("position") or {}).get("abbreviation"),
                    "stats": dict(zip([str(x).upper() for x in labels], stats)),
                })
    return out
