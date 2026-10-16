"""Data checks that run before the JSON is written.

player_fixture: every player event must belong to one of the two teams of its fixture.
A player whose team is not in the fixture (or whose fixture is not published) loses the
fixture link and the events (no guessing).
"""
from __future__ import annotations


def _teams(m: dict) -> set[str]:
    out = {m["home"], m["away"]}
    for side in ("home", "away"):
        dom = (m.get("domestic") or {}).get(side)
        if dom:
            out.add(dom[1])
    return out


def player_fixture_violations(player_out: dict[str, list], matches: list[dict]) -> list[str]:
    by_id = {m["id"]: m for m in matches}
    bad = []
    for sport, rows in player_out.items():
        for r in rows or []:
            nxt = r.get("next")
            if not nxt:
                continue
            m = by_id.get(nxt["match"])
            if m is None:
                bad.append(f"{sport}: {r['name']} ({r.get('team')}) -> {nxt['match']} ni objavljena")
            elif r.get("team") not in _teams(m) or m.get("mismatch"):
                bad.append(f"{sport}: {r['name']} ({r.get('team')}) ni v tekmi {m['home']} – {m['away']}")
    return bad


def enforce_player_fixture(player_out: dict[str, list], matches: list[dict]) -> int:
    """Remove the fixture link and props of players who fail the check. Returns the number removed."""
    by_id = {m["id"]: m for m in matches}
    n = 0
    for rows in player_out.values():
        for r in rows or []:
            nxt = r.get("next")
            if not nxt:
                continue
            m = by_id.get(nxt["match"])
            if m is None or r.get("team") not in _teams(m) or m.get("mismatch"):
                r.pop("next", None)
                r["props"] = None
                n += 1
    return n
