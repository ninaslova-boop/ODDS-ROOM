"""The official pick record: bet365 picks entered by the user in the app.

The app saves them to data/user_picks.json in the repository (a GitHub commit made from the app with
the user's own token). This module only reads that file: it settles the picks with the results and
publishes app/data/user_picks.json with status and totals. It never writes the repository file, so
the app and the pipeline cannot overwrite each other.
The automatic picks (data/picks_log.json) are the separate "model shadow log".
"""
from __future__ import annotations

from . import models
from .picks import _summary
from .util import ROOT, read_json

FILE = ROOT / "data" / "user_picks.json"


def settle(picks: list[dict], results: dict) -> list[dict]:
    out = []
    for p in picks:
        q = {**p, "status": "open"}
        q.pop("score", None)
        res = results.get(p.get("match"))
        vals = res and (res.get(p.get("stat")) or res.get("goals") or res.get("points"))
        if vals and vals[0] is not None:
            r = models.settle({"scope": p["scope"], "kind": p["kind"], "line": p.get("line")}, vals[0], vals[1],
                              hockey=(p.get("sport") == "nhl"))
            q["status"] = "won" if r == 1 else ("lost" if r == 0 else "push")
            q["score"] = f"{vals[0]:g}-{vals[1]:g}"
        out.append(q)
    return out


def publish(results: dict) -> dict:
    data = read_json(FILE, {}) or {}
    picks = settle(data.get("picks", []), results)
    rows = [{**p, "p": p.get("p_final", 0)} for p in picks]
    by_sport = {}
    for sp in sorted({p.get("sport") for p in rows}):
        by_sport[sp] = _summary([p for p in rows if p.get("sport") == sp])
    return {"picks": picks, "updated": data.get("updated"), "stats": {"all": _summary(rows), "by_sport": by_sport}}
