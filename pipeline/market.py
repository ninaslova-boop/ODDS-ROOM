"""Model against the market, for every sport.

* Reference market (no margin, proportional removal):
  Pinnacle when available, else the average of many books. A market with fewer than
  MIN_BOOKS books (for example the single ESPN price) is "thin".
* Guard (one rule for the whole match): when the model differs from the reference by more than
  GUARD_PP on any main market (1X2 / moneyline, or the main total), the WHOLE match is
  unreliable: every market gets "unreliable": True and the match gets "unreliable": True.
  No pick is logged from it and it is not shown in "reliable" lists.
* Calibration (before everything else): p_model is calibrated per sport (calibration.py, refit weekly);
  the model parameters are moved to the calibrated main markets. "p_raw" keeps the uncalibrated value,
  "p_model" is the calibrated one. The guard and the blend use the calibrated p_model.
* Shrink to the market (all sports):
      p_final = w * p_model + (1 - w) * p_market
  with w = W[sport] (setting per sport, env MARKET_W_<SPORT>), or W_THIN for a thin market.
  The model parameters are re-fitted to the blended main markets (football and NHL: the two
  goal expectations; basketball: mean margin and mean total). Every other line and market is
  then derived from the same distribution, so a line the user enters (bet365 177.5 instead of
  178.5) gets a p_final that is consistent with the market at its nearest line.
  Each market keeps "p_model"; the match keeps "exp_model" and "blend".
"""
from __future__ import annotations

import logging
import math
import os
from statistics import NormalDist

import numpy as np

from . import models

log = logging.getLogger("pipeline.market")

GUARD_PP = 0.10
W = {"football": 0.4, "nhl": 0.4, "nba": 0.4, "euroleague": 0.4}
W_THIN = 0.6
MIN_BOOKS = 4
W_MODEL = W["football"]          # kept for older code and tests
RESULT_SCOPES = ("match", "dnb", "spread")
US_SPORTS = ("nhl", "nba", "euroleague")
_N = NormalDist()


def weight(sport: str, thin: bool) -> float:
    if thin:
        return W_THIN
    env = os.environ.get(f"MARKET_W_{sport.upper()}")
    try:
        return float(env) if env else W.get(sport, 0.4)
    except ValueError:
        return W.get(sport, 0.4)


def no_vig(prices: dict[str, float], keys: tuple[str, ...]) -> dict[str, float] | None:
    """Proportional margin removal. None when a price is missing."""
    if not all(prices.get(k) and prices[k] > 1 for k in keys):
        return None
    inv = {k: 1 / prices[k] for k in keys}
    s = sum(inv.values())
    return {k: v / s for k, v in inv.items()}


def reference(m: dict) -> tuple[str, dict[str, float]] | None:
    """(source, {"1","X","2"[, "over2.5"]}) without margin, best source first (football)."""
    r = reference_full(m)
    return (r[0], r[1]) if r else None


def reference_full(m: dict) -> tuple[str, dict[str, float], int | None] | None:
    """Like reference() plus the number of books (None = many, e.g. the football-data.co.uk average)."""
    o = m.get("odds_raw") or {}
    cands = []
    if o.get("pin"):
        cands.append(("pinnacle", o["pin"], None))
    if o.get("1"):
        cands.append(("avg", o, o.get("n_books")))
    if o.get("ml_home"):
        cands.append(("espn", {"1": o.get("ml_home"), "X": o.get("ml_draw"), "2": o.get("ml_away"),
                               "over2.5": o.get("over") if o.get("total") == 2.5 else None,
                               "under2.5": o.get("under") if o.get("total") == 2.5 else None}, 1))
    for src, pr, n in cands:
        r = no_vig(pr, ("1", "X", "2"))
        if not r:
            continue
        ou = no_vig(pr, ("over2.5", "under2.5"))
        if ou:
            r["over2.5"] = ou["over2.5"]
        return src, r, n
    return None


def model_probs(M) -> dict[str, float]:
    k = M.shape[0]
    hg = np.arange(k)[:, None]; ag = np.arange(k)[None, :]
    return {"1": float(M[hg > ag].sum()), "X": float(M[hg == ag].sum()), "2": float(M[hg < ag].sum()),
            "over2.5": float(M[(hg + ag) > 2.5].sum())}


def nhl_probs(lh: float, la: float, line: float | None) -> dict[str, float]:
    """Moneyline incl. OT and the total incl. the OT / SO goal (same rules as models.goal_markets)."""
    M = models.score_matrix(lh, la, 12, 0.0)
    k = M.shape[0]
    hg = np.arange(k)[:, None]; ag = np.arange(k)[None, :]
    p_draw = float(M[hg == ag].sum())
    out = {"1": float(M[hg > ag].sum()) + p_draw * 0.52, "sum": (lh + la) / 10}
    if line is not None:
        tot_ot = hg + ag + (hg == ag)
        out["over"] = float(M[tot_ot > line].sum())
    return out


def fit_params(x0: tuple[float, float], target: dict[str, float], probs) -> tuple[float, float]:
    """Two positive parameters whose probs(a, b) best match target (damped Gauss-Newton in log space)."""
    keys = [k for k in target]
    x = [math.log(max(x0[0], 0.05)), math.log(max(x0[1], 0.05))]

    def resid(v):
        p = probs(math.exp(v[0]), math.exp(v[1]))
        return [p[k] - target[k] for k in keys]

    r = resid(x)
    for _ in range(30):
        eps = 1e-4
        J = []
        for j in range(2):
            v = list(x); v[j] += eps
            rj = resid(v)
            J.append([(rj[i] - r[i]) / eps for i in range(len(keys))])
        a11 = sum(J[0][i] ** 2 for i in range(len(keys))) + 1e-6
        a22 = sum(J[1][i] ** 2 for i in range(len(keys))) + 1e-6
        a12 = sum(J[0][i] * J[1][i] for i in range(len(keys)))
        g1 = -sum(J[0][i] * r[i] for i in range(len(keys)))
        g2 = -sum(J[1][i] * r[i] for i in range(len(keys)))
        det = a11 * a22 - a12 * a12
        if abs(det) < 1e-12:
            break
        d = [(g1 * a22 - g2 * a12) / det, (a11 * g2 - a12 * g1) / det]
        step = 1.0
        base = sum(e * e for e in r)
        while step > 1e-3:
            v = [x[0] + step * d[0], x[1] + step * d[1]]
            rv = resid(v)
            if sum(e * e for e in rv) < base:
                x, r = v, rv
                break
            step /= 2
        else:
            break
        if max(abs(step * d[0]), abs(step * d[1])) < 1e-6:
            break
    return math.exp(x[0]), math.exp(x[1])


def fit_lambdas(lh: float, la: float, target: dict[str, float], rho: float) -> tuple[float, float]:
    """Football goal expectations whose score matrix best matches target 1 / 2 (/ over2.5)."""
    tg = {k: target[k] for k in ("1", "2", "over2.5") if k in target}
    return fit_params((lh, la), tg, lambda a, b: model_probs(models.score_matrix(a, b, 10, rho)))


def _key(mk: dict) -> tuple:
    return mk["stat"], mk["scope"], mk["kind"], mk.get("line")


def _keep_model_p(m: dict) -> dict[tuple, float]:
    return {_key(mk): mk["p"] for mk in m["markets"]}


def _set_p_model(m: dict, before: dict[tuple, float]):
    for mk in m["markets"]:
        mk["p_model"] = round(before.get(_key(mk), mk["p"]), 4)


def _flag_match(m: dict):
    """One rule: a gap on any main market makes the whole match unreliable."""
    m["unreliable"] = True
    for mk in m["markets"]:
        mk["unreliable"] = True


def is_unreliable(m: dict) -> bool:
    return bool(m.get("unreliable") or m.get("mismatch") or any(mk.get("unreliable") for mk in m["markets"]))


def apply(matches: list[dict], rebuild, cal: dict | None = None) -> dict:
    """rebuild: {sport: fn(match, a, b)} that re-creates the markets of a match from its two parameters
    (a callable alone is used for football). cal: {sport: calibration function} (default: data/calibration.json).
    Order: calibrate p_model -> guard -> blend with the market. Returns counters for logging."""
    if callable(rebuild):
        rebuild = {"football": rebuild}
    if cal is None:
        from . import calibration
        cal = {sp: calibration.function(sp) for sp in ("football", *US_SPORTS)}
    stats = {"checked": 0, "flagged": 0, "blended": 0, "calibrated": 0}
    for m in matches:
        raw = _keep_model_p(m)
        fn, rb = cal.get(m["sport"]), rebuild.get(m["sport"])
        if fn and rb and m.get("exp"):
            stats["calibrated"] += calibrate(m, fn, rb)
        before = _keep_model_p(m)
        if m["sport"] in US_SPORTS:
            res = guard_us(m, rb)
        elif m["sport"] == "football" and m.get("exp"):
            res = _football(m, rb)
        else:
            res = None
        _set_p_model(m, before)
        for mk in m["markets"]:
            mk["p_raw"] = round(raw.get(_key(mk), mk["p_model"]), 4)
        if res is None:
            continue
        stats["checked"] += 1
        stats["flagged"] += res["flagged"]
        stats["blended"] += res["blended"]
    return stats


def _pair(fn, p: float) -> float:
    """Calibrated probability of one side of a two-way market, so that both sides still sum to 1."""
    a, b = fn(p), fn(1 - p)
    return a / (a + b)


def calibrate(m: dict, fn, rebuild) -> int:
    """Move the model parameters so that the main markets match the calibrated probabilities.
    Every other market follows from the same distribution. Returns 1 when the match was changed."""
    lh, la = m["exp"]
    if m["sport"] == "football":
        raw = model_probs(models.score_matrix(lh, la, 10, -0.06))
        c = {k: fn(raw[k]) for k in ("1", "X", "2")}
        s = sum(c.values())
        target = {k: v / s for k, v in c.items()}
        target["over2.5"] = _pair(fn, raw["over2.5"])
        new = fit_lambdas(lh, la, target, -0.06)
    elif m["sport"] == "nhl":
        line = (m.get("odds_raw") or {}).get("total") or 6.5
        raw = nhl_probs(lh, la, line)
        new = fit_params((lh, la), {"1": _pair(fn, raw["1"]), "over": _pair(fn, raw["over"])},
                         lambda a, b: nhl_probs(a, b, line))
    elif m.get("sd"):
        sd_t, sd_m = m["sd"]
        p1 = models.norm_cdf((lh - la) / sd_m)
        margin = sd_m * _N.inv_cdf(min(max(_pair(fn, p1), 1e-4), 1 - 1e-4))
        total = lh + la
        new = ((total + margin) / 2, (total - margin) / 2)
    else:
        return 0
    if abs(new[0] - lh) < 1e-4 and abs(new[1] - la) < 1e-4:
        return 0
    m["calibration"] = {"exp_raw": [round(lh, 3), round(la, 3)]}
    rebuild(m, *new)
    return 1


def _football(m: dict, rebuild) -> dict | None:
    ref = reference_full(m)
    if not ref:
        return None
    src, rp, n_books = ref
    rho = -0.06
    model = model_probs(models.score_matrix(m["exp"][0], m["exp"][1], 10, rho))
    gap_1x2 = max(abs(model[k] - rp[k]) for k in ("1", "X", "2"))
    gap_ou = abs(model["over2.5"] - rp["over2.5"]) if "over2.5" in rp else None
    thin = n_books is not None and n_books < MIN_BOOKS
    w = weight("football", thin)
    info = {"src": src, "p": {k: round(v, 4) for k, v in rp.items()},
            "model": {k: round(v, 4) for k, v in model.items()},
            "gap_1x2": round(gap_1x2, 4), "gap_ou": None if gap_ou is None else round(gap_ou, 4),
            "thin": thin, "n_books": n_books}
    blended = 0
    if rebuild:
        target = {k: w * model[k] + (1 - w) * rp[k] for k in rp}
        info["blend"] = {"w_model": w, "exp_model": list(m["exp"])}
        m["exp_model"] = list(m["exp"])
        lh, la = fit_lambdas(m["exp"][0], m["exp"][1], target, rho)
        rebuild(m, lh, la)
        m["blend"] = {"w": w, "src": src, "thin": thin}
        blended = 1
    m["market_ref"] = info
    flagged = gap_1x2 > GUARD_PP or (gap_ou is not None and gap_ou > GUARD_PP)
    if flagged:
        _flag_match(m)
    return {"flagged": int(flagged), "blended": blended}


def guard_us(m: dict, rebuild=None) -> dict | None:
    """NHL / NBA / EuroLeague: moneyline (incl. OT) and the main total."""
    o = m.get("odds_raw") or {}
    ml = no_vig({"1": o.get("ml_home"), "2": o.get("ml_away")}, ("1", "2"))
    line = o.get("total")
    ou = no_vig({"over": o.get("over"), "under": o.get("under")}, ("over", "under")) if line else None
    if not ml and not ou:
        return None

    def mp(scope, kind, ln=None):
        return next((k["p"] for k in m["markets"] if k["scope"] == scope and k["kind"] == kind
                     and (ln is None or k.get("line") == ln)), None)

    src = o.get("ref_src", "espn")
    n_books = o.get("n_books", 1 if src == "espn" else None)
    thin = n_books is not None and n_books < MIN_BOOKS
    w = weight(m["sport"], thin)
    info = {"src": src, "p": {}, "model": {}, "gap_1x2": None, "gap_ou": None, "thin": thin, "n_books": n_books}
    flag = False
    p1 = mp("match", "1")
    if ml and p1 is not None and mp("match", "2") is not None:
        p2 = mp("match", "2")
        info["p"].update({"1": round(ml["1"], 4), "2": round(ml["2"], 4)})
        info["model"].update({"1": p1, "2": p2})
        info["gap_1x2"] = round(max(abs(p1 - ml["1"]), abs(p2 - ml["2"])), 4)
        flag |= info["gap_1x2"] > GUARD_PP
    po = None
    if ou:
        po = mp("total", "over", line)
        if po is None and m.get("exp"):
            po = _model_over(m, line)
    if ou and po is not None:
        info["p"]["over"] = round(ou["over"], 4)
        info["model"]["over"] = round(po, 4)
        info["line"] = line
        info["gap_ou"] = round(abs(po - ou["over"]), 4)
        flag |= info["gap_ou"] > GUARD_PP
    blended = 0
    if rebuild and m.get("exp"):
        t1 = w * p1 + (1 - w) * ml["1"] if (ml and p1 is not None) else None
        to = w * po + (1 - w) * ou["over"] if (ou and po is not None) else None
        new = _refit_us(m, t1, to, line)
        if new:
            m["exp_model"] = list(m["exp"])
            rebuild(m, *new)
            m["blend"] = {"w": w, "src": src, "thin": thin}
            info["blend"] = {"w_model": w, "exp_model": m["exp_model"]}
            blended = 1
    m["market_ref"] = info
    if flag:
        _flag_match(m)
    return {"flagged": int(flag), "blended": blended}


def _model_over(m: dict, line: float) -> float:
    """Model probability of over `line` (the market's line), from the model distribution."""
    if m["sport"] == "nhl":
        return nhl_probs(m["exp"][0], m["exp"][1], line)["over"]
    t = m["exp"][0] + m["exp"][1]
    return 1 - models.norm_cdf((line - t) / m["sd"][0])


def _refit_us(m: dict, t1: float | None, to: float | None, line: float | None):
    lh, la = m["exp"]
    if m["sport"] == "nhl":
        target = {}
        if t1 is not None:
            target["1"] = t1
        if to is not None:
            target["over"] = to
        if len(target) < 2:
            target["sum"] = (lh + la) / 10      # keep the model's total when the market has only one price
        return fit_params((lh, la), target, lambda a, b: nhl_probs(a, b, line))
    if not m.get("sd"):
        return None
    sd_t, sd_m = m["sd"]
    margin, total = lh - la, lh + la
    if t1 is not None:
        margin = sd_m * _N.inv_cdf(min(max(t1, 1e-4), 1 - 1e-4))
    if to is not None and line is not None:
        total = line + sd_t * _N.inv_cdf(min(max(to, 1e-4), 1 - 1e-4))
    return (total + margin) / 2, (total - margin) / 2
