# Changelog

## 2026-10-16 – calibration of p_model

**What the data says.** The calibration table in the export pooled only the "yes" side of the tracked markets
(home win, over, BTTS yes). On the live sample (57 settled football matches) the misses come mostly from goals:
over 2.5 hit 70 % (model 52 %), draws 14 % (model 25 %). With both sides of every market (over AND under, all of
1/X/2) a Platt fit on that small live sample does not lower the out-of-sample Brier. So the calibration is fitted
on the large walk-forward sample of the backtest and used only when it helps on held-out data.

1. **Calibration step before the blend** (`pipeline/calibration.py`): per sport, Platt scaling and isotonic
   regression on settled predictions (every outcome of a market). Samples: walk-forward predictions of
   `validate.py` (football 16 leagues + UEFA, NHL, NBA, EuroLeague) + live tracked predictions. Time order:
   fit on the older 70 %, test on the newest 30 %; the better method is used only when it beats the raw p_model
   (holdout Brier), then refitted on all samples. Refit weekly with the "Validate models" workflow
   (`data/calibration.json`). In `market.apply` the model parameters are moved to the calibrated main markets
   (1X2 / moneyline, main total), then guard and blend use the calibrated p_model. Markets keep `p_raw`
   (uncalibrated) and `p_model` (calibrated); tracked predictions now store `p_raw` for the next refit.
2. **Odds bands in the export**: share of today's picks and candidates by odds (< 1.80, 1.80–2.50, 2.50–3.50,
   > 3.50); "UNDERDOG-HEAVY SLATE" warning when more than 70 % are at 2.50 or more. The calibration status per
   sport (method, n, holdout Brier raw → calibrated) is in the header.
3. **Tests**: `test_calibration_fixes_a_flat_model_out_of_sample`, `test_calibrated_beats_raw_on_stored_settled_predictions`,
   `test_market_calibrates_before_blend`, `test_export_odds_bands_and_underdog_warning`.

## 2026-10-16 – follow-up review

1. **One-team rule does not reopen the Deportivo bug.** The Odds API event with only one matching team at the
   same kickoff is accepted only when the other name is a spelling variant of OUR team (`util.name_variant`: a
   shared distinctive word, or nearly the same word; generic words like "Deportivo" never count). An unknown or
   other club = DATA MISMATCH. Tests: `test_one_team_rule_never_reopens_deportivo` (RC Deportivo / Deportivo /
   Deportivo A Coruña / Unknown Town – Levante against Alaves – Levante → mismatch; Omonoia, Saski Baskonia,
   Beşiktaş J.K., KK Crvena zvezda, Olimpia Milano, FC Copenhagen → match), `test_deportivo_is_never_alaves`.
2. **Voided card picks were never in the totals.** 34 card picks were logged on 16 Oct and voided before any
   result (the first run report said 36: two of those were goal picks). None was settled, so none was in
   38–29 / 56.7 %. Corrected totals (shadow log): 94 picks without voids (89 old rule + 5 new), 38–29, hit 56.7 %
   (expected 61.9 %), +1.03u, ROI +1.5 %, 34 void. `n` no longer counts void picks; a separate `void` count.
   Automatic picks only for publishable markets: 1X2 / moneyline (1, X, 2), the goal or point total, spreads,
   BTTS only with its own bet365 price; never cards, corners, team totals or double chance (`picks.publishable`).
   Test: `test_voided_picks_never_counted_and_only_publishable_markets`.
3. **Official record = the user's bet365 picks.** The app saves them to `data/user_picks.json` with a GitHub
   commit (button "Shrani na GitHub", fine-grained token of the user, stored only on the device; automatic after
   "Zabeleži" when a token is set). The pipeline settles them and publishes `app/data/user_picks.json`
   (`pipeline/user_picks.py`, never writes the repository file). Picks only on the device show
   "NI VARNOSTNO SHRANJENO" with the days since the last save / export. The automatic picks are the
   "model shadow log". Test: `test_user_picks_are_settled_from_the_repo_file`.
4. **Injuries for football: "injury data: none" stays.** API-Football is off (free plan: no current season;
   only with `API_FOOTBALL_INJURIES=1`). ESPN has football injury endpoints, but they are empty for Ligue 1,
   LaLiga and even the Premier League (checked 16 Oct 2026). No other free current-season source was found.
   Premier League keeps FPL; lineups (~1 h before kick-off) are used for all leagues.
5. **Price history.** `pipeline/price_history.py` saves the no-vig price (Pinnacle, else average of books, else
   ESPN) for NHL, NBA, EuroLeague and UEFA cups: a "daily" and a "close" (≤ 3 h before kickoff) snapshot per
   match, in `data/price_history/` (committed after every run that adds one). The Odds API is read once a day
   and once more ≤ 2.5 h before a kickoff, only on days with matches (about 350 of 500 free credits per month).
   `validate.py` uses these prices once a competition has 150+ finished games with them.
   Test: `test_price_history_snapshots_and_close_fetch`.
6. **EuroLeague props need 5 games this season.** Below that: "too few games (n/5)", no probability (the averages
   are still shown). Test: `test_euroleague_props_need_five_games`.
7. **w per sport in the export header** (`w (utež modela): nogomet 0.4, NHL 0.4, NBA 0.4, Euroliga 0.4; tanek trg
   0.6`, from `meta.market_w`). Test: `test_export_header_prints_w_per_sport`.

## 2026-10-16 – EuroLeague player and team stats from the official API
- Source: `https://api-live.euroleague.net/v2` (the API of euroleaguebasketball.net), competition `E`, current
  season only (`E2026` = 2026-27). Season game list + one box score per played game.
- Players: season and last-5 averages of points, rebounds, assists, 3PM and PRA (and minutes). Prop probabilities use
  mu = (season + last 5) / 2 with the spread of the season games (points and PRA adjusted for the opponent's defence).
  Props need 3 games and 12 minutes per game. Replaces the old rows (last 15 games, also from last season).
- Teams: season and last-5 averages of the official team totals (points, points allowed, rebounds, assists, 3PM),
  shown in the match statistics and the export line.
- Cache: every API response for the day (`data/cache/euroleague_api/<date>/`, 3 days kept); finished box scores once
  (`data/cache/euroleague_box_v2.json`, reset at a new season).
- No silent fallback: when the API does not return the current season (error, empty list, no game played, no box
  score), there is no EuroLeague player data. The app and the export say "player data: none" with the reason.
- Tests: `test_euroleague_current_season_only_and_day_cache`, `test_euroleague_no_current_season_means_no_player_data`,
  `test_euroleague_season_and_last5_averages_and_props`, `test_export_says_player_data_none_without_current_season`.

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

### Follow-up (same day, after the first production run)
- Bug: bet365 goal prices (over / under 2.5) were attached to the cards / corners totals 2.5, so 36 card picks were
  logged with a wrong price. Fixed (`models.attach_b365` only for result and goal / point totals); those picks are
  voided with the reason (`picks.repair`), nothing is deleted. Test: `test_bet365_goal_prices_never_on_cards_or_corners`.
- The Odds API names ("Omonoia FC", "Los Angeles Clippers", EuroLeague club names) made false DATA MISMATCHes. Now a
  one-team name match at the same kickoff is accepted unless the other name is another club we know.
- "Hertha" is no longer a generic word; alias "Celta Fortuna" = "Celta B".
- Validation needs market prices: NHL, NBA, EuroLeague and UEFA have no price history, so they stay "not validated".

### 9. Export format
- No pick labels; ◆ only. New sections: DATA MISMATCH, "to check on bet365", NEZANESLJIVO (whole match).
- p_final everywhere, p_model in brackets. Calibration of all estimates and of logged picks only, report by w,
  the user's bet365 picks, injury coverage and "not validated" labels in the header.
