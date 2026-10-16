"""Statistical models.

* Poisson team-strength model (attack / defence / home advantage) fitted with
  ridge-penalised IRLS and exponential time decay. Used for football goals,
  corners, cards, shots on target and for NHL goals.
* Dixon-Coles low-score correction for football goals.
* Gaussian team-strength model (offence / defence / home court) fitted with
  weighted ridge least squares. Used for basketball points.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

import numpy as np


# --------------------------------------------------------------------------
# Poisson ratings
# --------------------------------------------------------------------------
@dataclass
class PoissonRatings:
    teams: list[str]
    mu: float
    home: float
    att: dict[str, float]
    dfn: dict[str, float]          # positive = concedes more
    n_obs: int = 0
    league_avg: float = 0.0        # average count per team per game
    prior: dict[str, tuple[float, float]] = field(default_factory=dict)

    def expected(self, home: str, away: str, neutral: bool = False) -> tuple[float, float]:
        ah, dh = self.att.get(home, self.prior.get(home, (0.0, 0.0))[0]), self.dfn.get(home, self.prior.get(home, (0.0, 0.0))[1])
        aa, da = self.att.get(away, self.prior.get(away, (0.0, 0.0))[0]), self.dfn.get(away, self.prior.get(away, (0.0, 0.0))[1])
        hadv = 0.0 if neutral else self.home
        lh = math.exp(self.mu + hadv + ah + da)
        la = math.exp(self.mu + aa + dh)
        return lh, la

    def table(self) -> list[dict]:
        rows = [{"team": t, "att": round(math.exp(self.att[t]), 3), "def": round(math.exp(self.dfn[t]), 3)}
                for t in self.teams]
        return sorted(rows, key=lambda r: r["att"] / r["def"], reverse=True)


def time_weight(d: dt.date, ref: dt.date, half_life_days: float) -> float:
    age = max(0, (ref - d).days)
    return 0.5 ** (age / half_life_days)


def fit_poisson(games: list[dict], ref_date: dt.date, half_life_days: float = 150.0,
                ridge: float = 4.0, prior: dict[str, tuple[float, float]] | None = None,
                iters: int = 60, tol: float = 1e-7) -> PoissonRatings | None:
    """games: [{home, away, hc, ac, date: date, w: extra weight (optional)}]
    hc/ac = counts for the home / away team (goals, corners, ...).

    log E[hc] = mu + home + att[home] + def[away]
    log E[ac] = mu        + att[away] + def[home]
    Games with "neutral": True get no home term.
    """
    games = [g for g in games if g.get("hc") is not None and g.get("ac") is not None]
    if len(games) < 10:
        return None
    prior = prior or {}
    teams = sorted({g["home"] for g in games} | {g["away"] for g in games})
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    p = 2 + 2 * n
    dummy = p                      # index of a zero column, used to pad rows with fewer entries
    cols, y, w = [], [], []
    for g in games:
        wt = time_weight(g["date"], ref_date, half_life_days) * g.get("w", 1.0)
        if wt < 1e-4:
            continue
        hi, ai = idx[g["home"]], idx[g["away"]]
        hcol = dummy if g.get("neutral") else 1
        cols.append((0, hcol, 2 + hi, 2 + n + ai))
        cols.append((0, dummy, 2 + ai, 2 + n + hi))
        y += [float(g["hc"]), float(g["ac"])]
        w += [wt, wt]
    if len(cols) < 20:
        return None
    C = np.array(cols, dtype=np.int64); y = np.array(y); w = np.array(w)
    # every (row, column) pair of the sparse design matrix, for X^T W X
    ci = np.repeat(C, C.shape[1], axis=1).ravel()
    cj = np.tile(C, (1, C.shape[1])).ravel()

    # ridge pulls att/def towards the prior mean (0 for unknown teams)
    pen = np.full(p, ridge); pen[:2] = 1e-6
    prior_vec = np.zeros(p)
    for t, (a, d) in prior.items():
        if t in idx:
            prior_vec[2 + idx[t]] = a
            prior_vec[2 + n + idx[t]] = d

    beta = np.zeros(p + 1)         # last entry stays 0 (dummy column)
    beta[0] = math.log(max(np.average(y, weights=w), 0.05))
    for _ in range(iters):
        eta = np.clip(beta[C].sum(axis=1), -10, 10)
        lam = np.exp(eta)
        z = eta + (y - lam) / lam
        W = w * lam
        A = np.zeros((p + 1, p + 1))
        np.add.at(A, (ci, cj), np.repeat(W, C.shape[1] ** 2))
        b = np.zeros(p + 1)
        np.add.at(b, C.ravel(), np.repeat(W * z, C.shape[1]))
        A = A[:p, :p] + np.diag(pen)
        b = b[:p] + pen * prior_vec
        new = np.linalg.solve(A, b)
        done = np.max(np.abs(new - beta[:p])) < tol
        beta[:p] = new
        if done:
            break
    beta = beta[:p]

    # identifiability: centre att and def, move the mean into mu
    att = beta[2:2 + n]; dfn = beta[2 + n:]
    ca, cd = att.mean(), dfn.mean()
    att = att - ca; dfn = dfn - cd
    mu = beta[0] + ca + cd
    return PoissonRatings(
        teams=teams, mu=float(mu), home=float(beta[1]),
        att={t: float(att[i]) for t, i in idx.items()},
        dfn={t: float(dfn[i]) for t, i in idx.items()},
        n_obs=len(games), league_avg=float(np.average(y, weights=w)), prior=prior,
    )


# --------------------------------------------------------------------------
# Score matrices and markets
# --------------------------------------------------------------------------
def poisson_pmf(lam: float, kmax: int) -> np.ndarray:
    k = np.arange(kmax + 1)
    logp = k * math.log(max(lam, 1e-9)) - lam - np.array([math.lgamma(i + 1) for i in k])
    return np.exp(logp)


def poisson_sf(lam: float, line: float) -> float:
    """P(X > line) for half lines (e.g. 2.5)."""
    k = int(math.floor(line))
    cdf = sum(math.exp(i * math.log(max(lam, 1e-12)) - lam - math.lgamma(i + 1)) for i in range(k + 1))
    return max(0.0, min(1.0, 1 - cdf))


def score_matrix(lh: float, la: float, kmax: int = 10, rho: float = 0.0) -> np.ndarray:
    M = np.outer(poisson_pmf(lh, kmax), poisson_pmf(la, kmax))
    if rho:
        # Dixon-Coles tau for 0-0, 1-0, 0-1, 1-1
        M[0, 0] *= max(0.0, 1 - lh * la * rho)
        M[0, 1] *= max(0.0, 1 + lh * rho)
        M[1, 0] *= max(0.0, 1 + la * rho)
        M[1, 1] *= max(0.0, 1 - rho)
    return M / M.sum()


def mk(stat, scope, kind, line, p, label, group) -> dict:
    return {"stat": stat, "scope": scope, "kind": kind, "line": line,
            "p": round(float(p), 4), "label": label, "group": group}


def goal_markets(M: np.ndarray, stat: str = "goals", unit: str = "golov",
                 total_lines=(0.5, 1.5, 2.5, 3.5, 4.5, 5.5), team_lines=(0.5, 1.5, 2.5, 3.5),
                 draw_possible: bool = True, hockey: bool = False) -> list[dict]:
    """Markets from a joint score matrix M[h, a]."""
    k = M.shape[0]
    hg = np.arange(k)[:, None]; ag = np.arange(k)[None, :]
    tot = hg + ag
    diff = hg - ag
    out = []
    p_home = float(M[diff > 0].sum()); p_draw = float(M[diff == 0].sum()); p_away = float(M[diff < 0].sum())
    if hockey:
        # regulation draw goes to OT/SO: small home edge, one extra goal for the winner
        ot_home = 0.52
        out.append(mk(stat, "match", "1", None, p_home + p_draw * ot_home, "Zmaga domačih (vklj. OT)", "Izid"))
        out.append(mk(stat, "match", "2", None, p_away + p_draw * (1 - ot_home), "Zmaga gostov (vklj. OT)", "Izid"))
        out.append(mk(stat, "match", "X", None, p_draw, "Remi po 60 min", "Izid"))
        out.append(mk(stat, "spread", "home", -1.5, float(M[diff >= 2].sum()), "Domači -1.5", "Hendikep"))
        out.append(mk(stat, "spread", "away", -1.5, float(M[diff <= -2].sum()), "Gostje -1.5", "Hendikep"))
        out.append(mk(stat, "spread", "home", 1.5, 1 - float(M[diff <= -2].sum()), "Domači +1.5", "Hendikep"))
        out.append(mk(stat, "spread", "away", 1.5, 1 - float(M[diff >= 2].sum()), "Gostje +1.5", "Hendikep"))
        # total including the OT/SO decider goal
        tot_ot = tot + (diff == 0)
        for L in total_lines:
            po = float(M[tot_ot > L].sum())
            out.append(mk(stat, "total", "over", L, po, f"Skupaj {unit} več kot {L}", "Skupaj"))
            out.append(mk(stat, "total", "under", L, 1 - po, f"Skupaj {unit} manj kot {L}", "Skupaj"))
    else:
        out.append(mk(stat, "match", "1", None, p_home, "Zmaga domačih (1)", "Izid"))
        if draw_possible:
            out.append(mk(stat, "match", "X", None, p_draw, "Remi (X)", "Izid"))
        out.append(mk(stat, "match", "2", None, p_away, "Zmaga gostov (2)", "Izid"))
        out.append(mk(stat, "match", "1X", None, p_home + p_draw, "Dvojna 1X", "Izid"))
        out.append(mk(stat, "match", "X2", None, p_away + p_draw, "Dvojna X2", "Izid"))
        out.append(mk(stat, "match", "12", None, p_home + p_away, "Dvojna 12", "Izid"))
        nd = p_home + p_away
        if nd > 0:
            out.append(mk(stat, "dnb", "home", None, p_home / nd, "Domači brez remija", "Izid"))
            out.append(mk(stat, "dnb", "away", None, p_away / nd, "Gostje brez remija", "Izid"))
        for L in total_lines:
            po = float(M[tot > L].sum())
            out.append(mk(stat, "total", "over", L, po, f"Skupaj več kot {L}", "Skupni goli"))
            out.append(mk(stat, "total", "under", L, 1 - po, f"Skupaj manj kot {L}", "Skupni goli"))
        btts = float(M[1:, 1:].sum())
        out.append(mk(stat, "btts", "yes", None, btts, "Oba dasta gol (BTTS da)", "BTTS"))
        out.append(mk(stat, "btts", "no", None, 1 - btts, "Ne dasta oba (BTTS ne)", "BTTS"))
        out.append(mk(stat, "btts_over", "yes", 2.5, float(M[(hg > 0) & (ag > 0) & (tot > 2.5)].sum()),
                      "BTTS + več kot 2.5", "BTTS"))
        for L in team_lines:
            ph = float(M[hg[:, 0] > L, :].sum())
            pa = float(M[:, ag[0] > L].sum())
            out.append(mk(stat, "home", "over", L, ph, f"Domači več kot {L}", "Goli ekip"))
            out.append(mk(stat, "away", "over", L, pa, f"Gostje več kot {L}", "Goli ekip"))
            if L == 0.5:
                out.append(mk(stat, "home", "under", L, 1 - ph, "Domači brez gola", "Goli ekip"))
                out.append(mk(stat, "away", "under", L, 1 - pa, "Gostje brez gola", "Goli ekip"))
        out.append(mk(stat, "spread", "home", -1.5, float(M[diff >= 2].sum()), "Domači zmaga z 2+", "Hendikep"))
        out.append(mk(stat, "spread", "away", -1.5, float(M[diff <= -2].sum()), "Gostje zmagajo z 2+", "Hendikep"))
    return out


def total_distribution(M: np.ndarray, kmax: int = 8) -> list[dict]:
    k = M.shape[0]
    tot = np.add.outer(np.arange(k), np.arange(k))
    return [{"n": n, "p": round(float(M[tot == n].sum()), 4)} for n in range(kmax + 1)]


def top_scores(M: np.ndarray, n: int = 6) -> list[dict]:
    flat = [((i, j), M[i, j]) for i in range(M.shape[0]) for j in range(M.shape[1])]
    flat.sort(key=lambda x: -x[1])
    return [{"s": f"{i}-{j}", "p": round(float(p), 4)} for (i, j), p in flat[:n]]


def count_markets(lh: float, la: float, stat: str, group: str, unit: str,
                  total_lines, team_lines=()) -> list[dict]:
    """Independent Poisson totals for corners / cards / shots on target."""
    out = []
    for L in total_lines:
        po = poisson_sf(lh + la, L)
        out.append(mk(stat, "total", "over", L, po, f"{unit} skupaj več kot {L}", group))
        out.append(mk(stat, "total", "under", L, 1 - po, f"{unit} skupaj manj kot {L}", group))
    for L in team_lines:
        out.append(mk(stat, "home", "over", L, poisson_sf(lh, L), f"{unit} domači več kot {L}", group))
        out.append(mk(stat, "away", "over", L, poisson_sf(la, L), f"{unit} gostje več kot {L}", group))
    return out


# --------------------------------------------------------------------------
# Gaussian (basketball) ratings
# --------------------------------------------------------------------------
def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


@dataclass
class GaussRatings:
    teams: list[str]
    mu: float
    home: float
    off: dict[str, float]
    dfn: dict[str, float]     # positive = concedes more
    sd_total: float
    sd_margin: float
    n_obs: int = 0

    def expected(self, home: str, away: str) -> tuple[float, float]:
        ph = self.mu + self.home / 2 + self.off.get(home, 0) + self.dfn.get(away, 0)
        pa = self.mu - self.home / 2 + self.off.get(away, 0) + self.dfn.get(home, 0)
        return ph, pa

    def table(self) -> list[dict]:
        rows = [{"team": t, "off": round(self.mu + self.off[t], 1), "def": round(self.mu + self.dfn[t], 1)}
                for t in self.teams]
        return sorted(rows, key=lambda r: r["off"] - r["def"], reverse=True)


def fit_gauss(games: list[dict], ref_date: dt.date, half_life_days: float = 60.0,
              ridge: float = 3.0) -> GaussRatings | None:
    games = [g for g in games if g.get("hc") is not None and g.get("ac") is not None]
    if len(games) < 20:
        return None
    teams = sorted({g["home"] for g in games} | {g["away"] for g in games})
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    p = 2 + 2 * n
    rows, y, w = [], [], []
    for g in games:
        wt = time_weight(g["date"], ref_date, half_life_days) * g.get("w", 1.0)
        hi, ai = idx[g["home"]], idx[g["away"]]
        r1 = np.zeros(p); r1[0] = 1; r1[1] = 0.5; r1[2 + hi] = 1; r1[2 + n + ai] = 1
        r2 = np.zeros(p); r2[0] = 1; r2[1] = -0.5; r2[2 + ai] = 1; r2[2 + n + hi] = 1
        rows += [r1, r2]; y += [g["hc"], g["ac"]]; w += [wt, wt]
    X = np.vstack(rows); y = np.array(y, float); w = np.array(w)
    pen = np.full(p, ridge); pen[:2] = 1e-6
    beta = np.linalg.solve(X.T @ (X * w[:, None]) + np.diag(pen), X.T @ (w * y))
    off = beta[2:2 + n] - beta[2:2 + n].mean()
    dfn = beta[2 + n:] - beta[2 + n:].mean()
    mu = beta[0] + beta[2:2 + n].mean() + beta[2 + n:].mean()
    r = GaussRatings(teams, float(mu), float(beta[1]), {t: float(off[i]) for t, i in idx.items()},
                     {t: float(dfn[i]) for t, i in idx.items()}, 18.0, 12.5, len(games))
    # residual spread of totals and margins (weighted)
    tr, mr, ws = [], [], []
    for g in games:
        ph, pa = r.expected(g["home"], g["away"])
        tr.append((g["hc"] + g["ac"]) - (ph + pa))
        mr.append((g["hc"] - g["ac"]) - (ph - pa))
        ws.append(time_weight(g["date"], ref_date, half_life_days * 3))
    ws = np.array(ws)
    r.sd_total = float(max(10.0, math.sqrt(np.average(np.square(tr), weights=ws))))
    r.sd_margin = float(max(8.0, math.sqrt(np.average(np.square(mr), weights=ws))))
    return r


def basketball_markets(ph: float, pa: float, sd_total: float, sd_margin: float,
                       total_line: float | None = None, spread_line: float | None = None) -> list[dict]:
    out = []
    m = ph - pa
    t = ph + pa
    p_home = norm_cdf(m / sd_margin)
    out.append(mk("points", "match", "1", None, p_home, "Zmaga domačih", "Izid"))
    out.append(mk("points", "match", "2", None, 1 - p_home, "Zmaga gostov", "Izid"))
    base = total_line if total_line else round(t) + 0.5
    for L in sorted({base - 10, base - 5, base, base + 5, base + 10}):
        po = 1 - norm_cdf((L - t) / sd_total)
        out.append(mk("points", "total", "over", L, po, f"Skupaj več kot {L:g}", "Skupne točke"))
        out.append(mk("points", "total", "under", L, 1 - po, f"Skupaj manj kot {L:g}", "Skupne točke"))
    sl = spread_line if spread_line is not None else -round(m * 2) / 2
    if sl == int(sl):
        sl -= 0.5
    # home covers sl (e.g. -5.5) when margin > 5.5
    pc = 1 - norm_cdf((-sl - m) / sd_margin)
    out.append(mk("points", "spread", "home", sl, pc, f"Domači {sl:+g}", "Hendikep"))
    out.append(mk("points", "spread", "away", -sl, 1 - pc, f"Gostje {-sl:+g}", "Hendikep"))
    sd_team = sd_total / math.sqrt(2) * 0.95
    for side, val in (("home", ph), ("away", pa)):
        L = round(val) + 0.5
        po = 1 - norm_cdf((L - val) / sd_team)
        nm = "Domači" if side == "home" else "Gostje"
        out.append(mk("points", side, "over", L, po, f"{nm} več kot {L:g}", "Točke ekip"))
        out.append(mk("points", side, "under", L, 1 - po, f"{nm} manj kot {L:g}", "Točke ekip"))
    return out


# --------------------------------------------------------------------------
# Settlement of a market against a final result
# --------------------------------------------------------------------------
def settle(market: dict, h: float, a: float, hockey: bool = False) -> int | None:
    """Return 1 (won), 0 (lost) or None (push / not settleable).
    h, a = final values of the market's stat for home / away (goals, corners, ...).
    For hockey the final score includes the OT / shoot-out goal."""
    scope, kind, line = market["scope"], market["kind"], market.get("line")
    if scope == "match":
        if hockey and kind == "X":
            return None  # regulation draw: not known from the final score alone
        res = "1" if h > a else ("2" if h < a else "X")
        return int(res in kind)
    if scope == "dnb":
        if h == a:
            return None
        return int((h > a) == (kind == "home"))
    if scope == "total":
        t = h + a
        if t == line:
            return None
        return int((t > line) == (kind == "over"))
    if scope in ("home", "away"):
        v = h if scope == "home" else a
        if v == line:
            return None
        return int((v > line) == (kind == "over"))
    if scope == "btts":
        both = h > 0 and a > 0
        return int(both == (kind == "yes"))
    if scope == "btts_over":
        return int(h > 0 and a > 0 and h + a > line)
    if scope == "spread":
        diff = (h - a) if kind == "home" else (a - h)
        v = diff + line
        if v == 0:
            return None
        return int(v > 0)
    return None


def attach_b365(markets: list[dict], b365: dict | None):
    """bet365 prices on the markets: b365 = {"1", "X", "2", "over2.5", "under2.5", "tot": [[side, line, price]]}.
    Only real bet365 prices (football-data.co.uk B365 columns or The Odds API "bet365"), never an average."""
    if not b365:
        return
    tot = {(s, float(l)): p for s, l, p in b365.get("tot", [])}
    for m in markets:
        price = None
        if m["scope"] == "match" and m["kind"] in ("1", "X", "2"):
            price = b365.get(m["kind"])
        elif m["scope"] == "total" and m.get("line") is not None:
            price = tot.get((m["kind"], float(m["line"]))) or (b365.get(f"{m['kind']}2.5") if m["line"] == 2.5 else None)
        if price and price > 1:
            m["b365"] = round(price, 3)
