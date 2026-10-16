"""Price history for the competitions without free historical odds: NHL, NBA, EuroLeague, UEFA cups.

Two snapshots per match, prices without margin:
* "daily": the first price seen (once a day The Odds API is read; ESPN prices for NHL / NBA),
* "close": the first price seen in the last CLOSE_H hours before kickoff (The Odds API is read again
  close to kickoff, see odds_api.fetch).
Source: Pinnacle when available, else the average of the books (The Odds API), else ESPN (one book).
Files: data/price_history/<sport>-<YYYY-MM>.json = {match id: {home, away, league, kickoff, daily, close}}.
The validation backtest (validate.py) uses the "close" price once a competition has MIN_GAMES of them.
"""
from __future__ import annotations

import datetime as dt
import logging

from . import market
from .util import ROOT, iso, parse_iso, read_json, write_json

log = logging.getLogger("pipeline.price_history")

DIR = ROOT / "data" / "price_history"
CLOSE_H = 3.0
MIN_GAMES = 150
CUPS = ("UCL", "UEL", "UECL")


def tracked(m: dict) -> bool:
    return m["sport"] in ("nhl", "nba", "euroleague") or (m["sport"] == "football" and m.get("league") in CUPS)


def snapshot(m: dict) -> dict | None:
    """No-vig prices of the main markets and the raw prices, from the best source of the match."""
    o = m.get("odds_raw") or {}
    if m["sport"] == "football":
        ref = market.reference_full(m)
        if not ref:
            return None
        src, p, n_books = ref
        raw = o.get("pin") if src == "pinnacle" else {k: o.get(k) for k in ("1", "X", "2", "over2.5", "under2.5")}
        return {"src": src, "n_books": n_books, "p": {k: round(v, 4) for k, v in p.items()},
                "prices": {k: v for k, v in (raw or {}).items() if v}}
    ml = market.no_vig({"1": o.get("ml_home"), "2": o.get("ml_away")}, ("1", "2"))
    if not ml:
        return None
    out = {"src": o.get("ref_src", "espn"), "n_books": o.get("n_books", 1 if o.get("ref_src", "espn") == "espn" else None),
           "p": {"1": round(ml["1"], 4), "2": round(ml["2"], 4)},
           "prices": {"1": o["ml_home"], "2": o["ml_away"]}}
    ou = market.no_vig({"o": o.get("over"), "u": o.get("under")}, ("o", "u")) if o.get("total") else None
    if ou:
        out["line"] = o["total"]
        out["p"]["over"] = round(ou["o"], 4)
        out["prices"].update(over=o["over"], under=o["under"])
    return out


def _file(sport: str, kickoff: str):
    return DIR / f"{sport}-{kickoff[:7]}.json"


def record(matches: list[dict], now: dt.datetime) -> int:
    """Add the snapshots that are missing. Returns the number of new snapshots."""
    files: dict = {}
    n = 0
    for m in matches:
        if not tracked(m):
            continue
        t = parse_iso(m["kickoff"])
        if t <= now:
            continue
        tag = "close" if (t - now).total_seconds() <= CLOSE_H * 3600 else "daily"
        f = _file(m["sport"], m["kickoff"])
        data = files.setdefault(f, read_json(f, {}) or {})
        row = data.setdefault(m["id"], {"home": m["home"], "away": m["away"], "league": m.get("league"),
                                        "kickoff": m["kickoff"]})
        if tag in row:
            continue
        snap = snapshot(m)
        if not snap:
            continue
        row[tag] = {"t": iso(now), **snap}
        n += 1
    for f, data in files.items():
        write_json(f, data)
    if n:
        log.info("price history: %d new snapshots", n)
    return n


def load(sport: str, league: str | None = None) -> dict[str, dict]:
    """{match id: best snapshot (close, else daily)} of a sport (and league for the UEFA cups)."""
    out = {}
    for f in sorted(DIR.glob(f"{sport}-*.json")):
        for mid, row in (read_json(f, {}) or {}).items():
            if league and row.get("league") != league:
                continue
            snap = row.get("close") or row.get("daily")
            if snap:
                out[mid] = snap
    return out
