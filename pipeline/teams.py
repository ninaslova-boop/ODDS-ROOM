"""Team identity across data providers, and the fixture cross-check.

* ESPN gives every team an id. The first time an id is seen it is resolved with the strict name
  match (util.match_name: exact name or alias, no guessing) and stored. Later runs use the id.
  An id that would now resolve to another team is a conflict: the team is not resolved.
  Manual, checked mappings go into pipeline/team_ids.json and win over everything else.
* Cross-check (football leagues): a fixture from football-data.co.uk is confirmed when ESPN has
  the same two teams within KICKOFF_TOL minutes. When ESPN has one of the two teams at that time
  against another (or an unknown) team, the fixture is a DATA MISMATCH: it is not used for picks,
  value lists or player events.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from .util import CACHE_DIR, match_name, parse_iso, read_json, write_json

log = logging.getLogger("pipeline.teams")

KICKOFF_TOL = 15        # minutes
MANUAL = Path(__file__).with_name("team_ids.json")
STORE = CACHE_DIR / "team_ids.json"


class Registry:
    def __init__(self, path: Path = STORE, manual: Path = MANUAL):
        self.path = path
        self.ids: dict[str, str] = read_json(path, {}) or {}
        try:
            self.manual: dict[str, str] = json.loads(manual.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.manual = {}
        self.conflicts: list[str] = []

    def resolve(self, provider: str, league: str, team_id: str | None, name: str, candidates: list[str]) -> str | None:
        key = f"{provider}:{league}:{team_id}" if team_id else None
        if key and key in self.manual:
            return self.manual[key] if self.manual[key] in candidates else None
        strict = match_name(name, candidates)
        if key and key in self.ids:
            known = self.ids[key]
            if strict and strict != known:
                self.conflicts.append(f"{name} ({key}): shranjeno {known}, ime kaže na {strict}")
                return None
            return known if known in candidates else None
        if strict and key:
            # one id per team: a second id for the same team is suspicious
            other = next((k for k, v in self.ids.items() if v == strict and k.startswith(f"{provider}:{league}:")), None)
            if other and other != key:
                self.conflicts.append(f"{name} ({key}): {strict} že ima id {other}")
                return None
            self.ids[key] = strict
        return strict

    def save(self):
        write_json(self.path, self.ids)


def _minutes(a: str, b: str) -> float:
    return abs((parse_iso(a) - parse_iso(b)).total_seconds()) / 60


def cross_check(csv_rows: list[dict], espn_games: list[dict], league_name: str, covered_days: set[dt.date]
                ) -> tuple[list[dict], list[dict]]:
    """csv_rows: fixtures with HomeTeam / AwayTeam / _kickoff. espn_games: {"d", "h", "a", "rh", "ra", "id", ...}
    where rh / ra are the resolved model names (None when unknown).
    Returns (fixtures to model, mismatches). Fixtures get "_check": "ok" | "single source"."""
    out, bad = [], []
    used = set()
    for r in csv_rows:
        H, A, t = r["HomeTeam"], r["AwayTeam"], r["_kickoff"]
        near = [g for g in espn_games if _minutes(g["d"], t) <= KICKOFF_TOL]
        same = next((g for g in near if g["rh"] == H and g["ra"] == A), None)
        if same:
            used.add(same["id"])
            out.append({**r, "_check": "ok", "_espn": r.get("_espn") or same["_espn"]})
            continue
        clash = next((g for g in near if {g["rh"], g["ra"]} & {H, A}), None)
        later = next((g for g in espn_games if g["rh"] == H and g["ra"] == A), None)
        if clash:
            used.add(clash["id"])
            bad.append({"league": league_name, "kickoff": t, "home": H, "away": A,
                        "reason": f"ESPN ob tem času: {clash['h']} – {clash['a']}"})
        elif later:
            used.add(later["id"])
            bad.append({"league": league_name, "kickoff": t, "home": H, "away": A,
                        "reason": f"ESPN začetek {later['d'][:16].replace('T', ' ')} UTC (razlika > {KICKOFF_TOL} min)"})
        else:
            out.append({**r, "_check": "single source" if parse_iso(t).date() not in covered_days else "not in ESPN"})
    # ESPN games without a football-data.co.uk row
    for g in espn_games:
        if g["id"] in used:
            continue
        if g["rh"] and g["ra"]:
            busy = [r for r in csv_rows if {r["HomeTeam"], r["AwayTeam"]} & {g["rh"], g["ra"]}
                    and _minutes(r["_kickoff"], g["d"]) < 36 * 60]
            if not busy:
                out.append({"HomeTeam": g["rh"], "AwayTeam": g["ra"], "_kickoff": g["d"], "_espn": g["_espn"],
                            "_date": parse_iso(g["d"]).date(), "_check": "ESPN only", **g.get("_odds", {})})
        else:
            bad.append({"league": league_name, "kickoff": g["d"], "home": g["rh"] or g["h"], "away": g["ra"] or g["a"],
                        "reason": "ESPN ekipa ni prepoznana: " + ", ".join(n for n, r in ((g["h"], g["rh"]), (g["a"], g["ra"])) if not r)})
    return out, bad
