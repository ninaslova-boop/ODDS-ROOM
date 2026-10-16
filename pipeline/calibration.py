"""Calibration of p_model per sport, BEFORE the blend with the market.

Samples: settled predictions (p_model before calibration, outcome 0/1), always with every outcome of a
market (1, X, 2; over AND under; home AND away). With only one side ("over", "home win") a high-scoring
week looks like "favourites are underrated", which is a bias of the sample, not of the model.
Sources: the walk-forward backtest (validate.py, thousands of out-of-sample predictions) and the
tracked live predictions of the pick log.

Fit (refit weekly by the "Validate models" workflow, data/calibration.json):
* samples are kept in time order and binned (p rounded to 0.01) into "train" (older 70 %) and
  "holdout" (newest 30 %),
* Platt scaling  q = 1 / (1 + exp(-(a + b * logit p)))  and isotonic regression (pool adjacent
  violators) are fitted on train,
* the method with the lower holdout Brier score is used, and only when it beats the raw p_model by
  MIN_GAIN; then it is refitted on all samples. Else: no calibration (identity) for that sport.
"""
from __future__ import annotations

import datetime as dt
import logging
import math

from .util import ROOT, read_json, write_json

log = logging.getLogger("pipeline.calibration")

FILE = ROOT / "data" / "calibration.json"
MIN_SAMPLES = 1000
MIN_GAIN = 0.0002
HOLDOUT = 0.3
EPS = 1e-4
_CACHE: dict | None = None


def _logit(p: float) -> float:
    p = min(max(p, EPS), 1 - EPS)
    return math.log(p / (1 - p))


def _key(p: float) -> str:
    return f"{min(max(p, 0.0), 1.0):.2f}"


def bin_samples(samples: list[tuple[float, int]]) -> dict[str, list[int]]:
    """{"0.53": [n, hits]}"""
    out: dict[str, list[int]] = {}
    for p, y in samples:
        b = out.setdefault(_key(p), [0, 0])
        b[0] += 1
        b[1] += int(y)
    return out


def _items(bins: dict[str, list[int]]) -> list[tuple[float, int, int]]:
    return sorted((float(k), v[0], v[1]) for k, v in bins.items() if v[0])


def brier(bins: dict[str, list[int]], f=lambda p: p) -> float | None:
    n = s = 0.0
    for p, cnt, hits in _items(bins):
        q = f(p)
        s += hits * (1 - q) ** 2 + (cnt - hits) * q ** 2
        n += cnt
    return s / n if n else None


def fit_platt(bins: dict[str, list[int]], l2: float = 1.0) -> tuple[float, float]:
    """Logistic regression of the outcome on logit(p) (Newton steps, small pull towards a = 0, b = 1)."""
    a, b = 0.0, 1.0
    it = _items(bins)
    for _ in range(60):
        ga = gb = haa = hab = hbb = 0.0
        for p, cnt, hits in it:
            x = _logit(p)
            q = 1 / (1 + math.exp(-(a + b * x)))
            r = cnt * q - hits
            w = cnt * q * (1 - q)
            ga += r; gb += r * x; haa += w; hab += w * x; hbb += w * x * x
        ga += l2 * a; gb += l2 * (b - 1); haa += l2; hbb += l2
        det = haa * hbb - hab * hab
        if det <= 0:
            break
        da, db = (hbb * ga - hab * gb) / det, (haa * gb - hab * ga) / det
        a, b = a - da, b - db
        if abs(da) + abs(db) < 1e-10:
            break
    return a, b


def fit_isotonic(bins: dict[str, list[int]]) -> list[list[float]]:
    """Pool adjacent violators on the bins. Returns knots [[p, value], ...] (increasing)."""
    blocks = [[p * cnt, cnt, hits] for p, cnt, hits in _items(bins)]      # [sum p, n, hits]
    merged: list[list[float]] = []
    for b in blocks:
        merged.append(list(b))
        while len(merged) > 1 and merged[-2][2] / merged[-2][1] >= merged[-1][2] / merged[-1][1]:
            x = merged.pop()
            merged[-1] = [merged[-1][0] + x[0], merged[-1][1] + x[1], merged[-1][2] + x[2]]
    return [[round(sp / n, 4), round(h / n, 4)] for sp, n, h in merged]


def platt_fn(a: float, b: float):
    return lambda p: 1 / (1 + math.exp(-(a + b * _logit(p))))


def iso_fn(knots: list[list[float]]):
    xs = [k[0] for k in knots]
    ys = [k[1] for k in knots]

    def f(p: float) -> float:
        if p <= xs[0]:
            v = ys[0]
        elif p >= xs[-1]:
            v = ys[-1]
        else:
            i = next(i for i in range(1, len(xs)) if xs[i] >= p)
            t = (p - xs[i - 1]) / (xs[i] - xs[i - 1]) if xs[i] > xs[i - 1] else 0
            v = ys[i - 1] + t * (ys[i] - ys[i - 1])
        return min(max(v, 0.01), 0.99)
    return f


def make_fn(entry: dict | None):
    if not entry or entry.get("method") in (None, "none"):
        return None
    if entry["method"] == "platt":
        return platt_fn(*entry["params"])
    return iso_fn(entry["knots"])


def fit_sport(samples: list[tuple[str, float, int]]) -> dict:
    """samples: (date, p_raw, outcome). Returns the entry for data/calibration.json."""
    samples = sorted(samples)
    n = len(samples)
    if n < MIN_SAMPLES:
        return {"method": "none", "n": n, "reason": f"premalo vzorcev ({n} < {MIN_SAMPLES})"}
    k = int(n * (1 - HOLDOUT))
    train = bin_samples([(p, y) for _, p, y in samples[:k]])
    hold = bin_samples([(p, y) for _, p, y in samples[k:]])
    raw = brier(hold)
    pl = fit_platt(train)
    iso = fit_isotonic(train)
    scores = {"platt": brier(hold, platt_fn(*pl)), "isotonic": brier(hold, iso_fn(iso))}
    best = min(scores, key=scores.get)
    entry = {"n": n, "train": train, "holdout": hold, "holdout_brier": {"raw": round(raw, 5),
             **{k2: round(v, 5) for k2, v in scores.items()}}}
    if scores[best] > raw - MIN_GAIN:
        return {**entry, "method": "none", "reason": "kalibracija na izločenih podatkih ne zmanjša Brier"}
    allb = bin_samples([(p, y) for _, p, y in samples])
    if best == "platt":
        a, b = fit_platt(allb)
        return {**entry, "method": "platt", "params": [round(a, 5), round(b, 5)]}
    return {**entry, "method": "isotonic", "knots": fit_isotonic(allb)}


def fit_all(by_sport: dict[str, list[tuple[str, float, int]]], today: dt.date) -> dict:
    old = read_json(FILE, {}) or {}
    sports = dict(old.get("sports") or {})
    sports.update({sp: fit_sport(s) for sp, s in by_sport.items()})
    out = {"fitted": today.isoformat(), "sports": sports}
    write_json(FILE, out, compact=True)
    for sp, e in out["sports"].items():
        log.info("calibration %s: %s (n=%s, holdout %s)", sp, e["method"], e["n"], e.get("holdout_brier"))
    return out


def load() -> dict:
    global _CACHE
    if _CACHE is None:
        _CACHE = read_json(FILE, {}) or {}
    return _CACHE


def function(sport: str):
    """Calibration function of a sport, or None (no calibration)."""
    return make_fn((load().get("sports") or {}).get(sport))


def summary() -> dict:
    """Short status per sport for meta.json / the export."""
    out = {}
    for sp, e in (load().get("sports") or {}).items():
        hb = e.get("holdout_brier") or {}
        out[sp] = {"method": e.get("method"), "n": e.get("n"), "raw": hb.get("raw"),
                   "cal": hb.get(e.get("method")) if e.get("method") not in (None, "none") else None,
                   "reason": e.get("reason")}
    return {"fitted": load().get("fitted"), "sports": out}


def tracked_samples(tracked: list[list]) -> dict[str, list[tuple[str, float, int]]]:
    """Live settled predictions with every outcome of the market. The raw p_model is t[5] (stored since the
    calibration step); older entries (logged before the market shrink, 16 Oct 2026 12:00) used t[2]."""
    out: dict[str, list] = {}
    for t in tracked:
        if t[3] not in (0, 1):
            continue
        p = t[5] if len(t) > 5 and t[5] is not None else (t[2] if t[4] < "2026-10-16T12:00" else None)
        if p is None:
            continue
        scope = t[0].split("|")[2]
        rows = out.setdefault(t[1], [])
        rows.append((t[4][:10], p, t[3]))
        if scope != "match" or t[1] != "football":       # binary markets: add the other side
            rows.append((t[4][:10], 1 - p, 1 - t[3]))
    return out
