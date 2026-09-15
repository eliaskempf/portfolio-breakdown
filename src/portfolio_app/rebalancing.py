"""Target-band rebalancing with proven minimum trade/cash objectives."""

from collections.abc import Collection
from dataclasses import dataclass
import math
from typing import Callable, Literal

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, linprog, milp

from portfolio_app.contributions import contribution_candidates
from portfolio_app.holdings import DataError

SOLVER_TOL = 2e-6


class RebalanceError(DataError):
    pass


def ignore_empty_positions(holdings: pd.DataFrame) -> pd.DataFrame:
    """Redistribute removed targets equally per remaining asset, then account row."""
    kept = holdings.loc[holdings["shares"] > 0].copy()
    removed = holdings.loc[holdings["shares"] == 0]
    if removed.empty or kept.empty or "target_allocation" not in holdings or holdings.target_allocation.isna().all():
        return kept
    if holdings.target_allocation.isna().any():
        raise RebalanceError("Set targets for every position before redistributing empty-position targets; missing targets are unknown.")
    increment = float(removed.target_allocation.sum()) / kept.id.nunique()
    counts = kept.groupby("id")["id"].transform("size")
    kept["target_allocation"] += increment / counts
    return kept


@dataclass(frozen=True)
class RebalanceInput:
    positions: pd.DataFrame
    values: np.ndarray
    targets: np.ndarray
    lower: np.ndarray
    upper: np.ndarray
    held: np.ndarray

    @property
    def total(self) -> float:
        return math.fsum(self.values)


def prepare_rebalance(valued: pd.DataFrame, *, tolerance: float = .5,
                      tolerance_type: Literal["pp", "relative"] = "pp") -> RebalanceInput:
    if valued.empty:
        raise RebalanceError("Add at least one position before calculating a rebalance.")
    if len(valued) > 100:
        raise RebalanceError("The interactive calculator supports up to 100 position rows.")
    if "target_allocation" not in valued or valued.target_allocation.isna().any():
        raise RebalanceError("Set a target for every position, including explicit zero targets, before calculating.")
    values = valued.current_value_eur.to_numpy(dtype=float)
    targets = valued.target_allocation.to_numpy(dtype=float)
    shares = valued.shares.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values < 0).any():
        raise RebalanceError("Every held position needs a finite, nonnegative EUR valuation.")
    if not np.isfinite(shares).all() or (shares < 0).any():
        raise RebalanceError("Share quantities must be finite and nonnegative.")
    if not np.isfinite(targets).all() or ((targets < 0) | (targets > 1)).any():
        raise RebalanceError("Targets must be finite fractions between zero and one.")
    if not math.isclose(math.fsum(targets), 1., abs_tol=1e-9, rel_tol=0):
        raise RebalanceError(f"Position targets must total 100% (currently {100 * math.fsum(targets):.2f}%). Targets are not automatically normalized.")
    if tolerance_type not in {"pp", "relative"} or not math.isfinite(tolerance) or not 0 <= tolerance <= 100:
        raise RebalanceError("Choose a finite tolerance between 0 and 100, in percentage points or relative percent.")
    margin = tolerance / 100 * (targets if tolerance_type == "relative" else np.ones(len(targets)))
    return RebalanceInput(valued.reset_index(drop=True).copy(), values, targets,
                          np.maximum(0, targets - margin), np.minimum(1, targets + margin), shares > 0)


def deviation(weights: np.ndarray, lower: np.ndarray, upper: np.ndarray) -> float:
    """Sum of all distances outside the allowed bands, in percentage points."""
    return float(100 * np.sum(np.maximum(lower - weights, 0) + np.maximum(weights - upper, 0)))


@dataclass
class RebalancePlan:
    table: pd.DataFrame
    new_money: float
    trade_count: int
    buy_count: int
    sell_count: int
    deviation_before: float
    deviation_after: float
    within_bands: bool

    @property
    def target_rms(self) -> float:
        """Root mean squared target gap across all position rows, in pp."""
        return float(np.sqrt(np.mean(self.table["Gap (pp)"].to_numpy() ** 2)))


def _result(problem: RebalanceInput, weights: np.ndarray, new_money: float, *, buys_only: bool,
            no_new_positions: bool, require_bands: bool, trade_amounts: np.ndarray | None = None) -> RebalancePlan:
    total = problem.total + new_money
    current = problem.values / total
    if (not np.isfinite(weights).all() or abs(weights.sum() - 1) > SOLVER_TOL or
            (weights < -SOLVER_TOL).any() or
            (buys_only and (weights < current - SOLVER_TOL).any()) or
            (no_new_positions and (weights[~problem.held] > current[~problem.held] + SOLVER_TOL).any())):
        raise RebalanceError("The solver result failed the portfolio constraints; no plan is shown.")
    within = bool(((weights >= problem.lower - SOLVER_TOL) & (weights <= problem.upper + SOLVER_TOL)).all())
    if require_bands and not within:
        raise RebalanceError("The solver could not verify a plan inside every target range.")
    delta = (weights - current) * total if trade_amounts is None else trade_amounts.copy()
    if trade_amounts is None:
        # Numerical solver noise is not a trade. Explicit cent allocations must
        # retain their amounts even when tiny compared with the portfolio.
        delta[np.abs(delta) < total * 1e-9] = 0.
    table = problem.positions[[column for column in ("position_id", "id", "name", "ticker", "account", "portfolio")
                               if column in problem.positions]].copy()
    table["Action"] = np.where(delta > 0, "Buy", np.where(delta < 0, "Sell", "Hold"))
    table["Trade (EUR)"] = delta
    table["Current (EUR)"] = problem.values
    table["After (EUR)"] = problem.values + delta
    table["Current %"] = 100 * problem.values / problem.total if problem.total else np.nan
    table["After %"] = 100 * weights
    table["Target %"] = 100 * problem.targets
    table["Lower %"], table["Upper %"] = 100 * problem.lower, 100 * problem.upper
    table["Gap (pp)"] = 100 * (weights - problem.targets)
    return RebalancePlan(table, new_money, int(np.count_nonzero(delta)), int(np.count_nonzero(delta > 0)),
                         int(np.count_nonzero(delta < 0)),
                         deviation(problem.values / problem.total, problem.lower, problem.upper) if problem.total else float("nan"),
                         deviation(weights, problem.lower, problem.upper), within)


class _AllocationModel:
    """Final weights, trade indicators and absolute-deviation auxiliary variables."""

    def __init__(self, problem: RebalanceInput, total: float, *, buys_only: bool, no_new: bool,
                 hard_bands: bool, max_trades: int, buy_allowed: np.ndarray | None = None):
        n = len(problem.values)
        self.n = n
        # x: final weight, z: trade, q: turnover, e: outside-band error, r: target distance
        self.x, self.z, self.q, self.e, self.r = (slice(i*n, (i+1)*n) for i in range(5))
        self.lower = np.zeros(5*n)
        self.upper = np.full(5*n, np.inf)
        self.upper[self.x] = 1
        self.upper[self.z] = 1
        current = problem.values / total
        if hard_bands:
            self.lower[self.x], self.upper[self.x] = problem.lower, problem.upper
        if buys_only:
            self.lower[self.x] = np.maximum(self.lower[self.x], current)
        if buy_allowed is not None:
            self.upper[:n][~buy_allowed] = current[~buy_allowed]
        if no_new:
            self.upper[:n][~problem.held] = np.minimum(self.upper[:n][~problem.held], current[~problem.held])
        if (self.lower > self.upper + 1e-12).any():
            raise RebalanceError("No feasible plan under these ranges and position restrictions. Widen the ranges or allow new positions.")
        self.upper = np.maximum(self.upper, self.lower)
        self.integrality = np.zeros(5*n)
        self.integrality[self.z] = 1
        self.rows, self.lb, self.ub = [], [], []
        self.add({i: 1 for i in range(n)}, 1, 1)
        self.add({n+i: 1 for i in range(n)}, -np.inf, max_trades)
        for i in range(n):
            self.add({i: 1, n+i: -(1-current[i])}, -np.inf, current[i])
            self.add({i: -1, n+i: -current[i]}, -np.inf, -current[i])
            self.add({i: 1, 2*n+i: -1}, -np.inf, current[i])
            self.add({i: -1, 2*n+i: -1}, -np.inf, -current[i])
            self.add({i: -1, 3*n+i: -1}, -np.inf, -problem.lower[i])
            self.add({i: 1, 3*n+i: -1}, -np.inf, problem.upper[i])
            self.add({i: -1, 4*n+i: -1}, -np.inf, -problem.targets[i])
            self.add({i: 1, 4*n+i: -1}, -np.inf, problem.targets[i])

    def add(self, coefficients: dict[int, float], lower: float, upper: float) -> None:
        row = np.zeros(5*self.n)
        for index, value in coefficients.items():
            row[index] = value
        self.rows.append(row)
        self.lb.append(lower)
        self.ub.append(upper)

    def solve(self, objective: slice):
        c = np.zeros(5*self.n)
        c[objective] = 1
        result = milp(c, integrality=self.integrality, bounds=Bounds(self.lower, self.upper),
                      constraints=LinearConstraint(np.asarray(self.rows), self.lb, self.ub),
                      options={"time_limit": 10., "mip_rel_gap": 0.})
        if result.status == 2:
            raise RebalanceError("No feasible plan under these ranges and restrictions. Try more trades, wider ranges, or allow new positions.")
        if result.status != 0 or result.x is None:
            raise RebalanceError("The optimization did not prove an optimum within its time limit. No minimum claim or trade plan is shown.")
        return result

    def limit(self, objective: slice, value: float):
        self.add({i: 1 for i in range(objective.start, objective.stop)}, -np.inf, value)


def minimum_trades(problem: RebalanceInput, *, no_new_positions: bool = False,
                   new_money: float = 0., buys_only: bool = False) -> RebalancePlan:
    """First minimize the number of changed positions, then total EUR turnover."""
    total = problem.total + new_money
    if not math.isfinite(new_money) or new_money < 0 or total <= 0:
        raise RebalanceError("This mode needs positive portfolio value and nonnegative new money.")
    model = _AllocationModel(problem, total, buys_only=buys_only, no_new=no_new_positions,
                             hard_bands=True, max_trades=len(problem.values))
    result = model.solve(model.z)
    model.limit(model.z, round(result.fun))
    result = model.solve(model.q)
    return _result(problem, result.x[model.x], new_money, buys_only=buys_only,
                   no_new_positions=no_new_positions, require_bands=True)


def minimum_new_money(problem: RebalanceInput, *, no_new_positions: bool = False) -> RebalancePlan:
    """Minimize buy-only cash first; minimize trades at that cash amount second."""
    if problem.total <= 0:
        raise RebalanceError("With no invested capital, choose an amount in Allocate new money first.")
    n = len(problem.values)
    current = problem.values / problem.total
    # x is final value in units of the original total; T is the final total.
    a = np.vstack([np.column_stack([-np.eye(n), problem.lower]),
                   np.column_stack([np.eye(n), -problem.upper])])
    bounds = [(value, value if no_new_positions and not held else None)
              for value, held in zip(current, problem.held)] + [(1., None)]
    result = linprog(np.r_[np.zeros(n), 1.], A_ub=a, b_ub=np.zeros(2*n),
                     A_eq=np.array([np.r_[np.ones(n), -1.]]), b_eq=[0.], bounds=bounds,
                     method="highs", options={"time_limit": 10.})
    if result.status == 2:
        raise RebalanceError("No finite buy-only contribution can reach these target ranges with the current restrictions. Allow sells, new positions, or wider ranges.")
    if result.status != 0:
        raise RebalanceError("The solver could not prove the minimum new-money amount.")
    cash = max(0., (result.x[-1] - 1) * problem.total)
    return minimum_trades(problem, new_money=cash, buys_only=True, no_new_positions=no_new_positions)


def _buy_eligibility(problem: RebalanceInput, eligible_position_ids: Collection[str] | None,
                     no_new_positions: bool) -> np.ndarray:
    allowed = np.ones(len(problem.values), dtype=bool)
    if eligible_position_ids is not None:
        if not isinstance(eligible_position_ids, Collection) or isinstance(eligible_position_ids, (str, bytes)) or any(not isinstance(key, str) for key in eligible_position_ids):
            raise RebalanceError("Eligible positions must be a collection of position IDs.")
        if "position_id" not in problem.positions or problem.positions.position_id.duplicated().any():
            raise RebalanceError("Selecting eligible positions requires unique position IDs.")
        if set(eligible_position_ids) - set(problem.positions.position_id):
            raise RebalanceError("An eligible position is no longer in this portfolio. Select the positions again.")
        allowed = problem.positions.position_id.isin(eligible_position_ids).to_numpy()
    return allowed & problem.held if no_new_positions else allowed


def allocate_new_money(problem: RebalanceInput, new_money: float, *, max_trades: int,
                       no_new_positions: bool = False, eligible_position_ids: Collection[str] | None = None) -> RebalancePlan:
    """Invest the full amount; minimize outside-band deviation, then trade count."""
    if not math.isfinite(new_money) or new_money < 0 or problem.total + new_money <= 0:
        raise RebalanceError("Enter nonnegative new money, with a positive final portfolio value.")
    if not isinstance(max_trades, int) or isinstance(max_trades, bool) or not 0 <= max_trades <= len(problem.values):
        raise RebalanceError("Maximum trades must be a whole number between zero and the position count.")
    allowed = _buy_eligibility(problem, eligible_position_ids, no_new_positions)
    if new_money > 0 and not allowed.any():
        raise RebalanceError("No feasible buy: select at least one eligible position allowed by No new positions before investing new money.")
    model = _AllocationModel(problem, problem.total + new_money, buys_only=True, no_new=no_new_positions,
                             hard_bands=False, max_trades=max_trades, buy_allowed=allowed)
    result = model.solve(model.e)
    model.limit(model.e, result.fun + 1e-9)
    result = model.solve(model.z)
    model.limit(model.z, round(result.fun))
    result = model.solve(model.r)
    plan = _result(problem, result.x[model.x], new_money, buys_only=True,
                   no_new_positions=no_new_positions, require_bands=False)
    if (plan.table.loc[~allowed, "Trade (EUR)"] != 0).any():
        raise RebalanceError("The solver changed a position outside the eligible selection; no plan is shown.")
    if plan.trade_count > max_trades:
        raise RebalanceError("The solver result exceeded the trade limit; no plan is shown.")
    return plan


def cash_tradeoffs(problem: RebalanceInput, new_money: float, *, max_trades: int,
                   no_new_positions: bool = False, eligible_position_ids: Collection[str] | None = None,
                   progress: Callable[[int, int], None] | None = None) -> list[RebalancePlan]:
    plans = []
    for limit in range(1, max_trades + 1):
        plan = allocate_new_money(problem, new_money, max_trades=limit, no_new_positions=no_new_positions,
                                  eligible_position_ids=eligible_position_ids)
        if not plans or plan.deviation_after < plans[-1].deviation_after - 1e-6:
            plans.append(plan)
        if progress:
            progress(limit, max_trades)
        if plan.within_bands:
            break
    return plans


def balanced_cash_tradeoffs(problem: RebalanceInput, new_money: float, *, eligible_position_ids: Collection[str],
                            minimum_purchase: float = .01, buy_all: bool = False,
                            max_trades: int | None = None, no_new_positions: bool = False) -> list[RebalancePlan]:
    """Best squared-gap plans under minimum purchases and selected-row intent."""
    cents = _positive_cents(new_money, "contribution")
    minimum = _positive_cents(minimum_purchase, "minimum purchase")
    selected = _buy_eligibility(problem, eligible_position_ids, False)
    allowed = selected & problem.held if no_new_positions else selected
    if not allowed.any():
        raise RebalanceError("No feasible buy: select at least one eligible position allowed by No new positions.")
    if buy_all and (selected & ~allowed).any():
        raise RebalanceError("Buy every selected position conflicts with No new positions. Deselect empty positions, allow skipping, or turn off No new positions.")
    count = int(allowed.sum())
    if max_trades is None:
        max_trades = count
    if not isinstance(max_trades, int) or isinstance(max_trades, bool) or not 1 <= max_trades <= len(problem.values):
        raise RebalanceError("Maximum trades must be a whole number between one and the position count.")
    if buy_all and max_trades < count:
        raise RebalanceError(f"Buying every selected position requires {count} trades. Increase Maximum trades or allow skipping.")
    required = minimum * (count if buy_all else 1)
    if cents < required:
        raise RebalanceError(f"This plan needs at least €{required / 100:,.2f} at the chosen minimum purchase. "
                             f"Add €{(required - cents) / 100:,.2f}, lower the minimum purchase, or select fewer positions.")
    deficits = (problem.targets[allowed] * (problem.total + new_money) - problem.values[allowed]) * 100
    candidates = contribution_candidates(deficits, cents, minimum, buy_all=buy_all, max_trades=max_trades)
    plans = []
    for pennies in candidates:
        delta = np.zeros(len(problem.values))
        delta[allowed] = pennies / 100
        plans.append(_result(problem, (problem.values + delta) / (problem.total + new_money), new_money,
                             buys_only=True, no_new_positions=no_new_positions, require_bands=False, trade_amounts=delta))
    return plans


def _positive_cents(amount: float, label: str) -> int:
    if not math.isfinite(amount) or amount <= 0 or not math.isfinite(amount * 100):
        raise RebalanceError(f"Enter a positive {label}.")
    cents = round(amount * 100)
    if cents < 1 or not math.isclose(amount * 100, cents, abs_tol=1e-6, rel_tol=0):
        raise RebalanceError(f"Enter the {label} in whole EUR cents.")
    return cents


def spread_new_money(problem: RebalanceInput, new_money: float, *, eligible_position_ids: Collection[str],
                     method: Literal["balance", "target"] = "balance", no_new_positions: bool = False,
                     minimum_purchase: float = .01, buy_all: bool = False,
                     max_trades: int | None = None) -> RebalancePlan:
    """Balance final target gaps, or split the contribution by target weights.

    Balance minimizes sum((final_weight - target)**2), without a trade penalty.
    Project desired buys onto the nonnegative, fixed-budget simplex. Unselected
    rows are constant in that objective. Bands only affect the reported status.
    Largest-remainder rounding also minimizes this quadratic over whole cents.
    """
    if method == "balance":
        return balanced_cash_tradeoffs(problem, new_money, eligible_position_ids=eligible_position_ids,
                                       minimum_purchase=minimum_purchase, buy_all=buy_all,
                                       max_trades=max_trades, no_new_positions=no_new_positions)[-1]
    cents = _positive_cents(new_money, "contribution")
    allowed = _buy_eligibility(problem, eligible_position_ids, no_new_positions)
    if not allowed.any():
        raise RebalanceError("No feasible buy: select at least one eligible position allowed by No new positions.")
    if method not in {"balance", "target"}:
        raise RebalanceError("Choose target-gap balancing or target weights for the contribution.")
    weights = np.where(allowed, problem.targets, 0.)
    if weights.sum() <= 0:
        raise RebalanceError("The selected eligible targets are all zero. Set positive targets or choose Rebalance selected positions.")
    exact = cents * weights / weights.sum()
    pennies = np.floor(exact)
    remainder = cents - int(pennies.sum())
    order = np.argsort(-(exact - pennies), kind="stable")
    pennies[order[:remainder]] += 1
    if method == "target" and ((exact > 0) & (pennies == 0)).any():
        raise RebalanceError("This amount is too small to give every positive-weight recipient at least €0.01. Increase the amount or choose fewer positions.")
    delta = pennies / 100
    return _result(problem, (problem.values + delta) / (problem.total + new_money), new_money,
                   buys_only=True, no_new_positions=no_new_positions, require_bands=False, trade_amounts=delta)
