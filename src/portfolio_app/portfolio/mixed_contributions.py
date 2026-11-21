"""Parent-first contribution routing through mixed category/position ownership."""
import math

import pandas as pd
import numpy as np

from portfolio_app.portfolio.category_rebalancing import category_plan
from portfolio_app.portfolio.rebalancing import RebalanceError, prepare_rebalance, balanced_cash_tradeoffs, _positive_cents
from portfolio_app.portfolio.scoped_rebalancing import ScopedPlan, _route


def mixed_contribution(valued, config, amount, *, eligible_ids, minimum_purchase=.01, buy_all=False,
                       no_new_positions=False, max_trades=None, macro_tolerance=.5,
                       position_tolerance=.5, max_allocations=None, cap_scope='portfolio'):
    cents = _positive_cents(amount, 'contribution')
    minimum = _positive_cents(minimum_purchase, 'minimum purchase')
    if not np.isfinite(valued.current_value_reporting.to_numpy(dtype=float)).all() or (valued.current_value_reporting < 0).any():
        raise RebalanceError('Portfolio routing needs complete nonnegative valuations.')
    if valued.bucket_id.eq('').any() or not set(valued.bucket_id) <= config.descendants():
        raise RebalanceError('Assign positions to categories before routing a portfolio contribution.')
    if not set(eligible_ids) <= set(valued.position_id):
        raise RebalanceError('Selected positions changed; select them again.')
    if cap_scope not in {'portfolio', 'bucket'}:
        raise RebalanceError('Choose portfolio or category cap scope.')
    if not math.isfinite(macro_tolerance) or not 0 <= macro_tolerance <= 100:
        raise RebalanceError('Category tolerance must be between 0 and 100 percentage points.')
    max_allocations = max_allocations or {}
    if set(max_allocations)-set(eligible_ids) or any(not math.isfinite(v) or not 0 <= v <= 1 for v in max_allocations.values()):
        raise RebalanceError('Caps must be fractions between zero and one for selected positions.')
    maximum = len(eligible_ids) if max_trades is None else max_trades
    if not isinstance(maximum, int) or maximum < 1:
        raise RebalanceError('Maximum trades must be positive.')
    allowed = valued.position_id.isin(eligible_ids)
    if no_new_positions:
        if buy_all and (allowed & valued.shares.eq(0)).any():
            raise RebalanceError('Buy every selected position conflicts with Only buy existing positions.')
        allowed &= valued.shares > 0
    if not allowed.any():
        raise RebalanceError('Select at least one eligible source position.')
    if buy_all and int(allowed.sum()) > maximum:
        raise RebalanceError('Maximum trades is smaller than the selected position count.')
    eligible = set(valued.loc[allowed, 'position_id'])
    total = float(valued.current_value_reporting.sum())+amount
    units = {}
    for bucket in config.buckets:
        direct = valued.loc[valued.bucket_id.eq(bucket.id)]
        if not config.children(bucket.id):
            units[bucket.id] = (bucket.id, direct, False)
        else:
            for position in direct.itertuples():
                units[('position', position.position_id)] = (bucket.id, direct.loc[direct.position_id.eq(position.position_id)], True)
    capacities, floors = {}, {}
    for key, (owner, positions, individual) in units.items():
        rows = positions.loc[positions.position_id.isin(eligible)]
        capacity = 0
        denominator = total if cap_scope == 'portfolio' else float(valued.loc[valued.bucket_id.isin(config.descendants(owner)), 'current_value_reporting'].sum())+amount
        for r in rows.itertuples():
            cap = max_allocations.get(r.position_id)
            upper = cents if cap is None else min(cents, max(0, math.floor((cap*denominator-r.current_value_reporting)*100+1e-7)))
            if buy_all and upper < minimum:
                raise RebalanceError('A selected position cap cannot accommodate its minimum purchase.')
            capacity += upper if upper >= minimum else 0
        capacities[key] = min(cents, capacity)
        floors[key] = len(rows)*minimum if buy_all else 0
    budgets = {}
    subtree_values = {b.id: float(valued.loc[valued.bucket_id.isin(config.descendants(b.id)), 'current_value_reporting'].sum()) for b in config.buckets}
    routed_totals = {'': total}
    def route(parent, budget):
        children = config.children(parent)
        if not children:
            budgets[parent] = budget
            routed_totals[parent] = subtree_values[parent]+budget/100
            return
        direct = valued.loc[valued.bucket_id.eq(parent)] if parent else valued.iloc[:0]
        entries = [(b.id, b.target, subtree_values[b.id], [key for key, (owner, _, _) in units.items() if owner in config.descendants(b.id)]) for b in children]
        entries += [(('position', r.position_id), r.within_bucket_target, r.current_value_reporting, [('position', r.position_id)]) for r in direct.itertuples()]
        targets = [entry[1] for entry in entries]
        if any(t is None or not math.isfinite(t) or not 0 <= t <= 1 for t in targets) or not math.isclose(sum(targets), 1., abs_tol=1e-9):
            raise RebalanceError('Complete sibling targets, including direct investments, must total 100% for contribution routing.')
        values = np.array([entry[2]*100 for entry in entries])
        lower = [sum(floors[key] for key in entry[3]) for entry in entries]
        upper = [min(cents, sum(capacities[key] for key in entry[3])) for entry in entries]
        split = _route(values, np.array(targets), budget, lower, upper, macro_tolerance/100)
        routed_totals[parent] = sum(values)/100+budget/100
        for entry, cash in zip(entries, split):
            if isinstance(entry[0], tuple):
                budgets[entry[0]] = int(cash)
            else:
                route(entry[0], int(cash))
    route('', cents)
    frontiers, budget_rows = [], []
    for key, cash in sorted(budgets.items(), key=lambda item: str(item[0])):
        owner, positions, individual = units[key]
        budget_rows.append({'Bucket ID': owner, 'Position ID': positions.position_id.iloc[0] if individual else '', 'Budget': cash/100})
        if cash == 0 or (cash < minimum and not buy_all):
            continue
        ids = [key for key in positions.position_id if key in eligible]
        if not ids:
            continue
        caps = {}
        for pid in ids:
            if pid in max_allocations:
                denominator = total if cap_scope == 'portfolio' else routed_totals[owner]
                caps[pid] = min(1., max_allocations[pid]*denominator/(float(positions.current_value_reporting.sum())+cash/100))
        if config.flexible(owner):
            best = category_plan(positions, config, owner, amount=cash/100, eligible_ids=ids,
                minimum_purchase=minimum_purchase, buy_all=buy_all, no_new_positions=no_new_positions,
                max_trades=min(maximum, len(positions)), max_allocations=caps)
            plans = [best]
            if not buy_all:
                for count in range(1, best.trade_count):
                    plans.append(category_plan(positions, config, owner, amount=cash/100, eligible_ids=ids,
                        minimum_purchase=minimum_purchase, max_trades=count, no_new_positions=no_new_positions, max_allocations=caps))
        else:
            positions = positions.copy()
            positions['target_allocation'] = 1. if individual else positions.within_bucket_target
            problem = prepare_rebalance(positions, tolerance=position_tolerance)
            plans = balanced_cash_tradeoffs(problem, cash/100, eligible_position_ids=ids,
                minimum_purchase=minimum_purchase, buy_all=buy_all, no_new_positions=no_new_positions,
                max_trades=min(maximum, len(positions)), max_allocations=caps)
        frontier = [(p.trade_count, cash/100-p.unallocated_cash, p.target_rms**2, p) for p in plans]
        if not buy_all:
            frontier.insert(0, (0, 0., 0., None))
        frontiers.append(frontier)
    states = {0: (0., 0., [])}
    for frontier in frontiers:
        updated = {}
        for used, (invested, error, selected) in states.items():
            for count, spend, gap, plan in frontier:
                key = used+count
                if key > maximum:
                    continue
                candidate = (invested+spend, error+gap, selected+([plan] if plan is not None else []))
                if key not in updated or (-candidate[0], candidate[1]) < (-updated[key][0], updated[key][1]):
                    updated[key] = candidate
        states = updated
    if not states:
        raise RebalanceError('No plan fits the shared trade limit and purchase intent.')
    best = min(states.items(), key=lambda item: (-item[1][0], item[1][1], item[0]))[1]
    notes = [note for p in best[2] for note in getattr(p, 'priority_notes', ())]
    tables = [p.table for p in best[2]]
    trades = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame(columns=['position_id', 'Trade'])
    after = valued.copy()
    delta = trades.set_index('position_id').Trade
    after['current_value_reporting'] += after.position_id.map(delta).fillna(0.)
    return ScopedPlan(pd.DataFrame(budget_rows), trades, after, max(0., amount-float(trades.Trade.sum())), amount,
                      tuple(dict.fromkeys(notes)))
