"""Optional: bookmaker odds from The Odds API (free plan: 500 credits / month).

Set the repository secret ODDS_API_KEY to enable. Each sport costs 2 credits per fetch
(markets h2h + totals, one region). Odds are fetched once per day per sport, so the
default list uses about 12 credits per day (about 360 per month).
"""
from __future__ import annotations

import datetime as dt
import logging
import os

from . import models
from .util import CACHE_DIR, http_get, iso, match_name, name_variant, now_utc, parse_iso, read_json, write_json

log = logging.getLogger("pipeline.odds_api")

SPORT_MAP = {
    "icehockey_nhl": "nhl", "basketball_nba": "nba", "basketball_euroleague": "euroleague",
    "soccer_epl": "E0", "soccer_spain_la_liga": "SP1", "soccer_germany_bundesliga": "D1",
    "soccer_italy_serie_a": "I1", "soccer_france_ligue_one": "F1",
    "soccer_uefa_champs_league": "UCL", "soccer_uefa_europa_league": "UEL",
    "soccer_uefa_europa_conference_league": "UECL",
}
# UEFA cups first: football-data.co.uk has no Pinnacle prices for them
DEFAULT_SPORTS = ("soccer_uefa_champs_league,soccer_uefa_europa_league,soccer_uefa_europa_conference_league,"
                  "icehockey_nhl,basketball_nba,basketball_euroleague")


def _avg(prices: list[float]) -> float | None:
    prices = [p for p in prices if p and p > 1]
    return round(sum(prices) / len(prices), 3) if prices else None


DAY_AHEAD_H = 36      # no fetch when the sport has no match in the next 36 hours
CLOSE_H = 2.5         # a second fetch when a match starts within 2.5 hours (closing prices)


def need_fetch(hit: dict | None, kickoffs: list[dt.datetime], now: dt.datetime) -> str | None:
    """"daily" (first fetch of the UTC day), "close" (a match starts soon and the last fetch is older than
    the start of its closing window) or None. No fetch at all when no match is near (saves credits)."""
    soon = [t for t in kickoffs if now < t <= now + dt.timedelta(hours=DAY_AHEAD_H)]
    if not soon:
        return None
    if not hit or hit.get("day") != now.date().isoformat():
        return "daily"
    last = parse_iso(hit["t"]) if hit.get("t") else None
    closing = [t for t in soon if t - now <= dt.timedelta(hours=CLOSE_H)]
    if closing and (last is None or last < min(closing) - dt.timedelta(hours=CLOSE_H)):
        return "close"
    return None


def fetch(key: str, sport: str, kickoffs: list[dt.datetime] | None = None) -> list[dict]:
    """Once per UTC day, and once more close to kickoff; the cache is used otherwise.
    Budget (free plan, 500 credits / month): 2 credits per fetch, only on days with matches."""
    path = CACHE_DIR / "odds_api.json"
    cache = read_json(path, {})
    hit = cache.get(sport)
    now = now_utc()
    why = need_fetch(hit, kickoffs or [], now) if kickoffs is not None else (
        None if hit and hit.get("day") == now.date().isoformat() else "daily")
    if not why:
        return hit["events"] if hit else []
    events = http_get(f"https://api.the-odds-api.com/v4/sports/{sport}/odds",
                    params={"apiKey": key, "regions": "eu", "markets": "h2h,totals", "oddsFormat": "decimal"})
    cache[sport] = {"day": now.date().isoformat(), "t": iso(now), "why": why, "events": events}
    write_json(path, cache)
    log.info("The Odds API %s: %s fetch, %d events", sport, why, len(events))
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
            events = fetch(key, sport, [parse_iso(m["kickoff"]) for m in pool])
        except Exception as e:  # noqa: BLE001
            errors.append(f"The Odds API {sport}: {e}")
            continue
        for ev in events:
            m = find_match(ev, pool)
            if m is None:
                continue
            if m.get("mismatch"):
                continue
            h2h: dict[str, list] = {}
            totals: dict[tuple[str, float], list] = {}
            pin: dict[str, float] = {}
            pin_tot: dict[tuple[str, float], float] = {}
            b365: dict = {}
            for b in ev.get("bookmakers", []):
                is_pin = b.get("key") == "pinnacle"
                if b.get("key") == "bet365":
                    b365 = bet365_prices(b, ev)
                for mk in b.get("markets", []):
                    for o in mk.get("outcomes", []):
                        if mk["key"] == "h2h":
                            h2h.setdefault(o["name"], []).append(o.get("price"))
                            if is_pin:
                                side = "1" if o["name"] == ev["home_team"] else ("2" if o["name"] == ev["away_team"] else "X")
                                pin[side] = o.get("price")
                        elif mk["key"] == "totals" and o.get("point") is not None:
                            totals.setdefault((o["name"].lower(), float(o["point"])), []).append(o.get("price"))
                            if is_pin:
                                pin_tot[(o["name"].lower(), float(o["point"]))] = o.get("price")
                                if float(o["point"]) == 2.5:
                                    pin[f"{o['name'].lower()}2.5"] = o.get("price")
            if m["sport"] == "football" and all(k in pin for k in ("1", "X", "2")):
                m.setdefault("odds_raw", {})["pin"] = pin
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
            raw = m.setdefault("odds_raw", {})
            raw["source"] = "the-odds-api"
            raw["n_books"] = len(ev.get("bookmakers", []))
            if b365:
                raw["b365"] = b365
                models.attach_b365(m["markets"], b365)
            if m["sport"] == "football" and not raw.get("1") and prices["1"] and prices["2"] and prices["X"]:
                raw.update({k: v for k, v in prices.items()})
                for side in ("over", "under"):
                    v = _avg(totals.get((side, 2.5), []))
                    if v:
                        raw[f"{side}2.5"] = v
            if m["sport"] in ("nhl", "nba", "euroleague"):
                us_reference(raw, ev, h2h, totals, pin, pin_tot)
    return errors


KICKOFF_TOL_MIN = 15


def bet365_prices(b: dict, ev: dict) -> dict:
    """bet365 prices of one Odds API event, in the models.attach_b365 format."""
    out: dict = {"tot": []}
    for mk in b.get("markets", []):
        for o in mk.get("outcomes", []):
            if mk["key"] == "h2h":
                side = "1" if o["name"] == ev["home_team"] else ("2" if o["name"] == ev["away_team"] else "X")
                out[side] = o.get("price")
            elif mk["key"] == "totals" and o.get("point") is not None:
                out["tot"].append([o["name"].lower(), float(o["point"]), o.get("price")])
    return out


def find_match(ev: dict, pool: list[dict]) -> dict | None:
    """The match of an Odds API event: kickoff within KICKOFF_TOL_MIN and the same teams.
    The Odds API often writes names in another way ("Omonoia FC", "Saski Baskonia"). So when one team
    matches by name, the other one is accepted only when it is a spelling variant of our team
    (util.name_variant) and not the name of another team we know. Otherwise (e.g. "RC Deportivo"
    where we have "Alaves") the match gets a "mismatch" note: DATA MISMATCH, no price, no pick."""
    t = parse_iso(ev["commence_time"])
    near = [m for m in pool if abs((parse_iso(m["kickoff"]) - t).total_seconds()) <= KICKOFF_TOL_MIN * 60]
    for m in near:
        if match_name(ev["home_team"], [m["home"]]) and match_name(ev["away_team"], [m["away"]]):
            return m
    known = sorted({x[s] for x in pool for s in ("home", "away")})
    for m in near:
        h_ok = bool(match_name(ev["home_team"], [m["home"]]))
        a_ok = bool(match_name(ev["away_team"], [m["away"]]))
        if h_ok == a_ok:
            continue
        other_name, ours = (ev["away_team"], m["away"]) if h_ok else (ev["home_team"], m["home"])
        hit = match_name(other_name, known)
        # accepted only as a spelling variant of OUR team; an unknown or other club is a DATA MISMATCH
        # (16 Oct 2026: "RC Deportivo – Levante" must never be priced as "Alaves – Levante")
        if (hit and hit != ours) or (not hit and not name_variant(other_name, ours)):
            m["mismatch"] = f"The Odds API ob tem času: {ev['home_team']} – {ev['away_team']}"
        return m
    return None


def us_reference(raw: dict, ev: dict, h2h: dict, totals: dict, pin: dict, pin_tot: dict):
    """Moneyline and main total for the NHL / NBA / EuroLeague market guard.
    Pinnacle when it is listed, else the average of all books."""
    use_pin = "1" in pin and "2" in pin
    raw["ml_home"] = pin["1"] if use_pin else _avg(h2h.get(ev["home_team"], []))
    raw["ml_away"] = pin["2"] if use_pin else _avg(h2h.get(ev["away_team"], []))
    # main total line = the line most books offer
    lines: dict[float, int] = {}
    for (side, pt), prices in totals.items():
        if side == "over":
            lines[pt] = len(prices)
    if lines:
        line = max(lines, key=lambda pt: (lines[pt], -abs(pt % 1 - 0.5)))
        po, pu = pin_tot.get(("over", line)), pin_tot.get(("under", line))
        raw["total"] = line
        raw["over"] = po if po and pu else _avg(totals.get(("over", line), []))
        raw["under"] = pu if po and pu else _avg(totals.get(("under", line), []))
    raw["ref_src"] = "pinnacle" if use_pin else "avg"
