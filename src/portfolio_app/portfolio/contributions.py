"""Exact squared-gap contribution allocation with a uniform minimum buy."""

import numpy as np


def _project_cents(desired: np.ndarray, budget: int) -> np.ndarray:
    """Project onto a whole-cent, nonnegative simplex; preserve the budget."""
    if budget == 0:
        return np.zeros(len(desired))
    shifted = desired - desired.max()
    ordered = np.sort(shifted)[::-1]
    thresholds = (np.cumsum(ordered) - budget) / np.arange(1, len(ordered) + 1)
    active = np.flatnonzero(ordered > thresholds)
    exact = np.maximum(shifted - thresholds[active[-1]], 0)
    pennies = np.floor(exact)
    remainder = budget - int(pennies.sum())
    order = np.argsort(-(exact - pennies), kind="stable")
    pennies[order[:remainder]] += 1
    return pennies


def contribution_candidates(deficits: np.ndarray, budget: int, minimum: int, *,
                            buy_all: bool, max_trades: int) -> list[np.ndarray]:
    """Return improving cent allocations, ordered by trade count.

    Inputs are eligible-row deficits in cents and validated positive budgets.
    For exactly k buys, the k largest deficits are optimal: swapping a buy x
    from deficit d to a larger deficit D reduces squared error by 2*x*(D-d).
    After reserving the uniform minimum, simplex projection solves the remaining
    convex allocation. Enumerating k therefore also solves optional skipping and
    a trade cap, without an exponential subset search or a mixed-integer QP.
    """
    # Every candidate invests the same budget, so a common shift leaves the
    # objective ordering unchanged and improves precision for large holdings.
    deficits = deficits - deficits.max()
    count = len(deficits)
    order = np.argsort(-deficits, kind="stable")
    counts = [count] if buy_all else range(1, min(count, max_trades, budget // minimum) + 1)
    candidates = []
    best = float("inf")
    for k in counts:
        chosen = np.arange(count) if buy_all else np.sort(order[:k])
        pennies = np.zeros(count)
        pennies[chosen] = minimum + _project_cents(deficits[chosen] - minimum, budget - k * minimum)
        score = float(np.sum(pennies * (pennies - 2 * deficits)))
        if score < best:
            candidates.append(pennies)
            best = score
    return candidates
