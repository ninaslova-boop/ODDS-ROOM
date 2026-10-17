"""Entry point: python -m pipeline.build

Collects data for every sport, runs the models, updates the pick log and writes
JSON for the web app into app/data/.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import traceback

from . import (adjust, availability, calibration, checks, euroleague_players, football, goalies, market, odds_api, picks, players,
               price_history, rest, soccer_espn, sports, user_picks)
from .util import CACHE_DIR, OUT_DIR, ROOT, RUN, iso, now_utc, parse_iso, read_json, setup_logging, write_json

log = logging.getLogger("pipeline")
PICKS_LOG = ROOT / "data" / "picks_log.json"

SPORTS = ("football", "nhl", "nba", "euroleague")
LAST_FULL = CACHE_DIR / "last_full.json"
FULL_EVERY_H = 5.5


def pick_mode(arg: str) -> str:
    """auto: a full refresh when the last one is older than FULL_EVERY_H hours, else a quick one."""
    if arg in ("full", "quick"):
        return arg
    last = (read_json(LAST_FULL) or {}).get("t")
    if not last:
        return "full"
    age_h = (now_utc() - parse_iso(last)).total_seconds() / 3600
    return "full" if age_h >= FULL_EVERY_H else "quick"


def _safe(name: str, fn, *args, errors: list, default=None):
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001
        log.error("%s failed: %s\n%s", name, e, traceback.format_exc())
        errors.append(f"{name}: {e}")
        return default


RECENT = CACHE_DIR / "recent_ids.json"
RECENT_DAYS = 21


def recent_results(matches: list[dict], results: dict) -> dict:
    """Results of matches shown in the last RECENT_DAYS days (the app settles the user's bet365 picks
    with them). The ids of shown matches are kept in a small cache."""
    seen = read_json(RECENT, {}) or {}
    for m in matches:
        seen[m["id"]] = m["kickoff"]
    cutoff = iso(now_utc() - dt.timedelta(days=RECENT_DAYS))
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    write_json(RECENT, seen)
    return {k: results[k] for k in seen if k in results}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sports", default=",".join(SPORTS))
    ap.add_argument("--date", help="override today (YYYY-MM-DD) for testing")
    ap.add_argument("--mode", default="auto", choices=["auto", "full", "quick"])
    args = ap.parse_args(argv)
    setup_logging()
    RUN["mode"] = pick_mode(args.mode)
    log.info("run mode: %s", RUN["mode"])
    today = dt.date.fromisoformat(args.date) if args.date else now_utc().date()
    wanted = set(args.sports.split(","))
    errors: list[str] = []
    status: dict[str, dict] = {}
    all_matches, results = [], {}
    player_out: dict[str, list] = {}
    ratings = {}
    nhl_hist = None
    rest_inputs = []      # (sport, history, expected-function, rebuild)
    mismatches: list[dict] = []

    if "football" in wanted:
        fb = _safe("football", football.build, today, errors=errors)
        sp = _safe("football ESPN (Brazil, UEFA)", soccer_espn.build, today, fb, errors=errors)
        if sp:
            all_matches += sp["matches"]; results.update(sp["results"]); errors += sp["errors"]
        if fb:
            fb["extra_matches"] = sp["matches"] if sp else []
            all_matches += fb["matches"]; results.update(fb["results"]); ratings["football"] = fb["ratings"]
            mismatches += [{**x, "sport": "football"} for x in fb.get("mismatches", [])]
            errors += fb["errors"][:30]
            pl = _safe("football players", players.football_players, today, fb, errors=errors, default=([], []))
            player_out["football"], perr = pl
            errors += perr
        status["football"] = {"matches": len([m for m in all_matches if m["sport"] == "football"]),
                              "players": len(player_out.get("football", [])), "ok": bool(fb and fb["matches"])}

    if "nhl" in wanted:
        nhl = _safe("nhl", sports.build_nhl, today, errors=errors)
        if nhl:
            all_matches += nhl["matches"]; results.update(nhl["results"]); ratings["nhl"] = nhl["ratings"]
            errors += nhl["errors"]
            nhl_hist = nhl.get("hist")
            if nhl.get("model"):
                rest_inputs.append(("nhl", nhl["hist"], nhl["model"].expected, adjust._rebuild_nhl))
            pl = _safe("nhl players", players.nhl_players, today, nhl, errors=errors, default=([], []))
            player_out["nhl"], perr = pl
            errors += perr
        status["nhl"] = {"matches": len(nhl["matches"]) if nhl else 0,
                         "players": len(player_out.get("nhl", [])), "ok": bool(nhl and nhl.get("ratings"))}

    if "nba" in wanted:
        nba = _safe("nba", sports.build_basketball, today, "nba", errors=errors)
        if nba:
            all_matches += nba["matches"]; results.update(nba["results"]); ratings["nba"] = nba["ratings"]
            if nba.get("model"):
                rest_inputs.append(("nba", nba["hist"], nba["model"].expected, adjust._rebuild_basketball))
            errors += nba["errors"]
            games = read_json(CACHE_DIR / "espn_nba.json", {"events": {}})["events"]
            box = _safe("nba box", players.update_nba_boxes, games, errors=errors, default={"games": {}})
            player_out["nba"] = _safe("nba players", players.nba_players, nba, box, errors=errors, default=[])
        status["nba"] = {"matches": len(nba["matches"]) if nba else 0,
                         "players": len(player_out.get("nba", [])), "ok": bool(nba and nba.get("ratings"))}

    el_absent = None          # None = no EuroLeague box scores, so no absence data
    player_data: dict[str, dict] = {}
    if "euroleague" in wanted:
        el = _safe("euroleague", sports.build_euroleague, today, errors=errors)
        if el:
            all_matches += el["matches"]; results.update(el["results"]); ratings["euroleague"] = el["ratings"]
            if el.get("model"):
                rest_inputs.append(("euroleague", el["hist"], el["model"].expected, adjust._rebuild_basketball))
            errors += el["errors"]
            # player and team stats: official EuroLeague API, current season only (no fallback)
            box = _safe("euroleague box", euroleague_players.load, today, errors=errors,
                        default={"ok": False, "reason": "napaka pri nalaganju", "games": {}})
            if box["ok"]:
                player_out["euroleague"] = _safe("euroleague players", euroleague_players.players, el, box,
                                                 errors=errors, default=[])
                euroleague_players.attach_team_stats(el["matches"], euroleague_players.team_stats(box))
                el_absent = euroleague_players.absences(player_out["euroleague"] or [], box)
                player_data["euroleague"] = {"ok": True, "season": box["season"], "games": len(box["games"])}
            else:
                player_out["euroleague"] = []
                player_data["euroleague"] = {"ok": False, "season": box.get("season"), "reason": box["reason"]}
                errors.append(f"EuroLeague player data: none ({box['reason']})")
        status["euroleague"] = {"matches": len(el["matches"]) if el else 0,
                                "players": len(player_out.get("euroleague", [])), "ok": bool(el and el.get("ratings"))}

    errors += odds_api.apply(all_matches)

    # lineups and injuries
    av = _safe("availability", availability.collect, all_matches, today,
               {"euroleague": el_absent} if el_absent is not None else {},
               errors=errors, default=({}, []))
    avail, aerr = av
    errors += aerr
    n_adj = _safe("adjust", adjust.apply, all_matches, player_out, avail, errors=errors, default=0)
    log.info("availability: %d matches with news, %d adjusted", len(avail), n_adj or 0)
    if nhl_hist is not None:
        n_g = _safe("nhl goalies", goalies.apply, all_matches, today, nhl_hist, adjust._rebuild_nhl, errors=errors,
                    default=0)
        log.info("nhl goalies: %s matches adjusted", n_g)
    for sport, hist, expected, rb in rest_inputs:
        n_r = _safe(f"{sport} rest", rest.apply, all_matches, sport, hist, expected, rb, errors=errors, default=0)
        log.info("%s rest days: %s matches adjusted", sport, n_r)

    # model against the market: guard and blend (football)
    rebuild = {"football": adjust._rebuild_football, "nhl": adjust._rebuild_nhl,
               "nba": adjust._rebuild_basketball, "euroleague": adjust._rebuild_basketball}
    mst = _safe("market", market.apply, all_matches, rebuild, errors=errors, default={})
    log.info("market check: %s", mst)

    # price history (NHL, NBA, EuroLeague, UEFA): daily and closing prices without margin
    _safe("price history", price_history.record, all_matches, now_utc(), errors=errors, default=0)

    # two sources disagree on a fixture (The Odds API): DATA MISMATCH, no picks from it
    for m in all_matches:
        if m.get("mismatch"):
            mismatches.append({"sport": m["sport"], "league": m.get("league_name"), "kickoff": m["kickoff"],
                               "home": m["home"], "away": m["away"], "reason": m["mismatch"], "match": m["id"]})
    # player events only from players of the two teams of the fixture
    bad_players = checks.player_fixture_violations(player_out, all_matches)
    if bad_players:
        n_rm = checks.enforce_player_fixture(player_out, all_matches)
        log.warning("player check: %d players unlinked (%s)", n_rm, "; ".join(bad_players[:5]))

    # value info on each market (p = p_final)
    for m in all_matches:
        for mk in m["markets"]:
            mk["fair"] = round(1 / mk["p"], 2) if mk["p"] > 0.005 else None
            if mk.get("odds"):
                mk["edge"] = round(mk["p"] * mk["odds"] - 1, 4)
            if mk.get("b365"):
                mk["ev"] = round(mk["p"] * mk["b365"], 4)
                mk["strong"] = picks.is_strong(mk["p"], mk["b365"])
    all_matches.sort(key=lambda m: m["kickoff"])

    if RUN["mode"] == "full":
        log_data = picks.update(read_json(PICKS_LOG, {}), all_matches, results)
        write_json(PICKS_LOG, log_data, compact=True)
        write_json(LAST_FULL, {"t": iso(now_utc())})
    else:
        # quick runs do not change the pick log (it is committed only by full runs)
        log_data = read_json(PICKS_LOG, {}) or {}
        log_data.setdefault("picks", [])
        picks.repair(log_data["picks"])
        log_data["stats"] = picks.stats(log_data["picks"], log_data.get("tracked", []))
    picks.live_alerts(log_data["picks"], all_matches)

    gen = iso(now_utc())
    write_json(OUT_DIR / "matches.json", {"generated": gen, "matches": all_matches, "mismatches": mismatches})
    write_json(OUT_DIR / "validation.json", read_json(ROOT / "data" / "validation.json", {}) or {"competitions": {}})
    write_json(OUT_DIR / "results.json", {"generated": gen, "results": recent_results(all_matches, results)})
    write_json(OUT_DIR / "user_picks.json", {"generated": gen, **user_picks.publish(results)})
    write_json(OUT_DIR / "players.json", {"generated": gen, **player_out})
    write_json(OUT_DIR / "picks.json", {"generated": gen, "picks": log_data["picks"], "stats": log_data["stats"]})
    write_json(OUT_DIR / "meta.json", {"generated": gen, "today": today.isoformat(), "status": status,
                                       "mode": RUN["mode"], "news": len(avail),
                                       "injury_sources": availability.LAST_COVERAGE, "player_data": player_data,
                                       "market_w": {sp: market.weight(sp, False) for sp in market.W},
                                       "market_w_thin": market.W_THIN,
                                       "calibration": calibration.summary(),
                                       "errors": errors[:80], "ratings": ratings}, compact=False)
    (CACHE_DIR / "last_mode.txt").write_text(RUN["mode"], encoding="utf-8")
    log.info("done (%s): %d matches, %d errors", RUN["mode"], len(all_matches), len(errors))
    for e in errors[:40]:
        log.warning("source issue: %s", e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
