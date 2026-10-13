"""Pick log: every value pick is logged before kick-off, then settled from results.

Rules (honest log, nothing is deleted):
* A pick is logged once, with the probability and price seen at that moment.
* Settlement uses the final result: won (1), lost (0), push (None) or void (no result found).
* "tracked" keeps model probabilities of a few key markets for every match,
  so we can measure calibration even without a price.
"""
from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from . import models
from .util import iso, now_utc, parse_iso

log = logging.getLogger("pipeline.picks")
LOCAL = ZoneInfo("Europe/Ljubljana")

MIN_EDGE = 0.05
MIN_P = 0.40
MAX_ODDS = 4.5
LOG_WINDOW_H = 30
VOID_AFTER_DAYS = 10
MAX_TRACKED = 8000


def edge(p: float, odds: float | None) -> float | None:
    return None if not odds else p * odds - 1


def market_id(match_id: str, m: dict) -> str:
    return f"{match_id}|{m['stat']}|{m['scope']}|{m['kind']}|{m.get('line')}"


def candidates(match: dict) -> list[dict]:
    out = []
    for m in match["markets"]:
        o = m.get("odds")
        e = edge(m["p"], o)
        if e is None or e < MIN_EDGE or m["p"] < MIN_P or o > MAX_ODDS:
            continue
        if match["sport"] == "nhl" and m["scope"] == "match" and m["kind"] == "X":
            continue
        out.append({**m, "edge": round(e, 4)})
    # prefer edge, but a solid probability counts too
    out.sort(key=lambda m: -(m["edge"] * m["p"]))
    return out


TRACK_KEYS = {
    "football": [("goals", "match", "1", None), ("goals", "match", "X", None), ("goals", "match", "2", None),
                 ("goals", "total", "over", 2.5), ("goals", "btts", "yes", None), ("goals", "total", "over", 1.5),
                 ("goals", "total", "over", 3.5)],
    "nhl": [("goals", "match", "1", None), ("goals", "total", "over", 5.5), ("goals", "total", "over", 6.5)],
    "nba": [("points", "match", "1", None)],
    "euroleague": [("points", "match", "1", None)],
}


def update(log_data: dict, matches: list[dict], results: dict) -> dict:
    """Log new picks, track probabilities and settle finished ones. Return the updated log."""
    picks: list[dict] = log_data.get("picks", [])
    tracked: list[list] = log_data.get("tracked", [])
    have = {p["id"] for p in picks}
    have_tr = {t[0] for t in tracked}
    now = now_utc()
    totd_days = {p["day"] for p in picks if p.get("totd")}
    new_today: dict[str, list[dict]] = {}

    for match in matches:
        t = parse_iso(match["kickoff"])
        if not (now < t <= now + dt.timedelta(hours=LOG_WINDOW_H)):
            continue
        day = t.astimezone(LOCAL).date().isoformat()
        base = {"match": match["id"], "sport": match["sport"], "league": match.get("league_name"),
                "home": match["home"], "away": match["away"], "kickoff": match["kickoff"], "day": day}
        for c in candidates(match)[:1]:
            pid = market_id(match["id"], c)
            if pid in have or any(p["match"] == match["id"] for p in picks):
                continue
            pick = {**base, "id": pid, "label": c["label"], "group": c["group"], "stat": c["stat"],
                    "scope": c["scope"], "kind": c["kind"], "line": c.get("line"), "p": c["p"], "odds": c["odds"],
                    "odds_assumed": bool(c.get("odds_assumed")), "edge": c["edge"], "logged": iso(now),
                    "result": None, "status": "open"}
            picks.append(pick)
            have.add(pid)
            new_today.setdefault(day, []).append(pick)
        for stat, scope, kind, line in TRACK_KEYS.get(match["sport"], []):
            for m in match["markets"]:
                if (m["stat"], m["scope"], m["kind"], m.get("line")) == (stat, scope, kind, line):
                    tid = market_id(match["id"], m)
                    if tid not in have_tr:
                        tracked.append([tid, match["sport"], m["p"], None, match["kickoff"]])
                        have_tr.add(tid)
    # Tip of the Day: the best new pick of a day without one
    for day, lst in new_today.items():
        if day in totd_days:
            continue
        best = max(lst, key=lambda p: p["edge"] * p["p"] if p["p"] >= 0.5 else -1)
        if best["p"] >= 0.5:
            best["totd"] = True

    # settle
    for p in picks:
        if p["status"] != "open":
            continue
        r = _settle_one(p["match"], p["sport"], p, results)
        if r == "wait":
            if now - parse_iso(p["kickoff"]) > dt.timedelta(days=VOID_AFTER_DAYS):
                p["status"] = "void"
            continue
        p["result"] = r
        p["status"] = "won" if r == 1 else ("lost" if r == 0 else "push")
        p["settled"] = iso(now)
    for t in tracked:
        if t[3] is not None:
            continue
        match_id, stat, scope, kind, line = t[0].split("|")
        mk = {"stat": stat, "scope": scope, "kind": kind, "line": None if line == "None" else float(line)}
        r = _settle_one(match_id, t[1], mk, results)
        if r == "wait":
            if now - parse_iso(t[4]) > dt.timedelta(days=VOID_AFTER_DAYS):
                t[3] = -1
            continue
        t[3] = -1 if r is None else r
    tracked = tracked[-MAX_TRACKED:]
    return {"picks": picks, "tracked": tracked, "stats": stats(picks, tracked), "updated": iso(now)}


def _settle_one(match_id: str, sport: str, market: dict, results: dict):
    res = results.get(match_id)
    if not res:
        return "wait"
    vals = res.get(market["stat"])
    if not vals or vals[0] is None or vals[1] is None:
        return None
    return models.settle(market, vals[0], vals[1], hockey=(sport == "nhl"))


def _summary(rows: list[dict]) -> dict:
    s = [p for p in rows if p["status"] in ("won", "lost", "push")]
    won = sum(p["status"] == "won" for p in s)
    lost = sum(p["status"] == "lost" for p in s)
    push = sum(p["status"] == "push" for p in s)
    profit = sum((p["odds"] - 1) if p["status"] == "won" else (-1 if p["status"] == "lost" else 0) for p in s)
    staked = won + lost
    decided = [p for p in s if p["status"] != "push"]
    return {
        "n": len(rows), "open": sum(p["status"] == "open" for p in rows), "won": won, "lost": lost, "push": push,
        "hit": round(won / staked, 4) if staked else None,
        "profit": round(profit, 2), "roi": round(profit / staked, 4) if staked else None,
        "avg_odds": round(sum(p["odds"] for p in decided) / len(decided), 3) if decided else None,
        "avg_p": round(sum(p["p"] for p in decided) / len(decided), 4) if decided else None,
        "brier": round(sum((p["p"] - (p["status"] == "won")) ** 2 for p in decided) / len(decided), 4)
        if decided else None,
    }


def stats(picks: list[dict], tracked: list[list]) -> dict:
    by_sport = {}
    for sp in sorted({p["sport"] for p in picks}):
        by_sport[sp] = _summary([p for p in picks if p["sport"] == sp])
    by_month = {}
    for mo in sorted({p["day"][:7] for p in picks}):
        by_month[mo] = _summary([p for p in picks if p["day"][:7] == mo])
    # calibration on tracked probabilities + picks
    pts = [(t[2], t[3]) for t in tracked if t[3] in (0, 1)]
    pts += [(p["p"], 1 if p["status"] == "won" else 0) for p in picks if p["status"] in ("won", "lost")]
    buckets = []
    for lo in range(0, 100, 10):
        b = [(p, y) for p, y in pts if lo / 100 <= p < (lo + 10) / 100 or (lo == 90 and p == 1.0)]
        if b:
            buckets.append({"lo": lo / 100, "hi": (lo + 10) / 100, "n": len(b),
                            "pred": round(sum(p for p, _ in b) / len(b), 4), "act": round(sum(y for _, y in b) / len(b), 4)})
    brier = round(sum((p - y) ** 2 for p, y in pts) / len(pts), 4) if pts else None
    return {"all": _summary(picks), "by_sport": by_sport, "by_month": by_month,
            "calibration": buckets, "tracked_n": len(pts), "tracked_brier": brier}
