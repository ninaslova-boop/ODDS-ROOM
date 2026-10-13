"""Optional: bookmaker odds from The Odds API (free plan: 500 credits / month).

Set the repository secret ODDS_API_KEY to enable. Each sport costs 2 credits per fetch
(markets h2h + totals, one region). Odds are fetched once per day per sport, so the
default list uses about 6 credits per day (about 180 per month).
"""
from __future__ import annotations

import logging
import os

from .util import CACHE_DIR, http_get, match_name, now_utc, parse_iso, read_json, write_json

log = logging.getLogger("pipeline.odds_api")

SPORT_MAP = {
    "icehockey_nhl": "nhl", "basketball_nba": "nba", "basketball_euroleague": "euroleague",
    "soccer_epl": "E0", "soccer_spain_la_liga": "SP1", "soccer_germany_bundesliga": "D1",
    "soccer_italy_serie_a": "I1", "soccer_france_ligue_one": "F1",
}
DEFAULT_SPORTS = "icehockey_nhl,basketball_nba,basketball_euroleague"


def _avg(prices: list[float]) -> float | None:
    prices = [p for p in prices if p and p > 1]
    return round(sum(prices) / len(prices), 3) if prices else None


def fetch(key: str, sport: str) -> list[dict]:
    """Fetch once per UTC day (3 runs a day would use about 540 credits / month), reuse the cache otherwise."""
    path = CACHE_DIR / "odds_api.json"
    cache = read_json(path, {})
    today = now_utc().date().isoformat()
    hit = cache.get(sport)
    if hit and hit.get("day") == today:
        return hit["events"]
    events = http_get(f"https://api.the-odds-api.com/v4/sports/{sport}/odds",
                    params={"apiKey": key, "regions": "eu", "markets": "h2h,totals", "oddsFormat": "decimal"})
    cache[sport] = {"day": today, "events": events}
    write_json(path, cache)
    return events


def apply(matches: list[dict]) -> list[str]:
    key = os.environ.get("ODDS_API_KEY", "").strip()
    if not key:
        return []
    errors = []
    sports = [s.strip() for s in os.environ.get("ODDS_API_SPORTS", DEFAULT_SPORTS).split(",") if s.strip()]
    for sport in sports:
        target = SPORT_MAP.get(sport)
        pool = [m for m in matches if m["sport"] == target or m["league"] == target]
        if not pool:
            continue
        try:
            events = fetch(key, sport)
        except Exception as e:  # noqa: BLE001
            errors.append(f"The Odds API {sport}: {e}")
            continue
        for ev in events:
            t = parse_iso(ev["commence_time"])
            near = [m for m in pool if abs((parse_iso(m["kickoff"]) - t).total_seconds()) < 4 * 3600]
            if not near:
                continue
            h = match_name(ev["home_team"], [m["home"] for m in near])
            if not h:
                continue
            m = next(x for x in near if x["home"] == h)
            h2h: dict[str, list] = {}
            totals: dict[tuple[str, float], list] = {}
            for b in ev.get("bookmakers", []):
                for mk in b.get("markets", []):
                    for o in mk.get("outcomes", []):
                        if mk["key"] == "h2h":
                            h2h.setdefault(o["name"], []).append(o.get("price"))
                        elif mk["key"] == "totals" and o.get("point") is not None:
                            totals.setdefault((o["name"].lower(), float(o["point"])), []).append(o.get("price"))
            prices = {
                "1": _avg(h2h.get(ev["home_team"], [])),
                "2": _avg(h2h.get(ev["away_team"], [])),
                "X": _avg(h2h.get("Draw", [])),
            }
            for mk in m["markets"]:
                if mk["scope"] == "match" and mk["kind"] in prices and prices[mk["kind"]]:
                    if m["sport"] == "nhl" and mk["kind"] == "X":
                        continue
                    mk["odds"] = prices[mk["kind"]]
                    mk.pop("odds_assumed", None)
                elif mk["scope"] == "total" and mk["stat"] in ("goals", "points"):
                    p = _avg(totals.get((mk["kind"], float(mk["line"])), []))
                    if p:
                        mk["odds"] = p
                        mk.pop("odds_assumed", None)
            m.setdefault("odds_raw", {})["source"] = "the-odds-api"
    return errors
