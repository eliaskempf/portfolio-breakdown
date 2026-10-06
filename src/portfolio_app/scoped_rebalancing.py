"""Parent-first contribution routing, followed by isolated position optimizers."""
from dataclasses import dataclass
import math

import numpy as np
import pandas as pd
from scipy.optimize import linprog

from portfolio_app.allocation import Allocation
from portfolio_app.capped_contributions import bounded_projection
from portfolio_app.rebalancing import (RebalanceError, prepare_rebalance,
                                       balanced_cash_tradeoffs, _positive_cents)


@dataclass
class ScopedPlan:
    budgets: pd.DataFrame
    trades: pd.DataFrame
    after: pd.DataFrame
    unallocated_cash: float
    new_money: float


def sleeve_positions(valued: pd.DataFrame, config: Allocation, bucket: str) -> pd.DataFrame:
    if bucket not in config.leaves():
        raise RebalanceError('Choose a leaf bucket for a position-level calculation.')
    result = valued.loc[valued.bucket_id == bucket].copy()
    result['target_allocation'] = result.within_bucket_target
    return result


def _route(values, targets, budget, lower, upper, margin):
    """Minimize outside-band EUR distance, then target distance, with cent rounding."""
    n = len(values)
    final = sum(values) + budget
    # Variables: contribution, band error, absolute target error.
    desired = targets * final - values
    band_low = np.maximum(0, targets - margin) * final - values
    band_high = np.minimum(1, targets + margin) * final - values
    rows, rhs = [], []
    for i in range(n):
        for coefficient, aux, bound in [(-1, n+i, -band_low[i]), (1, n+i, band_high[i]),
                                         (-1, 2*n+i, -desired[i]), (1, 2*n+i, desired[i])]:
            row = np.zeros(3*n)
            row[i], row[aux] = coefficient, -1
            rows.append(row)
            rhs.append(bound)
    bounds = list(zip(lower, upper)) + [(0, None)] * (2*n)
    eq = np.zeros((1, 3*n))
    eq[0, :n] = 1
    # Cash beyond all capacities stays unallocated at this parent.
    spend = min(budget, sum(upper))
    if spend < sum(lower):
        raise RebalanceError('Contribution cannot satisfy Buy every selected position and minimum purchases.')
    objective = np.r_[np.zeros(n), np.ones(n), np.zeros(n)]
    first = linprog(objective, A_ub=np.array(rows), b_ub=rhs, A_eq=eq, b_eq=[spend], bounds=bounds, method='highs')
    if not first.success:
        raise RebalanceError('No feasible bucket budgets under the selected restrictions.')
    rows.append(objective)
    rhs.append(float(first.fun) + 1e-7)
    second = linprog(np.r_[np.zeros(2*n), np.ones(n)], A_ub=np.array(rows), b_ub=rhs,
                     A_eq=eq, b_eq=[spend], bounds=bounds, method='highs')
    if not second.success:
        raise RebalanceError('Could not verify bucket budgets.')
    return bounded_projection(second.x[:n], int(spend), np.asarray(lower), np.asarray(upper))


def portfolio_contribution(valued: pd.DataFrame, config: Allocation, amount: float, *,
                           eligible_ids: list[str], minimum_purchase: float = .01,
                           buy_all: bool = False, no_new_positions: bool = False,
                           max_trades: int | None = None, macro_tolerance: float = .5,
                           position_tolerance: float = .5, max_allocations: dict | None = None,
                           cap_scope: str = 'portfolio') -> ScopedPlan:
    cents = _positive_cents(amount, 'contribution')
    minimum = _positive_cents(minimum_purchase, 'minimum purchase')
    if valued.current_value_reporting.isna().any():
        raise RebalanceError('Portfolio routing needs complete EUR valuations. A sleeve-local calculation may still be available.')
    if (valued.bucket_id == '').any():
        raise RebalanceError('Assign positions to strategic buckets before routing a portfolio contribution.')
    if not set(eligible_ids) <= set(valued.position_id):
        raise RebalanceError('Selected positions changed; select them again.')
    if cap_scope not in {'portfolio', 'bucket'}:
        raise RebalanceError('Choose portfolio or bucket cap scope.')
    if not math.isfinite(macro_tolerance) or not 0 <= macro_tolerance <= 100:
        raise RebalanceError('Macro tolerance must be between 0 and 100 percentage points.')
    max_allocations = max_allocations or {}
    if set(max_allocations) - set(eligible_ids) or any(not math.isfinite(v) or not 0 <= v <= 1 for v in max_allocations.values()):
        raise RebalanceError('Caps must be fractions between zero and one for selected positions.')
    max_trades = len(eligible_ids) if max_trades is None else max_trades
    if not isinstance(max_trades, int) or max_trades < 1:
        raise RebalanceError('Maximum trades must be positive.')
    allowed = valued.position_id.isin(eligible_ids)
    if no_new_positions:
        if buy_all and (allowed & valued.shares.eq(0)).any():
            raise RebalanceError('Buy every selected position conflicts with No new positions.')
        allowed &= valued.shares > 0
    if not allowed.any():
        raise RebalanceError('Select at least one eligible source position.')
    if buy_all and int(allowed.sum()) > max_trades:
        raise RebalanceError('Maximum trades is smaller than the selected position count.')
    total = float(valued.current_value_reporting.sum()) + amount
    capacities, floors = {}, {}
    for leaf in config.leaves():
        rows = valued.loc[valued.bucket_id.eq(leaf) & allowed]
        capacity = 0
        for r in rows.itertuples():
            cap = max_allocations.get(r.position_id)
            # Bucket-relative caps are applied after its budget is fixed. Their
            # conservative upper bound here is the largest possible bucket total.
            denominator = total if cap_scope == 'portfolio' else float(valued.loc[valued.bucket_id.eq(leaf), 'current_value_reporting'].sum()) + amount
            upper = cents if cap is None else min(cents, max(0, math.floor((cap * denominator - r.current_value_reporting) * 100 + 1e-7)))
            if buy_all and upper < minimum:
                raise RebalanceError('A selected position cap cannot accommodate its minimum purchase.')
            capacity += upper if upper >= minimum else 0
        capacities[leaf] = min(cents, capacity)
        floors[leaf] = len(rows) * minimum if buy_all else 0
    budgets = {}
    def route(parent, budget):
        children = config.children(parent)
        if not children:
            budgets[parent] = budget
            return
        targets = [b.target for b in children]
        if any(t is None for t in targets) or not math.isclose(sum(targets), 1., abs_tol=1e-9):
            raise RebalanceError('Complete sibling bucket targets must total 100% for portfolio routing.')
        subsets = [config.leaves(b.id) for b in children]
        values = np.array([valued.loc[valued.bucket_id.isin(leaves), 'current_value_reporting'].sum() * 100 for leaves in subsets])
        lower = [sum(floors[k] for k in leaves) for leaves in subsets]
        upper = [min(cents, sum(capacities[k] for k in leaves)) for leaves in subsets]
        split = _route(values, np.array(targets), budget, lower, upper, macro_tolerance / 100)
        for child, cash in zip(children, split):
            route(child.id, int(cash))
    route('', cents)
    # Keep each bucket's macro budget fixed while comparing its position plans.
    frontiers = []
    budget_rows = []
    for leaf, cash in sorted(budgets.items()):
        budget_rows.append({'Bucket ID': leaf, 'Budget': cash / 100})
        if cash == 0:
            continue
        positions = sleeve_positions(valued, config, leaf)
        ids = positions.loc[positions.position_id.isin(valued.loc[allowed, 'position_id']), 'position_id'].tolist()
        if cash < minimum and not buy_all:
            continue
        problem = prepare_rebalance(positions, tolerance=position_tolerance)
        caps = {key: min(1., value * total / (problem.total + cash / 100)) if cap_scope == 'portfolio' else value
                for key, value in max_allocations.items() if key in ids}
        plans = balanced_cash_tradeoffs(problem, cash / 100, eligible_position_ids=ids,
                                        minimum_purchase=minimum_purchase, buy_all=buy_all,
                                        no_new_positions=no_new_positions, max_trades=min(max_trades, len(positions)),
                                        max_allocations=caps)
        if not buy_all:
            # Skipping a bucket is permitted under the global trade limit; its
            # reserved contribution remains cash and is shown as such.
            frontiers.append([(0, 0., 0., None), *[(p.trade_count, cash / 100 - p.unallocated_cash, p.target_rms**2, p) for p in plans]])
        else:
            frontiers.append([(p.trade_count, cash / 100 - p.unallocated_cash, p.target_rms**2, p) for p in plans])
    # Dynamic program: most invested cash, then equal-per-bucket squared error.
    states = {0: (0., 0., [])}
    for frontier in frontiers:
        updated = {}
        for used, (invested, error, selected) in states.items():
            for count, spend, gap, plan in frontier:
                key = used + count
                if key > max_trades:
                    continue
                candidate = (invested + spend, error + gap, selected + ([plan] if plan is not None else []))
                if key not in updated or (-candidate[0], candidate[1]) < (-updated[key][0], updated[key][1]):
                    updated[key] = candidate
        states = updated
    if not states:
        raise RebalanceError('No plan fits the shared trade limit and purchase intent.')
    best = min(states.items(), key=lambda item: (-item[1][0], item[1][1], item[0]))[1]
    tables = [p.table for p in best[2]]
    trades = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame(columns=['position_id', 'Trade'])
    after = valued.copy()
    delta = trades.set_index('position_id')['Trade'] if not trades.empty else pd.Series(dtype=float)
    after['current_value_reporting'] += after.position_id.map(delta).fillna(0)
    leftover = round(amount - float(trades['Trade'].sum()), 2)
    return ScopedPlan(pd.DataFrame(budget_rows), trades, after, leftover, amount)
