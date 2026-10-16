"""Rest days and back-to-backs (NHL, NBA, EuroLeague), measured from the season data.

A team plays on "short rest" when its previous game was SHORT_REST[sport] days before or less
(NHL / NBA: the day before = back-to-back; EuroLeague: double-game weeks, 2 days or less).

The effect is not guessed. From the finished games we compare what short-rest teams scored and
conceded with what the fitted team model expected:
* NHL (Poisson goals): factor = (actual + K) / (expected + K), K = SHRINK_GOALS,
* basketball (points):  delta = sum(actual - expected) / (n + K), K = SHRINK_GAMES.
Only when at least MIN_GAMES short-rest games exist. The adjustment and its sample size are
written to m["context"], so the export shows them.
"""
from __future__ import annotations

import datetime as dt
import logging

from .util import parse_iso

log = logging.getLogger("pipeline.rest")

SHORT_REST = {"nhl": 1, "nba": 1, "euroleague": 2}
MIN_GAMES = 60
SHRINK_GOALS = 150.0
SHRINK_GAMES = 60.0


def _last_game(dates: dict[str, list[dt.date]], team: str, day: dt.date) -> dt.date | None:
    prev = [d for d in dates.get(team, []) if d < day]
    return max(prev) if prev else None


def rest_days(dates: dict[str, list[dt.date]], team: str, day: dt.date) -> int | None:
    last = _last_game(dates, team, day)
    return (day - last).days if last else None


def team_dates(hist: list[dict]) -> dict[str, list[dt.date]]:
    out: dict[str, list[dt.date]] = {}
    for g in hist:
        for t in (g["home"], g["away"]):
            out.setdefault(t, []).append(g["date"])
    return out


def estimate(sport: str, hist: list[dict], expected) -> dict | None:
    """{"for": x, "against": y, "n": n, "kind": "mult" | "add"} for a team on short rest."""
    short = SHORT_REST[sport]
    dates = team_dates(hist)
    act_f = exp_f = act_a = exp_a = 0.0
    n = 0
    for g in hist:
        try:
            eh, ea = expected(g["home"], g["away"])
        except (KeyError, TypeError):
            continue
        for side, team, ex_for, ex_ag, sc_for, sc_ag in (("home", g["home"], eh, ea, g["hc"], g["ac"]),
                                                         ("away", g["away"], ea, eh, g["ac"], g["hc"])):
            r = rest_days(dates, team, g["date"])
            if r is None or r > short:
                continue
            n += 1
            act_f += sc_for; exp_f += ex_for; act_a += sc_ag; exp_a += ex_ag
    if n < MIN_GAMES:
        return None
    if sport == "nhl":
        return {"kind": "mult", "n": n, "for": (act_f + SHRINK_GOALS) / (exp_f + SHRINK_GOALS),
                "against": (act_a + SHRINK_GOALS) / (exp_a + SHRINK_GOALS)}
    return {"kind": "add", "n": n, "for": (act_f - exp_f) / (n + SHRINK_GAMES),
            "against": (act_a - exp_a) / (n + SHRINK_GAMES)}


def describe(sport: str, side_name: str, rest: int, eff: dict) -> str:
    what = "2. tekma v 2 dneh" if rest <= 1 else f"{rest} dni počitka"
    if eff["kind"] == "mult":
        return (f"{side_name}: {what} (iz {eff['n']} tekem: goli {100 * (eff['for'] - 1):+.0f} %, "
                f"prejeti {100 * (eff['against'] - 1):+.0f} %)")
    return (f"{side_name}: {what} (iz {eff['n']} tekem: točke {eff['for']:+.1f}, prejete {eff['against']:+.1f})")


def apply(matches: list[dict], sport: str, hist: list[dict], expected, rebuild) -> int:
    """Adjust the expectations of `sport` matches for short rest. Returns adjusted matches."""
    eff = estimate(sport, hist, expected)
    if not eff:
        log.info("rest %s: not enough short-rest games", sport)
        return 0
    dates = team_dates(hist)
    for m in matches:            # games that are scheduled but not finished yet count as well
        if m["sport"] == sport:
            d = parse_iso(m["kickoff"]).date()
            for t in (m["home"], m["away"]):
                dates.setdefault(t, []).append(d)
    short = SHORT_REST[sport]
    n = 0
    for m in matches:
        if m["sport"] != sport or not m.get("exp"):
            continue
        day = parse_iso(m["kickoff"]).date()
        rh, ra = rest_days(dates, m["home"], day), rest_days(dates, m["away"], day)
        sh, sa = rh is not None and rh <= short, ra is not None and ra <= short
        if not (sh or sa):
            continue
        a, b = m["exp"]
        if eff["kind"] == "mult":
            if sh:
                a *= eff["for"]; b *= eff["against"]
            if sa:
                b *= eff["for"]; a *= eff["against"]
        else:
            if sh:
                a += eff["for"]; b += eff["against"]
            if sa:
                b += eff["for"]; a += eff["against"]
        ctx = m.setdefault("context", [])
        if sh:
            ctx.append(describe(sport, m["home"], rh, eff))
        if sa:
            ctx.append(describe(sport, m["away"], ra, eff))
        rebuild(m, a, b)
        n += 1
    return n
