"""Category target constraints over tradable source rows, including flexible leaves."""
from dataclasses import dataclass
import math

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, linprog, milp

from portfolio_app.portfolio.allocation import analysis_targets, validate_allocation
from portfolio_app.portfolio.rebalancing import RebalanceError, RebalancePlan, _positive_cents
from portfolio_app.portfolio.strategic import bucket_positions, category_labels


@dataclass(frozen=True)
class TargetConstraint:
    name: str
    members: np.ndarray
    parent: np.ndarray | None
    target: float
    margin: float


def needs_category_planner(config, positions, scope=''):
    ids = config.descendants(scope)
    return any(b.id in ids and (b.allocation_mode == 'flexible' or
               (config.children(b.id) and positions.bucket_id.eq(b.id).any())) for b in config.buckets) or bool(config.children(scope))


def target_constraints(positions, config, scope='', *, category_tolerance=.5, position_tolerance=.5, relative=False):
    """Each sibling partition includes child categories and directly owned positions."""
    validate_allocation(config)
    labels = category_labels(config)
    specs = []
    def visit(key):
        parent_mask = None if key == scope else positions.bucket_id.isin(config.descendants(key)).to_numpy(dtype=float)
        if config.flexible(key):
            return
        children = config.children(key)
        direct = positions.loc[positions.bucket_id.eq(key)] if key else positions.iloc[:0]
        entries = [(labels[b.id], positions.bucket_id.isin(config.descendants(b.id)).to_numpy(dtype=float), b.target, category_tolerance) for b in children]
        entries += [(row['name'], positions.position_id.eq(row.position_id).to_numpy(dtype=float), row.within_bucket_target, position_tolerance) for _, row in direct.iterrows()]
        # An empty targeted category retains capacity at its parent.
        if entries:
            targets = [entry[2] for entry in entries]
            if any(t is None or not math.isfinite(t) or not 0 <= t <= 1 for t in targets) or not math.isclose(sum(targets), 1., abs_tol=1e-9):
                raise RebalanceError(f'Targets for {labels.get(key, "Portfolio")} must be complete and total 100%, including direct investments and subcategories.')
            for name, mask, target, tolerance in entries:
                specs.append(TargetConstraint(name, mask, parent_mask, target, tolerance / 100 * (target if relative else 1)))
        for b in children:
            visit(b.id)
    visit(scope)
    return specs


def _distances(specs, values, total):
    errors, gaps = [], []
    for spec in specs:
        denominator = total if spec.parent is None else float(spec.parent @ values)
        if denominator <= 0:
            errors.append(0.)
            gaps.append(0.)
            continue
        weight = float(spec.members @ values) / denominator
        errors.append(max(0., spec.target-spec.margin-weight, weight-spec.target-spec.margin))
        gaps.append(weight-spec.target)
    return 100*sum(errors), 100*np.sqrt(np.mean(np.square(gaps))) if gaps else 0.


@dataclass
class CategoryPlan(RebalancePlan):
    category_rms: float = 0.
    priority_notes: tuple[str, ...] = ()

    @property
    def target_rms(self):
        return self.category_rms


def category_plan(valued, config, scope='', *, mode='contribution', amount=0.,
                  category_tolerance=.5, position_tolerance=.5, relative=False,
                  eligible_ids=None, no_new_positions=False, max_trades=None,
                  minimum_purchase=.01, buy_all=False, max_allocations=None, cap_scope='portfolio',
                  portfolio_value=None):
    """Solve category bands directly; never assign synthetic member weights.

    Hard-band modes prove their cash/trade objective. Contributions first invest
    available cash, then minimize band error and exact error. Saved priorities
    break ties only after those objectives have been fixed.
    """
    positions = bucket_positions(valued, config, scope).reset_index(drop=True)
    if positions.empty or len(positions) > 100:
        raise RebalanceError('Choose a scope containing 1 to 100 position rows.')
    if positions.bucket_id.eq('').any():
        raise RebalanceError('Assign positions to categories before planning.')
    if mode not in {'contribution', 'fewest', 'minimum'}:
        raise RebalanceError('Choose an available planning mode.')
    for number in (amount, category_tolerance, position_tolerance, minimum_purchase):
        if not math.isfinite(number) or number < 0:
            raise RebalanceError('Planning amounts and tolerances must be finite and nonnegative.')
    if category_tolerance > 100 or position_tolerance > 100 or minimum_purchase <= 0:
        raise RebalanceError('Tolerances must not exceed 100 and minimum purchases must be positive.')
    specs = target_constraints(positions, config, scope, category_tolerance=category_tolerance,
                               position_tolerance=position_tolerance, relative=relative)
    values = positions.current_value_reporting.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values < 0).any():
        raise RebalanceError('Every held position needs a finite, nonnegative valuation.')
    n, k = len(values), len(specs)
    initial = math.fsum(values)
    ids = positions.position_id.tolist()
    eligible = set(ids if eligible_ids is None else eligible_ids)
    if not eligible <= set(ids):
        raise RebalanceError('Selected purchase positions changed. Review the selection.')
    allowed = np.array([key in eligible for key in ids])
    held = positions.shares.to_numpy(dtype=float) > 0
    if no_new_positions:
        if buy_all and (allowed & ~held).any():
            raise RebalanceError('Buy every selected position conflicts with Only buy existing positions.')
        allowed &= held
    maximum = n if max_trades is None else max_trades
    if not isinstance(maximum, int) or not 1 <= maximum <= n:
        raise RebalanceError('Maximum trades must be between one and the number of positions.')
    if buy_all and allowed.sum() > maximum:
        raise RebalanceError('Maximum trades is smaller than the required purchase count.')
    protected = set().union(*(config.descendants(b.id) for b in config.buckets if b.sell_protected)) if config.buckets else set()
    sell_allowed = ~positions.bucket_id.isin(protected).to_numpy() if mode == 'fewest' else np.zeros(n, dtype=bool)
    caps = max_allocations or {}
    if set(caps) - eligible or any(not math.isfinite(v) or not 0 <= v <= 1 for v in caps.values()):
        raise RebalanceError('Caps must be fractions between zero and one for selected positions.')
    if cap_scope not in {'portfolio', 'bucket'}:
        raise RebalanceError('Choose portfolio or category cap scope.')

    # Minimum cash is a continuous problem before minimizing trade count at that cash.
    if mode == 'minimum':
        scale = max(initial, 1.)
        rows, rhs = [], []
        for spec in specs:
            for sign, target in ((-1, max(0., spec.target-spec.margin)), (1, min(1., spec.target+spec.margin))):
                row = np.zeros(n+1)
                row[:n] = sign*spec.members
                if spec.parent is None:
                    row[n] = -sign*target
                    bound = sign*target*initial/scale
                else:
                    row[:n] -= sign*target*spec.parent
                    bound = 0.
                rows.append(row)
                rhs.append(bound)
        equality = np.r_[np.ones(n), -1.][None, :]
        bounds = [(v/scale, None if allow else v/scale) for v, allow in zip(values, allowed)] + [(0., None)]
        solved = linprog(np.r_[np.zeros(n), 1.], A_ub=np.array(rows) if rows else None,
                         b_ub=np.array(rhs) if rows else None, A_eq=equality, b_eq=[initial/scale], bounds=bounds, method='highs')
        if not solved.success:
            raise RebalanceError('No feasible buy-only plan reaches every category target range.')
        amount = float(solved.x[-1])*scale
    elif mode == 'fewest':
        amount = 0.
    if mode == 'contribution' and amount > 0:
        amount = _positive_cents(amount, 'contribution') / 100
    total = initial + amount
    if total <= 0:
        raise RebalanceError('A positive portfolio value or contribution is required.')
    current = values/total
    buy_scale = total*100 if mode == 'contribution' else 1.
    # x, buy, sell, buy indicator, sell indicator, unallocated cash, band error, exact error.
    size = 5*n + 1 + 2*k
    cash = 5*n
    e, r = slice(cash+1, cash+1+k), slice(cash+1+k, size)
    lower, upper = np.zeros(size), np.full(size, np.inf)
    upper[:5*n] = 1.
    upper[n:2*n] = np.where(allowed, buy_scale, 0.)
    upper[2*n:3*n] = np.where(sell_allowed, current, 0.)
    upper[cash] = amount/total if mode == 'contribution' else 0.
    integrality = np.zeros(size)
    integrality[3*n:5*n] = 1
    if mode == 'contribution':
        integrality[n:2*n] = 1
    rows, lows, highs = [], [], []
    def add(coefficients, lo=-np.inf, hi=np.inf):
        row = np.zeros(size)
        for index, coefficient in coefficients.items():
            row[index] = coefficient
        rows.append(row)
        lows.append(lo)
        highs.append(hi)
    add({**dict.fromkeys(range(n), 1.), cash: 1.}, 1., 1.)
    add(dict.fromkeys(range(3*n, 5*n), 1.), hi=maximum)
    for i in range(n):
        add({i: 1., n+i: -1./buy_scale, 2*n+i: 1.}, current[i], current[i])
        add({n+i: 1., 3*n+i: -buy_scale}, hi=0.)
        add({2*n+i: 1., 4*n+i: -current[i]}, hi=0.)
        add({3*n+i: 1., 4*n+i: 1.}, hi=1.)
        if mode == 'contribution':
            add({n+i: -1., 3*n+i: _positive_cents(minimum_purchase, 'minimum purchase')}, hi=0.)
        if buy_all and allowed[i]:
            lower[3*n+i] = 1.
        if ids[i] in caps:
            if cap_scope == 'bucket' and positions.bucket_id.iloc[i] != scope:
                mask = positions.bucket_id.isin(config.descendants(positions.bucket_id.iloc[i])).to_numpy(dtype=float)
                coefficients = {j: -caps[ids[i]]*mask[j] for j in range(n)}
                coefficients[i] += 1.
                # Enforce the final cap for any purchase; merely holding an
                # already oversized investment must not force a sale.
                coefficients[3*n+i] = 1.
                add(coefficients, hi=1.)
            elif cap_scope == 'bucket':
                add({i: 1.}, hi=max(current[i], caps[ids[i]]))
            else:
                denominator = (portfolio_value if portfolio_value is not None else initial) + amount
                add({i: 1.}, hi=max(current[i], caps[ids[i]]*denominator/total))
    for j, spec in enumerate(specs):
        for sign, target in ((-1, max(0., spec.target-spec.margin)), (1, min(1., spec.target+spec.margin))):
            coefficients = sign*spec.members.copy()
            bound = sign*target if spec.parent is None else 0.
            if spec.parent is not None:
                coefficients -= sign*target*spec.parent
            entries = {i: v for i, v in enumerate(coefficients)}
            if mode == 'contribution':
                entries[e.start+j] = -1.
            add(entries, hi=bound)
        for sign in (-1, 1):
            coefficients = sign*spec.members.copy()
            bound = sign*spec.target if spec.parent is None else 0.
            if spec.parent is not None:
                coefficients -= sign*spec.target*spec.parent
            add({**{i: v for i, v in enumerate(coefficients)}, r.start+j: -1.}, hi=bound)
    def solve(objective):
        result = milp(objective, integrality=integrality, bounds=Bounds(lower, upper),
                      constraints=LinearConstraint(np.array(rows), lows, highs),
                      options={'time_limit': 10., 'mip_rel_gap': 0.})
        if result.status != 0 or result.x is None:
            raise RebalanceError('No proven feasible category plan under these restrictions. Review targets, eligible positions and trade limits.')
        # Retain each primary optimum before applying later tie-breaks.
        add({i: v for i, v in enumerate(objective) if v}, hi=float(objective @ result.x)+1e-9)
        return result
    objective = np.zeros(size)
    if mode == 'contribution':
        objective[cash] = 1.
        solve(objective)
        objective[:] = 0.; objective[e] = 1.
        solve(objective)
        objective[:] = 0.; objective[r] = 1.
        solve(objective)
    objective[:] = 0.; objective[3*n:5*n] = 1.
    solve(objective)
    objective[:] = 0.; objective[n:2*n] = 1./buy_scale; objective[2*n:3*n] = 1.
    result = solve(objective)
    objective[:] = 0.
    buckets = {b.id: b for b in config.buckets}
    for i, row in positions.iterrows():
        b = buckets[row.bucket_id]
        if b.allocation_mode == 'flexible':
            for priority, offset in ((b.buy_priority, n), (b.sell_priority, 2*n)):
                objective[offset+i] = (priority.index(row.position_id) if row.position_id in priority else len(priority)) / (buy_scale if offset == n else 1.)
    if objective.any():
        result = solve(objective)
    final = result.x[:n]*total
    delta = np.rint(result.x[n:2*n])/100 if mode == 'contribution' else final-values
    if mode != 'contribution':
        delta[np.abs(delta) < max(1e-7, total*1e-9)] = 0.
    final = values+delta
    notes = []
    for b in config.buckets:
        if b.allocation_mode != 'flexible':
            continue
        member_indexes = np.flatnonzero(positions.bucket_id.eq(b.id))
        members = set(positions.iloc[member_indexes].position_id)
        for direction, priority, sign in (('buy', b.buy_priority, 1), ('sell', b.sell_priority, -1)):
            changed = [i for i in member_indexes if sign*delta[i] > 0]
            if changed and len(members) > 1 and not members <= set(priority):
                raise RebalanceError(f'Set the complete {direction} priority for {b.name} before calculating these trades.')
            if any(priority and ids[i] != priority[0] for i in changed):
                notes.append(f'{b.name}: {direction} priority used additional members to satisfy planning restrictions.')
    unallocated = max(0., amount-float(delta.sum()))
    if abs(float(final.sum())+unallocated-total) > max(1e-5, total*1e-7) or (final < -1e-6).any():
        raise RebalanceError('Category plan failed value conservation; no plan is shown.')
    money_tolerance = max(1e-6, total*1e-8)
    if ((delta[~allowed] > money_tolerance).any() or (delta[~sell_allowed] < -money_tolerance).any()
            or np.count_nonzero(delta) > maximum):
        raise RebalanceError('Category plan failed source trade restrictions; no plan is shown.')
    if mode == 'contribution':
        bought = delta > 0
        if ((delta[bought] + 1e-7 < minimum_purchase).any() or
                (buy_all and not bought[allowed].all())):
            raise RebalanceError('Category plan failed minimum purchase verification; no plan is shown.')
    for i, pid in enumerate(ids):
        if pid not in caps or delta[i] <= money_tolerance:
            continue
        if cap_scope == 'portfolio':
            denominator = (portfolio_value if portfolio_value is not None else initial)+amount
        elif positions.bucket_id.iloc[i] == scope:
            denominator = total
        else:
            denominator = float(final[positions.bucket_id.isin(config.descendants(positions.bucket_id.iloc[i]))].sum())
        if final[i] > caps[pid]*denominator + money_tolerance:
            raise RebalanceError('Category plan failed allocation-cap verification; no plan is shown.')
    before_error, _ = _distances(specs, values, initial)
    after_error, rms = _distances(specs, final, total)
    if mode != 'contribution' and after_error > 1e-4:
        raise RebalanceError('Category plan failed target-range verification; no plan is shown.')
    targets = analysis_targets(positions, config).target_allocation.to_numpy(dtype=float)
    if scope:
        targets = np.array([config.relative_target(row.bucket_id, scope)*row.within_bucket_target
                            if not config.flexible(row.bucket_id) else np.nan for row in positions.itertuples()])
    table = positions[[c for c in ('position_id', 'id', 'name', 'ticker', 'account', 'portfolio') if c in positions]].copy()
    table['Action'] = np.where(delta > 0, 'Buy', np.where(delta < 0, 'Sell', 'Hold'))
    table['Trade'], table['Current'], table['After'] = delta, values, final
    table['Current %'] = 100*values/initial if initial else np.nan
    table['After %'] = 100*final/total
    table['Target %'] = 100*targets
    table['Gap (pp)'] = table['After %']-table['Target %']
    table['Lower %'] = table['Upper %'] = np.nan
    return CategoryPlan(table, amount, int(np.count_nonzero(delta)), int((delta > 0).sum()), int((delta < 0).sum()),
                        before_error, after_error, after_error <= 1e-4, unallocated, rms, tuple(notes))
