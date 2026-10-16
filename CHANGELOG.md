# Changelog

## 2026-10-16 – fixes after the 16 Oct 2026 slate

### 1. Team mapping (critical)
- ESPN called Deportivo "RC Deportivo". The name became "deportivo", and the old loose match joined it to
  "Deportivo Alaves" (Alaves). The model then used Alaves ratings and players.
- `util.match_name` is now strict: exact name or alias; containment only with a non-generic word and only one
  candidate; fuzzy only with a clear margin. A name like "Deportivo" alone never matches.
- `pipeline/teams.py`: ESPN teams are resolved by team ID (`Registry`, cache + `pipeline/team_ids.json` for checked
  manual links). A changed or duplicate ID is a conflict and is not resolved.
- Cross-check of every football fixture: football-data.co.uk against ESPN (kickoff ± 15 min, both teams). The
  Odds API events need both teams and kickoff ± 15 min. A disagreement is a DATA MISMATCH: no model, no pick, no
  value list, no player events. It is shown in the app and in the export.
- `pipeline/checks.py`: player events only for players of the two teams of the fixture.
- Tests: `test_deportivo_is_never_alaves`, `test_team_id_registry_conflict`, `test_kickoff_difference_is_mismatch`,
  `test_player_events_only_from_fixture_teams`, `tests/test_slate_16oct.py`.

### 2. Guardrail leaks
- One rule: a model–market gap > 10 pp on any main market (1X2 / moneyline / main total) makes the whole match
  unreliable (all markets, also BTTS, corners …). Before, only the flagged market group was blocked, so a pick on
  another market of the same match was possible.
- The flag is checked again right before a pick is logged. An open pick whose match is flagged later gets a red alert.
- Test: `test_unreliable_match_never_creates_a_logged_pick`.

### New pick rule
- A pick is valid when p_final × bet365 odds > 1.00, for every sport. No edge window, no minimum probability, no
  labels or tiers (Tip of the Day, Value, Lockdown, Bold … removed). Only flag: ◆ "strong" at ≥ 1.10.
- Automatic picks use only real bet365 prices (football-data.co.uk B365 columns, or bet365 in The Odds API). The
  average of books is never stored as a pick price. Tests: `test_picks_flow`, `test_pick_rule_and_strong_flag`.

### 3. Shrink toward the market
- p_final = w × p_model + (1 − w) × p_market (no vig) for football, NHL, NBA and EuroLeague. Market = Pinnacle,
  else the average of many books. Fewer than 4 books = "thin" market, w = 0.6. w is a setting per sport
  (`market.W`, env `MARKET_W_<SPORT>`).
- Model parameters are re-fitted to the blended main markets (goal expectations, or mean margin and total), so
  all lines stay consistent. Markets keep `p_model`, matches keep `exp_model` and `blend`.
- The pick log stores `p_model`, `p` (p_final) and `w`. New report: logged picks by sport and w (expected hit rate
  against the actual one, profit) and a calibration of logged picks only.
- Tests: `test_shrink_basketball_to_pinnacle`, `test_shrink_thin_market_uses_more_model_weight`,
  `test_shrink_nhl_refits_goals`, `test_market_guard_flags_and_blends_bodo_case`.

### 4. bet365 list
- New tab "bet365": markets sorted by kickoff with min odds = 1 / p_final. Inputs for bet365 odds and line,
  PICK / NO PICK, ◆, and "Zabeleži" (log). The log stores the bet365 odds you entered, in your browser
  (export / import as JSON). Picks settle from `app/data/results.json`. No scraping of bet365.
- OCR of a screenshot: not done (optional, later).

### 5. Exact line
- The app computes p for any entered line from the model distribution with the p_final parameters: football
  Dixon-Coles goal matrix, NHL Poisson (OT goal in totals, OT in moneyline), basketball normal distribution with the
  SDs fitted on past games. Check: `test_app_exact_line_matches_python_model` (JS = Python within 0.001).

### 6. Player events
- No 0.5 rebounds / assists lines. Export and lists show only lines with 55 % ≤ p ≤ 85 %.
- A player listed injured, suspended or doubtful (chance below 85 %, i.e. not "probable") loses his events.
- The export header prints "injury data: none" for every competition without an injury source.
  Note: Ligue 1 (Bourabaa) has no injury source until the API-Football key works.
- Tests: `test_doubtful_or_injured_player_loses_events`, `test_no_half_rebound_or_assist_lines`.

### 7. Context
- `pipeline/rest.py`: short rest (NHL / NBA back-to-back, EuroLeague ≤ 2 days) measured from the season data
  (actual against expected, shrunk, at least 60 games). Each adjustment and its sample size is in the export line.
- NHL goalie: no starter is published before the game, so the goalie changes nothing (information only). The old
  back-to-back guess (35 % for the No. 1) was removed.
- Test: `test_rest_effect_measured_from_data`, goalie tests.

### 8. Backtest before publishing
- `pipeline/validate.py` + workflow "Validate models" (Mondays, or by hand): walk-forward per competition (16
  football leagues, UCL / UEL / UECL, NHL, NBA, EuroLeague). Brier (model, p_final, market), picks with the p_final
  rule, hit rate against expected, ROI at closing odds (football: bet365 closing; others: ESPN; EuroLeague: no
  odds). Results in `data/validation.json` and on the new "Backtest" page.
- Every competition without a backtest (or with fewer than 150 games) shows "not validated" in the app and the export.
- Test: `test_validation_book_pick_rule_and_scores`.

### 9. Export format
- No pick labels; ◆ only. New sections: DATA MISMATCH, "to check on bet365", NEZANESLJIVO (whole match).
- p_final everywhere, p_model in brackets. Calibration of all estimates and of logged picks only, report by w,
  the user's bet365 picks, injury coverage and "not validated" labels in the header.
