"""Reproducible check of the 16 Oct 2026 problems, end to end (pipeline pieces + app.js in Node).

* Deportivo – Levante: ESPN 'RC Deportivo' must not become Alaves; the fixture is a DATA MISMATCH.
* No pick from an unreliable match (Verona – Catanzaro case: 1X2 gap > 10 pp, pick offered on another market).
* Adil Bourabaa (Le Mans, out): not in the player events of the export.
* bet365 list: each market with min odds = 1 / p_final and a result for the odds the user enters.
* Exact line in the app (JS) = the pipeline model (Python) for football, NHL and basketball.
"""
import datetime as dt
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from pipeline import adjust, checks, football, market, models, picks, players, sports, teams

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _football_match(mid, league, home, away, ko, lh, la, pin=None, b365=None):
    M = models.score_matrix(lh, la, 10, football.DC_RHO)
    odds = {}
    if pin:
        odds["pin"] = pin
    if b365:
        odds["b365"] = b365
    mk = models.goal_markets(M)
    football.attach_odds(mk, odds)
    return {"id": mid, "sport": "football", "league": league, "league_name": league, "home": home, "away": away,
            "kickoff": ko, "exp": [lh, la], "odds_raw": odds, "markets": mk, "form": {}, "stats": {}}


@pytest.fixture()
def slate(tmp_path):
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    ko = _iso(now + dt.timedelta(hours=6))
    # 1) Deportivo – Levante: football-data.co.uk says La Coruna – Levante, ESPN 'RC Deportivo' – Levante
    reg = teams.Registry(tmp_path / "ids.json", tmp_path / "none.json")
    laliga = ["Alaves", "Levante", "La Coruna", "Celta"]
    games = [{"id": "401882844", "d": ko, "h": "RC Deportivo", "a": "Levante",
              "rh": reg.resolve("espn", "esp.1", "3572", "RC Deportivo", laliga),
              "ra": reg.resolve("espn", "esp.1", "1538", "Levante", laliga), "_espn": {"slug": "esp.1", "id": "401882844"}}]
    rows = [{"Div": "SP1", "HomeTeam": "La Coruna", "AwayTeam": "Levante", "_kickoff": ko,
             "_date": dt.date.fromisoformat(ko[:10])}]
    fixtures, mismatches = teams.cross_check(rows, games, "LaLiga", {dt.date.fromisoformat(ko[:10])})
    mismatches = [{**m, "sport": "football"} for m in mismatches]
    assert fixtures == []

    # 2) Verona – Catanzaro: model 1X2 far from Pinnacle -> whole match unreliable (bet365 price on over 2.5)
    verona = _football_match("fb:I1:x:Verona:Catanzaro", "I1", "Verona", "Catanzaro", ko, 2.1, 0.8,
                             pin={"1": 2.6, "X": 3.2, "2": 2.9, "over2.5": 2.0, "under2.5": 1.85},
                             b365={"1": 2.6, "X": 3.2, "2": 2.9, "over2.5": 2.2, "under2.5": 1.7})
    # 3) a normal reliable match with bet365 prices
    ok = _football_match("fb:F1:x:Le Mans:Lens", "F1", "Le Mans", "Lens", ko, 1.3, 1.25,
                         pin={"1": 2.75, "X": 3.3, "2": 2.7, "over2.5": 1.95, "under2.5": 1.9},
                         b365={"1": 2.8, "X": 3.4, "2": 2.75, "over2.5": 2.0, "under2.5": 1.83})
    # 4) NBA match for the exact line check (178.5 priced, bet365 177.5)
    nba_mk = models.basketball_markets(90.0, 88.0, 17.0, 11.0, 178.5, None)
    nba = {"id": "nba:7", "sport": "nba", "league": "NBA", "league_name": "NBA", "home": "A", "away": "B",
           "kickoff": ko, "exp": [90.0, 88.0], "sd": [17.0, 11.0], "markets": nba_mk, "odds_raw": {}}
    M, nhl_mk = sports.nhl_markets(3.3, 2.9, {})
    nhl = {"id": "nhl:8", "sport": "nhl", "league": "NHL", "league_name": "NHL", "home": "C", "away": "D",
           "kickoff": ko, "exp": [3.3, 2.9], "markets": nhl_mk, "odds_raw": {}}
    matches = [verona, ok, nba, nhl]
    market.apply(matches, {"football": adjust._rebuild_football})

    # players: Bourabaa (Le Mans) is out; an Alaves player linked to the Deportivo fixture id
    bourabaa = {"name": "Adil Bourabaa", "team": "Le Mans", "league": "F1", "gp": 8, "min": 600, "avail": 1.0,
                "xg90": 0.3, "xa90": 0.1, "next": {"match": ok["id"], "home": True, "opp": "Lens", "kickoff": ko,
                                                    "exp_min": 75, "lam": [2.0, 0.7, 0.25, 0.08]},
                "props": players.football_props(2.0, 0.7, 0.25, 0.08)}
    other = {"name": "Theo Other", "team": "Le Mans", "league": "F1", "gp": 8, "min": 600, "avail": 1.0,
             "xg90": 0.2, "xa90": 0.1, "next": {"match": ok["id"], "home": True, "opp": "Lens", "kickoff": ko,
                                                 "exp_min": 75, "lam": [2.2, 0.8, 0.2, 0.08]},
             "props": players.football_props(2.2, 0.8, 0.2, 0.08)}
    mariano = {"name": "Mariano", "team": "Alaves", "league": "SP1", "gp": 8, "min": 600,
               "next": {"match": "fb:SP1:x:Alaves:Levante", "home": True, "opp": "Levante", "kickoff": ko},
               "props": players.football_props(2.0, 0.9, 0.3, 0.1)}
    player_out = {"football": [bourabaa, other, mariano]}
    avail = {ok["id"]: {"absent": {"home": [{"name": "Adil Bourabaa", "status": "out", "chance": 0,
                                             "reason": "ankle", "src": "API-Football"}], "away": []}}}
    adjust.apply(matches, player_out, avail)
    checks.enforce_player_fixture(player_out, matches)
    for m in matches:
        for mk in m["markets"]:
            if mk.get("b365"):
                mk["ev"] = round(mk["p"] * mk["b365"], 4)
    log = picks.update({}, matches, {})
    return {"matches": matches, "mismatches": mismatches, "players": player_out, "picks": log,
            "meta": {"injury_sources": {"F1": ["API-Football"]}}, "validation": {}, "day": "all2",
            "lineProb": [[nba["id"], "total", "under", 177.5], [nba["id"], "total", "under", 178.5],
                         [ok["id"], "total", "over", 2.5], [ok["id"], "total", "over", 3.5], [ok["id"], "btts", "yes", None],
                         [nhl["id"], "match", "1", None], [nhl["id"], "total", "over", 6.5]],
            "rowResult": [[f"{ok['id']}|total|over", "2.10", None], [f"{nba['id']}|total|under", "1.95", "177.5"]]}


def test_pipeline_side_of_the_slate(slate):
    assert slate["mismatches"][0]["home"] == "La Coruna" and slate["mismatches"][0]["away"] == "Levante"
    # no pick from the unreliable match, even with a bet365 price that would pass the rule
    verona = slate["matches"][0]
    assert verona["unreliable"] and not any(p["match"] == verona["id"] for p in slate["picks"]["picks"])
    assert all(p["odds_src"] == "bet365" for p in slate["picks"]["picks"])
    assert checks.player_fixture_violations(slate["players"], slate["matches"]) == []


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_app_export_and_bet365_list(slate, tmp_path):
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps(slate, default=str), encoding="utf-8")
    res = subprocess.run([NODE, str(ROOT / "tests" / "js" / "harness.js"), str(fx)], capture_output=True, text=True,
                         timeout=60)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    exp = out["export"]
    # DATA MISMATCH section lists Deportivo (La Coruna) – Levante; Alaves never appears
    mm = exp.split("## DATA MISMATCH")[1].split("##")[0]
    assert "La Coruna – Levante" in mm and "RC Deportivo" in mm
    assert "Alaves" not in exp and "Mariano" not in exp
    # Bourabaa is out: not in the player events (only in the absences line of the match); his team mate is
    pl = exp.split("## Igralci")[1]
    assert "Bourabaa" not in pl and "Theo Other" in pl
    # the unreliable match is not in the bet365 list nor in the candidates
    cand = exp.split("## Za preveriti na bet365")[1].split("##")[0]
    assert "Verona" not in cand
    assert "fb:I1:x:Verona:Catanzaro" in out["bad"]
    assert not any(r["key"].startswith("fb:I1:") for r in out["b365"])
    # bet365 list: min odds = 1 / p_final for every row
    assert out["b365"] and all(abs(r["min"] - round(1 / r["p"], 2)) < 1e-9 for r in out["b365"])
    # entered odds: PICK / NO PICK by p_final x odds > 1, exact line 177.5
    ok_over, nba_under = out["rowResult"]
    assert ok_over["ok"] == (ok_over["p"] * 2.10 > 1)
    assert nba_under["p"] == pytest.approx(out["lineProb"][0], abs=1e-9)
    # no pick labels in the export
    for word in ("TOTD", "Tip of the Day", "Lockdown", "VALUE PICK", "Bold"):
        assert word not in exp
    assert "injury data: none" in exp      # LaLiga / NBA / NHL in this slate have no injury source listed


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_app_exact_line_matches_python_model(slate, tmp_path):
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps(slate, default=str), encoding="utf-8")
    out = json.loads(subprocess.run([NODE, str(ROOT / "tests" / "js" / "harness.js"), str(fx)],
                                    capture_output=True, text=True, timeout=60).stdout)
    lp = out["lineProb"]
    nba = slate["matches"][2]
    t = sum(nba["exp"])
    assert lp[0] == pytest.approx(models.norm_cdf((177.5 - t) / 17.0), abs=2e-4)     # under 177.5
    assert lp[1] == pytest.approx(models.norm_cdf((178.5 - t) / 17.0), abs=2e-4)
    assert lp[0] < lp[1]
    ok = slate["matches"][1]

    def py(m, scope, kind, line):
        return next(x["p"] for x in m["markets"] if x["scope"] == scope and x["kind"] == kind and x.get("line") == line)
    assert lp[2] == pytest.approx(py(ok, "total", "over", 2.5), abs=1e-3)
    assert lp[3] == pytest.approx(py(ok, "total", "over", 3.5), abs=1e-3)
    assert lp[4] == pytest.approx(py(ok, "btts", "yes", None), abs=1e-3)
    nhl = slate["matches"][3]
    assert lp[5] == pytest.approx(py(nhl, "match", "1", None), abs=1e-3)
    assert lp[6] == pytest.approx(py(nhl, "total", "over", 6.5), abs=1e-3)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_export_says_player_data_none_without_current_season(tmp_path):
    ko = _iso(dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=5))
    mk = models.basketball_markets(84.0, 80.0, 16.0, 11.0, 164.5, None)
    m = {"id": "euroleague:1", "sport": "euroleague", "league": "EL", "league_name": "EuroLeague",
         "home": "Real Madrid", "away": "Olympiacos", "kickoff": ko, "exp": [84.0, 80.0], "sd": [16.0, 11.0],
         "markets": mk, "odds_raw": {}}
    for ok, want in ((False, "player data: none → EuroLeague"), (True, "EuroLeague player data: EuroLeague API, sezona E2026")):
        meta = {"player_data": {"euroleague": {"ok": ok, "season": "E2026", "games": 40,
                                               "reason": "API ne vrne sezone E2026"}}}
        fx = tmp_path / f"fx{ok}.json"
        fx.write_text(json.dumps({"matches": [m], "meta": meta, "players": {"euroleague": []}}), encoding="utf-8")
        out = json.loads(subprocess.run([NODE, str(ROOT / "tests" / "js" / "harness.js"), str(fx)],
                                        capture_output=True, text=True, timeout=60).stdout)
        assert want in out["export"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_export_header_prints_w_per_sport(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_W_NHL", "0.5")
    meta = {"market_w": {sp: market.weight(sp, False) for sp in market.W}, "market_w_thin": market.W_THIN}
    assert meta["market_w"]["nhl"] == 0.5 and meta["market_w"]["football"] == 0.4 and market.weight("nba", True) == 0.6
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"matches": [], "meta": meta}), encoding="utf-8")
    out = json.loads(subprocess.run([NODE, str(ROOT / "tests" / "js" / "harness.js"), str(fx)],
                                    capture_output=True, text=True, timeout=60).stdout)
    assert "w (utež modela): nogomet 0.4, NHL 0.5, NBA 0.4, Euroliga 0.4; tanek trg (< 4 stavnice): 0.6" in out["export"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_export_odds_bands_and_underdog_warning(tmp_path):
    ko = _iso(dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=5))
    ms = []
    for i, (lh, la, o1, o2) in enumerate([(1.0, 1.6, 4.2, 1.9), (1.1, 1.5, 3.9, 2.0), (1.2, 1.4, 3.3, 2.2), (1.4, 1.2, 2.3, 3.4)]):
        mk = models.goal_markets(models.score_matrix(lh, la, 10, football.DC_RHO))
        for x in mk:
            if x["scope"] == "match" and x["kind"] in ("1", "2"):
                x["odds"] = o1 if x["kind"] == "1" else o2
                x["b365"] = x["odds"]
        ms.append({"id": f"fb:E0:{i}", "sport": "football", "league": "E0", "league_name": "PL", "home": f"H{i}",
                   "away": f"A{i}", "kickoff": ko, "exp": [lh, la], "markets": mk, "odds_raw": {}})
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"matches": ms, "meta": {}}), encoding="utf-8")
    exp = json.loads(subprocess.run([NODE, str(ROOT / "tests" / "js" / "harness.js"), str(fx)],
                                    capture_output=True, text=True, timeout=60).stdout)["export"]
    assert "Kvote pickov in kandidatov" in exp and ">3.50" in exp
    assert "UNDERDOG-HEAVY SLATE" in exp
    assert "Kalibracija p_model: še ni" in exp
