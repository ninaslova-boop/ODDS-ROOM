"""Pick log: every pick is logged before kick-off, then settled from results.

Pick rule (all sports): a pick is valid when p_final x bet365 odds > 1.00.
* p_final = the probability after the shrink to the market (market.py); p_model is kept too.
* Only real bet365 prices are used (football-data.co.uk B365 columns, or bet365 in The Odds API).
  An average of books is never stored as the pick price. Without a bet365 price there is no
  automatic pick; the user enters bet365 odds in the app (bet365 list).
* "strong" = p_final x odds >= STRONG_EV. There are no other labels or tiers.
* A match that is unreliable (model-market gap on a main market) or a DATA MISMATCH never
  creates a pick. The check runs again at logging time.

Honest log: a pick is logged once, with the probability and price seen at that moment, and
nothing is deleted. "tracked" keeps model probabilities of a few key markets for every match,
so we can measure calibration even without a price.
"""
from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from . import market as market_mod
from . import models
from .util import iso, now_utc, parse_iso

log = logging.getLogger("pipeline.picks")
LOCAL = ZoneInfo("Europe/Ljubljana")

STRONG_EV = 1.10
LOG_WINDOW_H = 30
VOID_AFTER_DAYS = 10
MAX_TRACKED = 8000


def ev(p: float, odds: float | None) -> float | None:
    return None if not odds else p * odds


def edge(p: float, odds: float | None) -> float | None:
    return None if not odds else p * odds - 1


def market_id(match_id: str, m: dict) -> str:
    return f"{match_id}|{m['stat']}|{m['scope']}|{m['kind']}|{m.get('line')}"


def is_pick(p_final: float, odds: float | None) -> bool:
    return bool(odds) and p_final * odds > 1.0


def candidates(match: dict) -> list[dict]:
    """Markets of a match that pass the pick rule with a bet365 price, best first."""
    if market_mod.is_unreliable(match):
        return []
    out = []
    for m in match["markets"]:
        o = m.get("b365")
        if not is_pick(m["p"], o) or m.get("unreliable"):
            continue
        if match["sport"] == "nhl" and m["scope"] == "match" and m["kind"] == "X":
            continue
        e = m["p"] * o
        out.append({**m, "odds": o, "ev": round(e, 4), "edge": round(e - 1, 4), "strong": e >= STRONG_EV})
    out.sort(key=lambda m: -m["ev"])
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
            # check again right before logging (the match may have been flagged in this run)
            if market_mod.is_unreliable(match) or not is_pick(c["p"], c["odds"]):
                continue
            pick = {**base, "id": pid, "label": c["label"], "group": c["group"], "stat": c["stat"],
                    "scope": c["scope"], "kind": c["kind"], "line": c.get("line"), "p": c["p"],
                    "p_model": c.get("p_model", c["p"]), "w": (match.get("blend") or {}).get("w", 1.0),
                    "odds": c["odds"], "odds_src": "bet365", "ev": c["ev"], "edge": c["edge"],
                    "strong": c["strong"], "logged": iso(now), "result": None, "status": "open"}
            picks.append(pick)
            have.add(pid)
        for stat, scope, kind, line in TRACK_KEYS.get(match["sport"], []):
            for m in match["markets"]:
                if (m["stat"], m["scope"], m["kind"], m.get("line")) == (stat, scope, kind, line):
                    tid = market_id(match["id"], m)
                    if tid not in have_tr:
                        tracked.append([tid, match["sport"], m["p"], None, match["kickoff"]])
                        have_tr.add(tid)
    repair(picks)
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


def repair(picks: list[dict]):
    """Picks that were logged with a wrong price because of a data bug are voided, with the reason.
    Nothing is deleted. 16 Oct 2026: bet365 goal prices (over / under 2.5) were attached to the
    cards and corners totals 2.5."""
    for p in picks:
        if p.get("odds_src") == "bet365" and p.get("stat") not in ("goals", "points") and p["status"] != "void":
            p["status"] = "void"
            p["result"] = None
            p["void_reason"] = "napaka podatkov: kvota bet365 za gole je bila pripisana trgu " + str(p.get("stat"))


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
            "calibration": buckets, "tracked_n": len(pts), "tracked_brier": brier,
            "picks_calibration": _buckets([(p["p"], 1 if p["status"] == "won" else 0) for p in picks
                                           if p["status"] in ("won", "lost")]),
            "by_w": by_weight(picks)}


def _buckets(pts: list[tuple[float, int]]) -> list[dict]:
    out = []
    for lo in range(0, 100, 10):
        b = [(p, y) for p, y in pts if lo / 100 <= p < (lo + 10) / 100 or (lo == 90 and p == 1.0)]
        if b:
            out.append({"lo": lo / 100, "hi": (lo + 10) / 100, "n": len(b),
                        "pred": round(sum(p for p, _ in b) / len(b), 4), "act": round(sum(y for _, y in b) / len(b), 4)})
    return out


def w_label(p: dict) -> str:
    """Picks logged before the shrink rule have no "w": they are the old rule (edge 5-25 %, average odds)."""
    if "w" not in p:
        return "staro pravilo"
    return f"w={p['w']:g}"


def by_weight(picks: list[dict]) -> dict:
    """Logged picks only: expected hit rate (mean p_final) against the actual one, and profit,
    by sport and by w. Use it to tune w on real data."""
    out: dict[str, dict] = {}
    for p in picks:
        out.setdefault(p["sport"], {}).setdefault(w_label(p), []).append(p)
    return {sp: {w: _summary(lst) for w, lst in ws.items()} for sp, ws in out.items()}


def live_alerts(picks_list: list[dict], matches: list[dict]):
    """Lineup Alert for open picks: compare the logged probability with the current one.
    yellow = still value but the model is less sure, red = no value any more (pick pulled)."""
    by_id = {m["id"]: m for m in matches}
    for p in picks_list:
        p.pop("alert", None)
        if p.get("status") != "open":
            continue
        m = by_id.get(p["match"])
        if not m:
            continue
        if market_mod.is_unreliable(m):
            p["alert"] = {"level": "red", "text": "Tekma je zdaj nezanesljiva ali DATA MISMATCH."}
            continue
        cur = next((k for k in m["markets"] if (k["stat"], k["scope"], k["kind"], k.get("line")) ==
                    (p["stat"], p["scope"], p["kind"], p.get("line"))), None)
        if not cur:
            continue
        p["p_now"] = cur["p"]
        drop = p["p"] - cur["p"]
        why = (m.get("adj") or {}).get("missing") or {}
        names = ", ".join(why.get("home", []) + why.get("away", []))[:120]
        if cur["p"] * p["odds"] - 1 < 0:
            p["alert"] = {"level": "red", "text": f"Model zdaj {round(cur['p'] * 100)} %: ni več value. {names}".strip()}
        elif drop >= 0.03:
            p["alert"] = {"level": "yellow", "text": f"Model zdaj {round(cur['p'] * 100)} %. {names}".strip()}
