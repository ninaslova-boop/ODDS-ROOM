import datetime as dt
import math
import random

import numpy as np
import pytest

from pipeline import espn, football, models, picks, players
from pipeline.util import american_to_decimal, match_name, norm_name


def simulate_league(seed=1, n_teams=12, rounds=2, mu=0.25, home=0.25):
    rng = np.random.default_rng(seed)
    teams = [f"T{i}" for i in range(n_teams)]
    att = {t: a for t, a in zip(teams, rng.normal(0, 0.3, n_teams))}
    dfn = {t: d for t, d in zip(teams, rng.normal(0, 0.25, n_teams))}
    games = []
    d0 = dt.date(2026, 1, 1)
    k = 0
    for _ in range(rounds):
        for h in teams:
            for a in teams:
                if h == a:
                    continue
                lh = math.exp(mu + home + att[h] + dfn[a])
                la = math.exp(mu + att[a] + dfn[h])
                games.append({"home": h, "away": a, "hc": int(rng.poisson(lh)), "ac": int(rng.poisson(la)),
                              "date": d0 + dt.timedelta(days=k // 6)})
                k += 1
    return games, att, dfn


def test_poisson_fit_recovers_home_and_ranks():
    games, att, _ = simulate_league(rounds=4)
    r = models.fit_poisson(games, dt.date(2026, 6, 1), half_life_days=10_000, ridge=0.5)
    assert r is not None
    assert abs(r.home - 0.25) < 0.08
    true_order = sorted(att, key=att.get)
    est = [r.att[t] for t in true_order]
    assert np.corrcoef(est, sorted(att.values()))[0, 1] > 0.9


def test_score_matrix_and_markets_are_consistent():
    M = models.score_matrix(1.6, 1.1, 10, -0.06)
    assert abs(M.sum() - 1) < 1e-9
    mk = {(m["scope"], m["kind"], m["line"]): m["p"] for m in models.goal_markets(M)}
    assert abs(mk[("match", "1", None)] + mk[("match", "X", None)] + mk[("match", "2", None)] - 1) < 1e-6
    assert abs(mk[("total", "over", 2.5)] + mk[("total", "under", 2.5)] - 1) < 1e-6
    assert mk[("total", "over", 1.5)] > mk[("total", "over", 2.5)] > mk[("total", "over", 3.5)]
    assert mk[("match", "1", None)] > mk[("match", "2", None)]


def test_hockey_markets_no_draw_in_moneyline():
    M = models.score_matrix(3.2, 2.9, 12)
    mk = {(m["scope"], m["kind"], m["line"]): m["p"] for m in models.goal_markets(M, hockey=True,
                                                                                    total_lines=(5.5, 6.5))}
    assert abs(mk[("match", "1", None)] + mk[("match", "2", None)] - 1) < 1e-6


def test_basketball_markets():
    mk = models.basketball_markets(115, 110, 18, 12.5, total_line=224.5, spread_line=-4.5)
    d = {(m["scope"], m["kind"], m["line"]): m["p"] for m in mk}
    assert 0.6 < d[("match", "1", None)] < 0.75
    assert abs(d[("total", "over", 224.5)] - 0.5) < 0.05
    assert abs(d[("spread", "home", -4.5)] + d[("spread", "away", 4.5)] - 1) < 1e-6


def test_gauss_fit():
    rng = random.Random(3)
    teams = [f"B{i}" for i in range(10)]
    strength = {t: rng.gauss(0, 5) for t in teams}
    games = []
    for k in range(600):
        h, a = rng.sample(teams, 2)
        m = 3 + strength[h] - strength[a]
        t = 225
        games.append({"home": h, "away": a, "hc": (t + m) / 2 + rng.gauss(0, 9), "ac": (t - m) / 2 + rng.gauss(0, 9),
                      "date": dt.date(2026, 1, 1) + dt.timedelta(days=k // 8)})
    r = models.fit_gauss(games, dt.date(2026, 4, 1), half_life_days=10_000, ridge=0.5)
    assert abs(r.home - 3) < 1.5
    assert 8 < r.sd_margin < 18


@pytest.mark.parametrize("market,h,a,expected", [
    ({"scope": "match", "kind": "1", "line": None}, 2, 1, 1),
    ({"scope": "match", "kind": "X2", "line": None}, 1, 1, 1),
    ({"scope": "total", "kind": "over", "line": 2.5}, 2, 1, 1),
    ({"scope": "total", "kind": "under", "line": 2.5}, 2, 1, 0),
    ({"scope": "btts", "kind": "yes", "line": None}, 2, 0, 0),
    ({"scope": "spread", "kind": "home", "line": -1.5}, 3, 1, 1),
    ({"scope": "spread", "kind": "away", "line": 4.5}, 110, 106, 1),
    ({"scope": "home", "kind": "over", "line": 1.5}, 1, 3, 0),
    ({"scope": "dnb", "kind": "home", "line": None}, 1, 1, None),
])
def test_settle(market, h, a, expected):
    assert models.settle({"stat": "goals", **market}, h, a) == expected


SAMPLE_CSV = """﻿Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR,Referee,HS,AS,HST,AST,HF,AF,HC,AC,HY,AY,HR,AR,B365H,B365D,B365A,AvgH,AvgD,AvgA,Avg>2.5,Avg<2.5
E0,15/08/2026,20:00,Liverpool,Bournemouth,4,2,H,1,0,H,A Taylor,19,10,10,3,7,10,6,7,1,2,0,0,1.3,6,8.5,1.29,5.8,8.9,1.4,2.9
E0,16/08/2026,14:00,Aston Villa,Newcastle,,,,,,,,,,,,,,,,,,,,2.4,3.4,2.9,2.35,3.4,2.95,1.8,2.0
"""


def test_parse_csv_and_odds():
    rows = football.parse_csv(SAMPLE_CSV)
    assert len(rows) == 2
    assert rows[0]["_date"] == dt.date(2026, 8, 15)
    assert rows[0]["HomeTeam"] == "Liverpool"
    o = football.odds_from_row(rows[1])
    assert o["1"] == 2.35 and o["over2.5"] == 1.8
    assert football.season_code(dt.date(2026, 10, 13)) == "2627"
    assert football.prev_season_code("2627") == "2526"
    assert football.kickoff_iso(rows[0]) == "2026-08-15T19:00:00Z"


def test_espn_parse_event_and_odds():
    ev = {"id": "401", "date": "2026-10-21T23:30Z", "season": {"type": 2},
          "competitions": [{"competitors": [
              {"homeAway": "home", "score": "112", "team": {"displayName": "Boston Celtics", "abbreviation": "BOS"}},
              {"homeAway": "away", "score": "104", "team": {"displayName": "New York Knicks", "abbreviation": "NY"}}],
              "status": {"type": {"completed": True}},
              "odds": [{"details": "BOS -5.5", "overUnder": 221.5,
                        "homeTeamOdds": {"moneyLine": -220}, "awayTeamOdds": {"moneyLine": 180}}]}]}
    g = espn.parse_event(ev)
    assert g["hs"] == 112 and g["done"] and g["h"] == "Boston Celtics"
    assert g["odds"]["total"] == 221.5 and g["odds"]["spread_home"] == -5.5
    assert abs(g["odds"]["ml_home"] - 1.4545) < 0.01 and g["odds"]["ml_away"] == 2.8


def test_names_and_odds_helpers():
    assert match_name("Manchester United", ["Man United", "Man City", "Arsenal"]) == "Man United"
    assert match_name("Wolverhampton Wanderers", ["Wolves", "West Ham"]) == "Wolves"
    assert match_name("Nottingham Forest", ["Nott'm Forest", "Fulham"]) == "Nott'm Forest"
    assert match_name("Montréal Canadiens", ["Montreal Canadiens"]) == "Montreal Canadiens"
    assert norm_name("FC Bayern München") == "bayern"
    assert american_to_decimal(-110) == pytest.approx(1.909, abs=1e-3)
    assert american_to_decimal("+150") == 2.5


def test_player_helpers():
    assert players.p_at_least(1.0, 1) == pytest.approx(1 - math.exp(-1), abs=1e-6)
    assert players._lines(24.3) == [18.5, 21.5, 24.5, 27.5, 30.5]
    assert players._made("3-7") == 3


def test_picks_flow():
    now = dt.datetime.now(dt.timezone.utc)
    ko = (now + dt.timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    match = {"id": "fb:E0:x:A:B", "sport": "football", "league_name": "PL", "home": "A", "away": "B",
             "kickoff": ko, "markets": [
                 {"stat": "goals", "scope": "total", "kind": "over", "line": 2.5, "p": 0.62, "odds": 1.9,
                  "label": "Over 2.5", "group": "Skupni goli"},
                 {"stat": "goals", "scope": "match", "kind": "1", "line": None, "p": 0.45, "odds": 2.0,
                  "label": "1", "group": "Izid"}]}
    log = picks.update({}, [match], {})
    assert len(log["picks"]) == 1
    p = log["picks"][0]
    assert p["kind"] == "over" and p["status"] == "open" and p.get("totd")
    # logging again does not duplicate; settle with a 2-1 result
    log = picks.update(log, [match], {"fb:E0:x:A:B": {"goals": [2, 1]}})
    assert len(log["picks"]) == 1 and log["picks"][0]["status"] == "won"
    assert log["stats"]["all"]["profit"] == pytest.approx(0.9)


def test_espn_range_split_on_400(monkeypatch, tmp_path):
    calls = []

    def fake_get(url, params=None, **kw):
        calls.append(params["dates"])
        if "-" in params["dates"] and params["dates"].split("-")[0] != params["dates"].split("-")[1]:
            span = (dt.datetime.strptime(params["dates"][9:], "%Y%m%d") - dt.datetime.strptime(params["dates"][:8], "%Y%m%d")).days
            if span > 6:
                raise RuntimeError("400 Bad Request")
        return {"events": []}

    monkeypatch.setattr(espn, "http_get", fake_get)
    monkeypatch.setattr(espn.time, "sleep", lambda s: None)
    games, reqs, ok = espn._fetch_range("hockey", "nhl", dt.date(2025, 11, 1), dt.date(2025, 11, 30))
    assert ok and games == []
    assert calls[0] == "20251101-20251130"
    assert "20251101-20251107" in calls and "20251129-20251130" in calls


def test_newcomer_prior():
    row = lambda h, a: {"HomeTeam": h, "AwayTeam": a}
    cur = [row("Burnley", "Charlton"), row("Stoke", "QPR")]
    prev = [row("Stoke", "QPR")]
    upper_prev = [row("Burnley", "Arsenal")]
    pr = football.newcomer_prior(cur, prev, upper_prev)
    assert pr["Burnley"] == (0.15, -0.15)
    assert pr["Charlton"] == (-0.15, 0.15)
    assert "Stoke" not in pr
