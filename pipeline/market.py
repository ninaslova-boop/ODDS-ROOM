"""Model against the market (football).

* Reference: Pinnacle without margin when available (football-data.co.uk columns PS*/P>2.5,
  or The Odds API), else the ESPN or market-average prices without margin.
* Guard: when the model differs from the reference by more than GUARD_PP on 1X2 or on
  Over/Under 2.5, the markets of that group get "unreliable": True. They are shown with a
  warning in the app and never logged as value picks.
* Blend: where Pinnacle is available, the final probabilities are W_MODEL * model +
  (1 - W_MODEL) * Pinnacle. The two goal expectations are re-fitted to the blended 1X2 and
  O/U 2.5, so every derived market (double chance, BTTS, other lines, exact scores) stays
  consistent with them.
"""
from __future__ import annotations

import logging
import math

import numpy as np

from . import models

log = logging.getLogger("pipeline.market")

GUARD_PP = 0.10
W_MODEL = 0.4
RESULT_SCOPES = ("match", "dnb", "spread")


def no_vig(prices: dict[str, float], keys: tuple[str, ...]) -> dict[str, float] | None:
    """Proportional margin removal. None when a price is missing."""
    if not all(prices.get(k) and prices[k] > 1 for k in keys):
        return None
    inv = {k: 1 / prices[k] for k in keys}
    s = sum(inv.values())
    return {k: v / s for k, v in inv.items()}


def reference(m: dict) -> tuple[str, dict[str, float]] | None:
    """(source, {"1","X","2"[, "over2.5"]}) without margin, best source first."""
    o = m.get("odds_raw") or {}
    cands = []
    if o.get("pin"):
        cands.append(("pinnacle", o["pin"]))
    if o.get("ml_home"):
        cands.append(("espn", {"1": o.get("ml_home"), "X": o.get("ml_draw"), "2": o.get("ml_away"),
                               "over2.5": o.get("over") if o.get("total") == 2.5 else None,
                               "under2.5": o.get("under") if o.get("total") == 2.5 else None}))
    if o.get("1"):
        cands.append(("avg", o))
    for src, pr in cands:
        r = no_vig(pr, ("1", "X", "2"))
        if not r:
            continue
        ou = no_vig(pr, ("over2.5", "under2.5"))
        if ou:
            r["over2.5"] = ou["over2.5"]
        return src, r
    return None


def model_probs(M) -> dict[str, float]:
    k = M.shape[0]
    hg = np.arange(k)[:, None]; ag = np.arange(k)[None, :]
    return {"1": float(M[hg > ag].sum()), "X": float(M[hg == ag].sum()), "2": float(M[hg < ag].sum()),
            "over2.5": float(M[(hg + ag) > 2.5].sum())}


def fit_lambdas(lh: float, la: float, target: dict[str, float], rho: float) -> tuple[float, float]:
    """Goal expectations whose score matrix best matches target 1 / 2 (/ over2.5).
    Damped Gauss-Newton in log space with numeric derivatives."""
    keys = [k for k in ("1", "2", "over2.5") if k in target]
    x = [math.log(max(lh, 0.05)), math.log(max(la, 0.05))]

    def resid(v):
        p = model_probs(models.score_matrix(math.exp(v[0]), math.exp(v[1]), 10, rho))
        return [p[k] - target[k] for k in keys]

    r = resid(x)
    for _ in range(30):
        eps = 1e-4
        J = []
        for j in range(2):
            v = list(x); v[j] += eps
            rj = resid(v)
            J.append([(rj[i] - r[i]) / eps for i in range(len(keys))])
        # normal equations for 2 unknowns: (J^T J + mu I) d = -J^T r
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


def apply(matches: list[dict], rebuild) -> dict:
    """rebuild(match, lh, la) re-creates the goal markets of a football match.
    Returns counters for logging."""
    stats = {"checked": 0, "flagged": 0, "blended": 0}
    for m in matches:
        if m["sport"] in US_SPORTS:
            if guard_us(m):
                stats["flagged"] += 1
            stats["checked"] += "market_ref" in m
            continue
        if m["sport"] != "football" or not m.get("exp"):
            continue
        ref = reference(m)
        if not ref:
            continue
        src, rp = ref
        rho = -0.06
        model = model_probs(models.score_matrix(m["exp"][0], m["exp"][1], 10, rho))
        gap_1x2 = max(abs(model[k] - rp[k]) for k in ("1", "X", "2"))
        gap_ou = abs(model["over2.5"] - rp["over2.5"]) if "over2.5" in rp else None
        stats["checked"] += 1
        info = {"src": src, "p": {k: round(v, 4) for k, v in rp.items()},
                "model": {k: round(v, 4) for k, v in model.items()},
                "gap_1x2": round(gap_1x2, 4), "gap_ou": None if gap_ou is None else round(gap_ou, 4)}
        if src == "pinnacle":
            target = {k: W_MODEL * model[k] + (1 - W_MODEL) * rp[k] for k in rp}
            info["blend"] = {"w_model": W_MODEL, "exp_model": list(m["exp"])}
            lh, la = fit_lambdas(m["exp"][0], m["exp"][1], target, rho)
            rebuild(m, lh, la)
            stats["blended"] += 1
        flag_1x2 = gap_1x2 > GUARD_PP
        flag_ou = gap_ou is not None and gap_ou > GUARD_PP
        for mk in m["markets"]:
            if mk["stat"] != "goals":
                continue
            if (flag_1x2 and mk["scope"] in RESULT_SCOPES) or (flag_ou and mk["scope"] == "total"):
                mk["unreliable"] = True
        if flag_1x2 or flag_ou:
            stats["flagged"] += 1
        m["market_ref"] = info
    return stats


# --------------------------------------------------------------------------
# NHL / NBA: the same guard against ESPN prices without margin (no Pinnacle there)
# --------------------------------------------------------------------------
US_SPORTS = ("nhl", "nba")


def guard_us(m: dict) -> bool:
    """Moneyline (incl. OT) and the ESPN total line. Returns True when a group was flagged."""
    o = m.get("odds_raw") or {}
    ml = no_vig({"1": o.get("ml_home"), "2": o.get("ml_away")}, ("1", "2"))
    line = o.get("total")
    ou = no_vig({"over": o.get("over"), "under": o.get("under")}, ("over", "under")) if line else None
    if not ml and not ou:
        return False

    def mp(scope, kind, ln=None):
        return next((k["p"] for k in m["markets"] if k["scope"] == scope and k["kind"] == kind
                     and (ln is None or k.get("line") == ln)), None)

    info = {"src": "espn", "p": {}, "model": {}, "gap_1x2": None, "gap_ou": None}
    flag_ml = flag_ou = False
    if ml and mp("match", "1") is not None and mp("match", "2") is not None:
        p1, p2 = mp("match", "1"), mp("match", "2")
        info["p"].update({"1": round(ml["1"], 4), "2": round(ml["2"], 4)})
        info["model"].update({"1": p1, "2": p2})
        info["gap_1x2"] = round(max(abs(p1 - ml["1"]), abs(p2 - ml["2"])), 4)
        flag_ml = info["gap_1x2"] > GUARD_PP
    if ou and mp("total", "over", line) is not None:
        po = mp("total", "over", line)
        info["p"]["over"] = round(ou["over"], 4)
        info["model"]["over"] = po
        info["line"] = line
        info["gap_ou"] = round(abs(po - ou["over"]), 4)
        flag_ou = info["gap_ou"] > GUARD_PP
    for k in m["markets"]:
        if (flag_ml and k["scope"] in RESULT_SCOPES) or (flag_ou and k["scope"] == "total"):
            k["unreliable"] = True
    m["market_ref"] = info
    return flag_ml or flag_ou
