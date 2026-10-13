"""Entry point: python -m pipeline.build

Collects data for every sport, runs the models, updates the pick log and writes
JSON for the web app into app/data/.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import traceback

from . import football, odds_api, picks, players, sports
from .util import OUT_DIR, ROOT, iso, now_utc, read_json, setup_logging, write_json

log = logging.getLogger("pipeline")
PICKS_LOG = ROOT / "data" / "picks_log.json"

SPORTS = ("football", "nhl", "nba", "euroleague")


def _safe(name: str, fn, *args, errors: list, default=None):
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001
        log.error("%s failed: %s\n%s", name, e, traceback.format_exc())
        errors.append(f"{name}: {e}")
        return default


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sports", default=",".join(SPORTS))
    ap.add_argument("--date", help="override today (YYYY-MM-DD) for testing")
    args = ap.parse_args(argv)
    setup_logging()
    today = dt.date.fromisoformat(args.date) if args.date else now_utc().date()
    wanted = set(args.sports.split(","))
    errors: list[str] = []
    status: dict[str, dict] = {}
    all_matches, results = [], {}
    player_out: dict[str, list] = {}
    ratings = {}

    if "football" in wanted:
        fb = _safe("football", football.build, today, errors=errors)
        if fb:
            all_matches += fb["matches"]; results.update(fb["results"]); ratings["football"] = fb["ratings"]
            errors += fb["errors"][:30]
            pl = _safe("football players", players.football_players, today, fb, errors=errors, default=([], []))
            player_out["football"], perr = pl
            errors += perr
        status["football"] = {"matches": len(fb["matches"]) if fb else 0,
                              "players": len(player_out.get("football", [])), "ok": bool(fb and fb["matches"])}

    if "nhl" in wanted:
        nhl = _safe("nhl", sports.build_nhl, today, errors=errors)
        if nhl:
            all_matches += nhl["matches"]; results.update(nhl["results"]); ratings["nhl"] = nhl["ratings"]
            errors += nhl["errors"]
            pl = _safe("nhl players", players.nhl_players, today, nhl, errors=errors, default=([], []))
            player_out["nhl"], perr = pl
            errors += perr
        status["nhl"] = {"matches": len(nhl["matches"]) if nhl else 0,
                         "players": len(player_out.get("nhl", [])), "ok": bool(nhl and nhl.get("ratings"))}

    if "nba" in wanted:
        nba = _safe("nba", sports.build_basketball, today, "nba", errors=errors)
        if nba:
            all_matches += nba["matches"]; results.update(nba["results"]); ratings["nba"] = nba["ratings"]
            errors += nba["errors"]
            games = read_json(ROOT / "data" / "cache" / "espn_nba.json", {"events": {}})["events"]
            box = _safe("nba box", players.update_nba_boxes, games, errors=errors, default={"games": {}})
            player_out["nba"] = _safe("nba players", players.nba_players, nba, box, errors=errors, default=[])
        status["nba"] = {"matches": len(nba["matches"]) if nba else 0,
                         "players": len(player_out.get("nba", [])), "ok": bool(nba and nba.get("ratings"))}

    if "euroleague" in wanted:
        el = _safe("euroleague", sports.build_euroleague, today, errors=errors)
        if el:
            all_matches += el["matches"]; results.update(el["results"]); ratings["euroleague"] = el["ratings"]
            errors += el["errors"]
        status["euroleague"] = {"matches": len(el["matches"]) if el else 0, "ok": bool(el and el.get("ratings"))}

    errors += odds_api.apply(all_matches)

    # value info on each market
    for m in all_matches:
        for mk in m["markets"]:
            mk["fair"] = round(1 / mk["p"], 2) if mk["p"] > 0.005 else None
            if mk.get("odds"):
                mk["edge"] = round(mk["p"] * mk["odds"] - 1, 4)
    all_matches.sort(key=lambda m: m["kickoff"])

    log_data = picks.update(read_json(PICKS_LOG, {}), all_matches, results)
    write_json(PICKS_LOG, log_data, compact=True)

    gen = iso(now_utc())
    write_json(OUT_DIR / "matches.json", {"generated": gen, "matches": all_matches})
    write_json(OUT_DIR / "players.json", {"generated": gen, **player_out})
    write_json(OUT_DIR / "picks.json", {"generated": gen, "picks": log_data["picks"], "stats": log_data["stats"]})
    write_json(OUT_DIR / "meta.json", {"generated": gen, "today": today.isoformat(), "status": status,
                                       "errors": errors[:80], "ratings": ratings}, compact=False)
    log.info("done: %d matches, %d errors", len(all_matches), len(errors))
    for e in errors[:40]:
        log.warning("source issue: %s", e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
