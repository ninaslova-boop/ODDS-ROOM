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


def test_espn_fetches_days_in_parallel(monkeypatch):
    calls = []

    def fake_get(url, params=None, **kw):
        calls.append(params["dates"])
        if params["dates"] == "20251103":
            raise RuntimeError("500")
        return {"events": []}

    monkeypatch.setattr(espn, "http_get", fake_get)
    games, reqs, ok = espn._fetch_range("hockey", "nhl", dt.date(2025, 11, 1), dt.date(2025, 11, 30))
    assert reqs == 30 and not ok and games == []
    assert sorted(calls) == [f"202511{d:02d}" for d in range(1, 31)]          # one request per day, no ranges


def test_update_games_marks_done_days_and_limits_requests(monkeypatch, tmp_path):
    monkeypatch.setattr(espn, "CACHE_DIR", tmp_path)
    ev = lambda d, done: {"id": d, "date": f"{d[:4]}-{d[4:6]}-{d[6:]}T19:00Z", "season": {"type": 2},
                          "competitions": [{"competitors": [
                              {"homeAway": "home", "score": "1", "team": {"displayName": "A"}},
                              {"homeAway": "away", "score": "0", "team": {"displayName": "B"}}],
                              "status": {"type": {"completed": done}}}]}
    calls = []

    def fake_get(url, params=None, **kw):
        calls.append(params["dates"])
        return {"events": [ev(params["dates"], True)]}

    monkeypatch.setattr(espn, "http_get", fake_get)
    today = dt.date(2026, 10, 14)
    win = [(dt.date(2026, 9, 1), dt.date(2026, 10, 14))]
    g = espn.update_games("soccer", "x.1", today, windows=win, max_requests=10)
    assert len(calls) == 10 and min(calls) == "20261005"                  # newest days first
    calls.clear()
    espn.update_games("soccer", "x.1", today, windows=win, max_requests=1000)
    assert "20261008" not in calls and "20261013" in calls                # done days are skipped, recent ones refreshed
    assert len(g) == 10

def test_newcomer_prior():
    row = lambda h, a: {"HomeTeam": h, "AwayTeam": a}
    cur = [row("Burnley", "Charlton"), row("Stoke", "QPR")]
    prev = [row("Stoke", "QPR")]
    upper_prev = [row("Burnley", "Arsenal")]
    pr = football.newcomer_prior(cur, prev, upper_prev)
    assert pr["Burnley"] == (0.15, -0.15)
    assert pr["Charlton"] == (-0.15, 0.15)
    assert "Stoke" not in pr


# ---------------- availability / adjustments ----------------
from pipeline import adjust, availability, soccer_espn, util  # noqa: E402


def test_same_player():
    sp = availability.same_player
    assert sp("Bukayo Saka", "Saka")
    assert sp("Erling Haaland", "Erling Braut Haaland")
    assert sp("Kylian Mbappé", "Kylian Mbappe")
    assert not sp("Gabriel Jesus", "Gabriel Martinelli")
    assert availability.find_player("Saka", ["Bukayo Saka", "Declan Rice"]) == "Bukayo Saka"


def test_parse_rosters():
    def roster(side, n_start):
        return {"homeAway": side, "roster": [{"athlete": {"displayName": f"{side} P{i}"}, "starter": i < n_start,
                                              "position": {"abbreviation": "M"}} for i in range(18)]}
    out = availability.parse_rosters({"rosters": [roster("home", 11), roster("away", 11)]})
    assert len(out["home"]["xi"]) == 11 and len(out["away"]["bench"]) == 7
    assert availability.parse_rosters({"rosters": [roster("home", 0), roster("away", 0)]}) is None


def test_espn_injuries(monkeypatch):
    payload = {"injuries": [{"displayName": "Boston Celtics", "injuries": [
        {"athlete": {"displayName": "Jayson Tatum"}, "status": "Out", "shortComment": "Achilles"},
        {"athlete": {"displayName": "Derrick White"}, "status": "Day-To-Day", "shortComment": "Knee"},
        {"athlete": {"displayName": "Someone"}, "status": "Active"}]}]}
    monkeypatch.setattr(availability, "cached_get", lambda *a, **k: payload)
    inj = availability.espn_injuries("basketball", "nba")["Boston Celtics"]
    assert [(i["name"], i["status"], i["chance"]) for i in inj] == [("Jayson Tatum", "out", 0), ("Derrick White", "doubt", 75)]


def _fb_match():
    M = models.score_matrix(1.8, 1.0, 10, -0.06)
    return {"id": "fb:E0:2026-10-18:Arsenal:Fulham", "sport": "football", "league": "E0", "home": "Arsenal",
            "away": "Fulham", "kickoff": "2026-10-18T14:00:00Z", "exp": [1.8, 1.0], "odds_raw": {"1": 1.5},
            "markets": models.goal_markets(M) + [{"stat": "corners", "scope": "total", "kind": "over", "line": 9.5,
                                                  "p": 0.5, "label": "", "group": "Koti"}]}


def _fb_player(name, xg90, home=True):
    lam = [3.0, 1.0, xg90, 0.1]
    return {"name": name, "team": "Arsenal", "league": "E0", "gp": 8, "min": 700, "xg90": xg90, "xa90": 0.1, "avail": 1.0,
            "next": {"match": "fb:E0:2026-10-18:Arsenal:Fulham", "home": home, "exp_min": 80, "lam": lam},
            "props": players.football_props(*lam)}


def test_adjust_out_player_lowers_team_and_drops_props():
    m = _fb_match()
    star, other = _fb_player("Bukayo Saka", 0.6), _fb_player("Declan Rice", 0.1)
    avail = {m["id"]: {"absent": {"home": [{"name": "Saka", "status": "out", "chance": 0}], "away": []}}}
    n = adjust.apply([m], {"football": [star, other]}, avail)
    assert n == 1
    assert m["exp"][0] < 1.8 and m["exp"][1] == 1.0
    assert star["props"] is None and star["next"]["status"] == "out"
    assert other["props"] and other["next"]["status"] == "ok"
    assert any(x["stat"] == "corners" for x in m["markets"])          # other markets are kept
    assert m["adj"]["missing"]["home"] == ["Bukayo Saka"]
    home_win = next(x for x in m["markets"] if x["scope"] == "match" and x["kind"] == "1")
    assert home_win.get("odds") == 1.5                                   # odds re-attached


def test_adjust_confirmed_lineup_bench_and_start():
    m = _fb_match()
    starter, sub = _fb_player("Bukayo Saka", 0.6), _fb_player("Declan Rice", 0.1)
    lineup = {"home": {"xi": [{"name": "Bukayo Saka"}] + [{"name": f"X{i}"} for i in range(10)],
                       "bench": [{"name": "Declan Rice"}]},
              "away": {"xi": [{"name": f"Y{i}"} for i in range(11)], "bench": []}}
    adjust.apply([m], {"football": [starter, sub]}, {m["id"]: {"lineup": lineup}})
    assert starter["next"]["status"] == "start" and sub["next"]["status"] == "bench"
    assert sub["next"]["exp_min"] == adjust.SUB_MINUTES


def test_cup_priors_use_domestic_rating_and_league_strength():
    games, _, _ = simulate_league(rounds=2)
    r = models.fit_poisson(games, dt.date(2026, 6, 1), 10_000, 1.0)
    k = 0.8
    prior, mapped = soccer_espn.cup_priors(["T1", "Unknown FC"], {"E0": r}, k=k)
    assert mapped["T1"] == ("E0", "T1")
    g = soccer_espn.STRENGTH_SCALE
    assert prior["T1"][0] == pytest.approx(k * r.att["T1"] + g * soccer_espn.LEAGUE_STRENGTH["E0"] / 2)
    assert prior["Unknown FC"][0] < 0 < prior["Unknown FC"][1]


def test_cup_priors_espn_domestic_league():
    """A club from a league football-data.co.uk does not cover gets its ESPN domestic rating
    plus its own league strength, not the small-nation default."""
    games, _, _ = simulate_league(rounds=2)
    games = [{**g, "home": "N" + g["home"], "away": "N" + g["away"]} for g in games]
    nor = models.fit_poisson(games, dt.date(2026, 6, 1), 10_000, 1.0)
    best = max(nor.att, key=nor.att.get)
    prior, mapped = soccer_espn.cup_priors([best, "Nobody FC"], {}, {"NOR": nor}, k=1.0)
    assert mapped[best] == ("NOR", best)
    g = soccer_espn.STRENGTH_SCALE
    s = soccer_espn.LEAGUE_STRENGTH["NOR"] * g
    assert prior[best][0] == pytest.approx(nor.att[best] + s / 2)
    assert prior["Nobody FC"][0] == pytest.approx(g * soccer_espn.UNKNOWN_STRENGTH / 2)
    assert soccer_espn.UNKNOWN_STRENGTH < s < soccer_espn.LEAGUE_STRENGTH["E0"]


def test_league_strength_formula_matches_anchors():
    assert soccer_espn.strength_from_coef(100) == pytest.approx(0.36)
    assert soccer_espn.strength_from_coef(30.5) == pytest.approx(-0.14, abs=0.01)
    vals = [soccer_espn.strength_from_coef(c) for c in (80, 50, 30, 20, 10)]
    assert vals == sorted(vals, reverse=True)


def test_fit_poisson_neutral_games_have_no_home_term():
    games, _, _ = simulate_league(rounds=3)
    neutral = [{**g, "neutral": True} for g in games]
    r = models.fit_poisson(neutral, dt.date(2026, 6, 1), 10_000, 1.0)
    assert abs(r.home) < 1e-3          # the home parameter has no data and stays at its tiny-ridge zero


def test_live_alerts():
    m = _fb_match()
    over = next(x for x in m["markets"] if x["scope"] == "total" and x["line"] == 2.5 and x["kind"] == "over")
    pk = {"match": m["id"], "status": "open", "stat": "goals", "scope": "total", "kind": "over", "line": 2.5,
          "p": over["p"] + 0.10, "odds": 1.0 / over["p"] + 0.05}
    picks.live_alerts([pk], [m])
    assert pk["alert"]["level"] in ("yellow", "red") and pk["p_now"] == over["p"]


def test_cached_get_quick_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(util, "CACHE_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(util, "http_get", lambda url, **k: calls.append(url) or {"v": len(calls)})
    assert util.cached_get("https://x.test/a") == {"v": 1}
    util.RUN["mode"] = "quick"
    try:
        assert util.cached_get("https://x.test/a") == {"v": 1} and len(calls) == 1
        assert util.cached_get("https://x.test/a", always_fresh=True) == {"v": 2}
    finally:
        util.RUN["mode"] = "full"

    def boom(url, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(util, "http_get", boom)
    assert util.cached_get("https://x.test/a") == {"v": 2}                # falls back to the last good copy


def test_lineup_with_unmatched_names_is_ignored():
    m = _fb_match()
    a, b = _fb_player("Bukayo Saka", 0.6), _fb_player("Declan Rice", 0.1)
    lineup = {"home": {"xi": [{"name": f"Other {i}"} for i in range(11)], "bench": []},
              "away": {"xi": [{"name": f"Y{i}"} for i in range(11)], "bench": []}}
    assert adjust.apply([m], {"football": [a, b]}, {m["id"]: {"lineup": lineup}}) == 0
    assert a["props"] and a["next"]["status"] == "ok"


def test_espn_fixture_fallback(monkeypatch):
    events = [
        {"id": "1", "d": "2026-10-18T14:00Z", "h": "Manchester United", "a": "Brighton & Hove Albion", "done": False,
         "odds": {"ml_home": 2.1, "ml_draw": 3.4, "ml_away": 3.3, "total": 2.5, "over": 1.8, "under": 2.0}},
        {"id": "2", "d": "2026-10-18T16:30Z", "h": "Arsenal", "a": "Fulham", "done": False},
        {"id": "3", "d": "2026-10-18T16:30Z", "h": "Unknown Town", "a": "Fulham", "done": False},
    ]
    monkeypatch.setattr(football.espn, "_fetch_range", lambda *a, **k: (events, 1, True))
    rows, unmatched = football.espn_fixtures("E0", ["Man United", "Brighton", "Arsenal", "Fulham"],
                                             dt.date(2026, 10, 14), 7)
    assert [(r["HomeTeam"], r["AwayTeam"]) for r in rows] == [("Man United", "Brighton"), ("Arsenal", "Fulham")]
    assert unmatched == ["Unknown Town - Fulham"]
    assert football.odds_from_row(rows[0])["1"] == 2.1 and football.odds_from_row(rows[0])["over2.5"] == 1.8
    csv_row = {"Div": "E0", "HomeTeam": "Arsenal", "AwayTeam": "Fulham", "_date": dt.date(2026, 10, 18), "AvgH": "1.4"}
    merged = football.merge_fixtures([csv_row], rows)
    assert len(merged) == 2 and merged[0]["_espn"]["id"] == "2" and merged[0]["AvgH"] == "1.4"


# ---------------- market guard and blend ----------------
from pipeline import market  # noqa: E402


def test_no_vig():
    p = market.no_vig({"1": 2.0, "X": 3.5, "2": 4.0}, ("1", "X", "2"))
    assert sum(p.values()) == pytest.approx(1)
    assert p["1"] == pytest.approx((1 / 2.0) / (1 / 2.0 + 1 / 3.5 + 1 / 4.0))
    assert market.no_vig({"1": 2.0, "X": None, "2": 4.0}, ("1", "X", "2")) is None


def test_fit_lambdas_round_trip():
    M = models.score_matrix(1.9, 0.8, 10, -0.06)
    target = market.model_probs(M)
    lh, la = market.fit_lambdas(1.2, 1.2, {k: target[k] for k in ("1", "2", "over2.5")}, -0.06)
    assert lh == pytest.approx(1.9, abs=0.02) and la == pytest.approx(0.8, abs=0.02)


def _cup_match(lh, la, pin):
    M = models.score_matrix(lh, la, 10, -0.06)
    return {"id": "fbe:UCL:1", "sport": "football", "league": "UCL", "home": "Bodo/Glimt", "away": "Atalanta",
            "kickoff": "2026-10-21T16:45:00Z", "exp": [lh, la], "odds_raw": {"pin": pin, "ml_home": 3.2},
            "markets": models.goal_markets(M)}


def test_market_guard_flags_and_blends_bodo_case():
    # model about 50 % for the home win, Pinnacle about 31 % (example from 14 Oct 2026)
    m = _cup_match(2.0, 1.0, {"1": 3.10, "X": 3.75, "2": 2.20, "over2.5": 1.80, "under2.5": 2.05})
    raw = market.model_probs(models.score_matrix(2.0, 1.0, 10, -0.06))
    st = market.apply([m], adjust._rebuild_football)
    assert st == {"checked": 1, "flagged": 1, "blended": 1}
    ref = m["market_ref"]
    assert ref["src"] == "pinnacle" and ref["gap_1x2"] > market.GUARD_PP
    home = next(x for x in m["markets"] if x["scope"] == "match" and x["kind"] == "1")
    expected = market.W_MODEL * raw["1"] + (1 - market.W_MODEL) * ref["p"]["1"]
    assert home["p"] == pytest.approx(expected, abs=0.01)          # blended
    assert home["unreliable"] is True
    assert all(x.get("unreliable") for x in m["markets"] if x["scope"] in ("match", "dnb", "spread"))
    assert not any(x.get("unreliable") for x in m["markets"] if x["scope"] == "btts")


def test_unreliable_market_is_not_logged_as_pick():
    now = dt.datetime.now(dt.timezone.utc)
    m = _cup_match(2.0, 1.0, {"1": 3.10, "X": 3.75, "2": 2.20})
    m["kickoff"] = (now + dt.timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    market.apply([m], adjust._rebuild_football)
    for x in m["markets"]:
        if x["scope"] == "match" and x["kind"] == "1":
            x["odds"] = 3.2                                       # would look like a big edge
    assert not any(c["scope"] == "match" for c in picks.candidates(m))


def test_market_small_gap_not_flagged():
    M = models.score_matrix(1.5, 1.1, 10, -0.06)
    p = market.model_probs(M)
    pin = {"1": 0.97 / p["1"], "X": 0.97 / p["X"], "2": 0.97 / p["2"]}
    m = _cup_match(1.5, 1.1, pin)
    st = market.apply([m], adjust._rebuild_football)
    assert st["flagged"] == 0 and not any(x.get("unreliable") for x in m["markets"])
    assert m["market_ref"]["gap_1x2"] < 0.01


def test_cup_priors_club_country_without_espn_league():
    prior, mapped = soccer_espn.cup_priors(["Slovan Bratislava", "Some Village FC"], {}, {})
    g = soccer_espn.STRENGTH_SCALE
    s = soccer_espn.LEAGUE_STRENGTH["SVK"] * g
    assert prior["Slovan Bratislava"] == pytest.approx((s / 2, -s / 2))
    assert prior["Some Village FC"][0] == pytest.approx(g * soccer_espn.UNKNOWN_STRENGTH / 2)
    assert "Slovan Bratislava" not in mapped          # no domestic rating, only the league strength


def test_nhl_guard_against_espn_moneyline():
    M = models.score_matrix(3.6, 2.4, 12)
    mk = models.goal_markets(M, hockey=True, total_lines=(5.5, 6.5))
    m = {"id": "nhl:1", "sport": "nhl", "home": "WPG", "away": "MIN", "markets": mk,
         "odds_raw": {"ml_home": 1.95, "ml_away": 1.95, "total": 6.5, "over": 1.91, "under": 1.91}}
    st = market.apply([m], adjust._rebuild_football)
    home = next(x for x in m["markets"] if x["scope"] == "match" and x["kind"] == "1")
    assert home["p"] > 0.6 and m["market_ref"]["gap_1x2"] > market.GUARD_PP
    assert home.get("unreliable") and st["flagged"] == 1
    assert all(x.get("unreliable") for x in m["markets"] if x["scope"] == "spread")


def test_api_football_falls_back_to_rapidapi(monkeypatch):
    calls = []

    def fake(url, params=None, headers=None, **kw):
        calls.append(url)
        if "api-sports.io" in url:
            return {"errors": {"token": "Error/Missing application key."}, "response": []}
        return {"errors": [], "response": [{"player": {"name": "Pedri", "type": "Missing Fixture", "reason": "Knee"},
                                             "team": {"name": "Barcelona"}}]}
    monkeypatch.setenv("API_FOOTBALL_KEY", "abc")
    monkeypatch.setattr(availability, "cached_get", fake)
    out = availability.api_football_injuries([dt.date(2026, 10, 18)])
    assert out[0]["name"] == "Pedri" and out[0]["status"] == "out"
    assert any("rapidapi" in c for c in calls)


def test_api_football_key_cleanup_and_error_hides_key(monkeypatch):
    assert availability.clean_key(' "x-apisports-key: 0123abcd" ') == "0123abcd"
    assert availability.clean_key("0123abcd\n") == "0123abcd"

    def fake(url, params=None, headers=None, **kw):
        if "api-sports.io" in url:
            return {"errors": {"token": "Error/Missing application key."}, "response": []}
        raise RuntimeError("403 Client Error: Forbidden")
    monkeypatch.setenv("API_FOOTBALL_KEY", "secretkey123")
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.setattr(availability, "cached_get", fake)
    try:
        availability.api_football_injuries([dt.date(2026, 10, 18)])
        raise AssertionError("must fail")
    except RuntimeError as e:
        msg = str(e)
    assert "api-sports" in msg and "RapidAPI" in msg and "12 znakov" in msg
    assert "secretkey123" not in msg and "ISTI" not in msg
    monkeypatch.setenv("ODDS_API_KEY", "secretkey123")
    assert "ISTI kot ODDS_API_KEY" in availability.key_shape("secretkey123")


def test_odds_api_us_reference_for_guard():
    from pipeline import odds_api
    raw = {}
    ev = {"home_team": "Winnipeg Jets", "away_team": "Minnesota Wild"}
    h2h = {"Winnipeg Jets": [1.9, 1.95], "Minnesota Wild": [1.95, 1.9]}
    totals = {("over", 6.5): [1.9, 1.95, 1.92], ("under", 6.5): [1.9, 1.88, 1.92], ("over", 5.5): [1.5]}
    odds_api.us_reference(raw, ev, h2h, totals, {"1": 1.93, "2": 1.97}, {("over", 6.5): 1.94, ("under", 6.5): 1.9})
    assert raw["ml_home"] == 1.93 and raw["ref_src"] == "pinnacle"
    assert raw["total"] == 6.5 and raw["over"] == 1.94
