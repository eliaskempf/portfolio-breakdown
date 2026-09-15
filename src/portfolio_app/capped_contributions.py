"""Bounded whole-cent buys; isolate cap-specific optimization from the UI."""

from time import monotonic

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from portfolio_app.holdings import DataError


def bounded_projection(desired, budget, lower, upper):
    """Euclidean projection onto a box and fixed sum, rounded optimally to cents."""
    if budget == int(np.sum(lower)):
        return lower.copy()
    if budget == int(np.sum(upper)):
        return upper.copy()
    shifted = desired - np.max(desired)
    lo, hi = np.min(shifted - upper), np.max(shifted - lower)
    for _ in range(100):
        mid = (lo + hi) / 2
        if np.clip(shifted - mid, lower, upper).sum() > budget:
            lo = mid
        else:
            hi = mid
    exact = np.clip(shifted - (lo + hi) / 2, lower, upper)
    pennies = np.floor(exact)
    remainder = budget - int(pennies.sum())
    order = np.argsort(np.where(pennies < upper, -(exact - pennies), np.inf), kind="stable")
    pennies[order[:remainder]] += 1
    if remainder < 0 or remainder > len(order) or int(pennies.sum()) != budget or (pennies > upper).any():
        raise DataError("Could not round capped purchases safely; no plan is shown.")
    return pennies


def _best_capped_buys(deficits, budget, minimum, upper, limit, deadline):
    """Outer approximation of a separable integer quadratic with binary buys.

    Integer secants support x² at adjacent cent amounts. Every MILP supplies a
    global lower bound; only return after closing the objective gap. Reprojecting
    each proposed support gives an accurate feasible incumbent and useful cuts.
    Objective units are EUR²; there is no dependency on a quadratic solver.
    """
    n = len(deficits)
    rows, lb, ub = [], [], []

    def add(coefficients, low, high):
        row = np.zeros(3 * n)
        for i, value in coefficients.items():
            row[i] = value
        rows.append(row)
        lb.append(low)
        ub.append(high)

    add({i: 1 for i in range(n)}, budget, budget)
    add({n+i: 1 for i in range(n)}, 0, limit)
    for i in range(n):
        add({i: 1, n+i: -upper[i]}, -np.inf, 0)
        add({i: 1, n+i: -minimum}, 0, np.inf)
    cuts = set()

    def cut(point):
        for i, value in enumerate(point):
            p = int(value)
            if (i, p) not in cuts:
                add({i: (2*p+1)/10000, 2*n+i: -1}, -np.inf, p*(p+1)/10000)
                cuts.add((i, p))

    cut(np.zeros(n))
    cut(upper)
    cut(bounded_projection(deficits, budget, np.zeros(n), upper))
    c = np.r_[-2*deficits/10000, np.zeros(n), np.ones(n)]
    bounds = Bounds(np.zeros(3*n), np.r_[upper, (upper >= minimum).astype(float), np.full(n, np.inf)])
    integrality = np.r_[np.ones(2*n), np.zeros(n)]
    best, best_score = None, float("inf")
    while monotonic() < deadline:
        result = milp(c, integrality=integrality, bounds=bounds,
                      constraints=LinearConstraint(np.asarray(rows), lb, ub),
                      options={"time_limit": max(.001, deadline-monotonic()), "mip_rel_gap": 0.})
        if result.status != 0 or result.x is None:
            break
        support = result.x[n:2*n] > .5
        candidate = np.zeros(n)
        candidate[support] = bounded_projection(deficits[support], budget, np.full(int(support.sum()), minimum), upper[support])
        score = float(np.sum(candidate * (candidate - 2*deficits)) / 10000)
        if score < best_score:
            best, best_score = candidate, score
        if best_score - result.fun <= max(1e-7, abs(best_score)*1e-10):
            return best
        cut(np.rint(result.x[:n]))
        cut(candidate)
    raise DataError("The capped optimization did not prove an optimum within its time limit. No trade plan is shown; try fewer selected positions or turn off Prefer fewer trades.")


def capped_candidates(deficits, budget, minimum, upper, *, buy_all, max_trades):
    """Invest as much as possible, then minimize squared gaps for each trade cap."""
    if buy_all:
        invested = min(budget, int(upper.sum()))
        return [bounded_projection(deficits, invested, np.full(len(upper), minimum), upper)]
    upper = np.where(upper >= minimum, upper, 0)
    count = min(max_trades, int(np.count_nonzero(upper)), budget // minimum)
    if count == 0:
        return [np.zeros(len(upper))]
    deadline = monotonic() + 10
    plans = []
    best_invested, best_score = -1, float("inf")
    for limit in range(1, count + 1):
        invested = min(budget, int(np.sort(upper)[-limit:].sum()))
        if invested == int(upper.sum()):
            candidate = upper.copy()
        else:
            candidate = _best_capped_buys(deficits, invested, minimum, upper, limit, deadline)
        score = float(np.sum((candidate - deficits)**2))
        if invested > best_invested or score < best_score:
            plans.append(candidate)
            best_invested, best_score = invested, score
    return plans
