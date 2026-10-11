"""Explicit-instrument exposure planning; never passes notional to a cash solver.

Minimize absolute reporting-currency category gaps, then turnover. Funded trades
use fractional units; futures use whole contracts. Fees are per traded unit.
Only a declared external contribution and permitted sales fund purchases.
Collateral is a declared eligible subset of the existing funded future, plus
an explicit part of the contribution. No transfers or orders are persisted.
"""
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp

from portfolio_app.exposure_targets import exposure_gaps, leaf_composition
from portfolio_app.futures import CONTRACTS, require_eur_reporting
from portfolio_app.holdings import DataError
from portfolio_app.portfolio_exposure import finite


@dataclass(frozen=True)
class ExposurePlan:
    trades: pd.DataFrame
    gaps: pd.DataFrame
    equity: float
    fees: float
    unallocated_cash: float
    margin_required: float
    collateral_remaining: float
    complete: bool


def plan_exposure(result, targets, allocation, valued, *, permitted, future=None,
                  contribution=0., allow_sales=False, fee_per_unit=0.,
                  future_fee=0., collateral_limit=0., future_funding=0.,
                  margin_per_contract=None, max_contracts=10000, allow_short=False):
    """A hypothetical plan at current prices; contribution enters equity once.

    The linked ETF grants no permission: every source ID must be in permitted.
    Existing liabilities remain fixed. Category/position capital targets are
    neither read nor changed. Unknown amounts and unassigned nonzero exposure
    block planning; unresolved companies with known category notional do not.
    """
    if future is not None or result.reporting_currency == 'EUR':
        require_eur_reporting(valued)
    elif ('reporting_currency' not in valued or not valued.reporting_currency.eq(result.reporting_currency).all()
          or valued.attrs.get('reporting_currency', result.reporting_currency) != result.reporting_currency):
        raise DataError('Planning inputs require consistent reporting currency.')
    for value in (contribution, fee_per_unit, future_fee, collateral_limit, future_funding):
        if not finite(value) or value < 0:
            raise DataError('Funding, collateral limits and per-unit fees must be finite and nonnegative.')
    if future_funding > contribution:
        raise DataError('Dedicated future funding must be part of the external contribution.')
    if type(max_contracts) is not int or not 0 <= max_contracts <= 10000:
        raise DataError('Contract limit must be a whole number between 0 and 10000.')
    base = exposure_gaps(result, targets, allocation).set_index('Bucket ID')
    invalid = result.exposures.loc[~result.exposures.bucket_id.isin(allocation.leaves()), targets.field]
    if invalid.abs().gt(1e-9).any():
        raise DataError('Assign all nonzero exposures to valid leaf categories before planning.')
    permitted = set(permitted)
    if targets.policy == 'overlay_only' and permitted != {'future:overlay'}:
        raise DataError('Overlay-only planning permits the future alone; funded exposures must stay unchanged.')
    if not permitted:
        raise DataError('Select the instruments permitted in this plan.')
    if permitted - (set(valued.position_id) | ({'future:overlay'} if future else set())):
        raise DataError('A permitted source position no longer exists.')
    if future_funding and (future is None or future.reference_capital_eur is None):
        raise DataError('Dedicated funding requires an existing funded futures position.')
    protected = set().union(*(allocation.leaves(b.id) for b in allocation.buckets if b.sell_protected))
    instruments = []
    for row in valued.loc[valued.position_id.isin(permitted)].itertuples():
        if row.bucket_id not in allocation.leaves() or not finite(row.current_value_reporting) or row.current_value_reporting <= 0 or not finite(row.shares) or row.shares <= 0:
            raise DataError('Permitted funded positions need a positive priced balance and a valid leaf category.')
        source = result.exposures.loc[result.exposures.source_position_id.eq(row.position_id)]
        amount = float(source[targets.field].sum())
        if source.empty or source[targets.field].isna().any() or abs(amount) < 1e-9:
            raise DataError('Permitted instruments must have known nonzero exposure in the selected universe.')
        price = row.current_value_reporting / row.shares
        instruments.append(dict(id=row.position_id, name=row.name, bucket=row.bucket_id, unit=amount/row.shares,
                                price=price, fee=fee_per_unit, quantity=row.shares, future=False,
                                sales=allow_sales and row.bucket_id not in protected))
    changing_future = 'future:overlay' in permitted
    if changing_future:
        if future.reference_capital_eur is None or future.bucket_id not in allocation.leaves():
            raise DataError('Future planning requires dedicated funded capital and a valid leaf category.')
        if margin_per_contract is None or not finite(margin_per_contract) or margin_per_contract <= 0:
            raise DataError('Declare a positive initial margin per contract for this plan.')
        if collateral_limit > max(0., future.capital_value) + 1e-8:
            raise DataError('Eligible existing collateral cannot exceed the funded position value.')
        if future.bucket_id in protected:
            raise DataError('The future category is sell-protected; review that restriction before permitting contract changes.')
        instruments.append(dict(id='future:overlay', name=future.instrument + ' future', bucket=future.bucket_id,
                                unit=CONTRACTS[future.instrument][1]*future.mark_price, price=0., fee=future_fee,
                                quantity=future.contracts, future=True, sales=True))
    elif future_funding:
        raise DataError('Permit the future explicitly before dedicating new funding to it.')
    keys = list(base.index)
    n, k = len(instruments), len(keys)
    # Buy and sell magnitudes per instrument, absolute resulting contracts,
    # then absolute category gaps. Futures magnitudes and final count are integer.
    size, margin_index = 3*n + 1 + k, 2*n
    direction_start = 2*n+1+k
    lower, upper = np.zeros(size), np.full(size, np.inf)
    integer = np.zeros(size)
    cash, fees = np.zeros(size), np.zeros(size)
    changes = {key: np.zeros(size) for key in keys}
    future_fees = np.zeros(size)
    constraints, lo, hi = [], [], []
    def constraint(vector, minimum=-np.inf, maximum=np.inf):
        constraints.append(vector.copy()); lo.append(minimum); hi.append(maximum)
    for i, item in enumerate(instruments):
        buy, sell = 2*i, 2*i+1
        fees[[buy, sell]] = item['fee']
        changes[item['bucket']][buy] = item['unit']
        changes[item['bucket']][sell] = -item['unit']
        if item['future']:
            integer[[buy, sell, margin_index]] = 1
            upper[[buy, sell]] = 20000
            final = np.zeros(size); final[buy], final[sell] = 1, -1
            constraint(final, (-max_contracts if allow_short else 0)-item['quantity'], max_contracts-item['quantity'])
            positive, negative = final.copy(), -final
            positive[margin_index] = negative[margin_index] = -1
            constraint(positive, maximum=-item['quantity'])
            constraint(negative, maximum=item['quantity'])
            future_fees[[buy, sell]] = item['fee']
        else:
            upper[sell] = item['quantity'] if item['sales'] else 0
            upper[buy] = (contribution + sum(v['price']*v['quantity'] for v in instruments if not v['future'] and v['sales'])) / item['price']
            cash[buy], cash[sell] = item['price'] + item['fee'], -item['price'] + item['fee']
        direction = direction_start+i
        integer[direction], upper[direction] = 1, 1
        buy_side, sell_side = np.zeros(size), np.zeros(size)
        buy_side[buy], buy_side[direction] = 1, -upper[buy]
        sell_side[sell], sell_side[direction] = 1, upper[sell]
        constraint(buy_side, maximum=0)
        constraint(sell_side, maximum=upper[sell])
    constraint(cash, maximum=contribution-future_funding)
    if changing_future:
        margin = future_fees.copy(); margin[margin_index] = margin_per_contract
        constraint(margin, maximum=collateral_limit+future_funding)
        # A collateral deficit is not a fresh borrowing balance. New funding
        # first repairs a negative funded value before supporting margin.
        constraint(margin, maximum=future.capital_value+future_funding)
    else:
        upper[margin_index] = 0
    equity_before_fees = result.denominator + contribution
    constraint(fees, maximum=equity_before_fees-1e-8)
    composition = leaf_composition(targets, allocation) if targets.policy == 'composition' else {targets.overlay_bucket: 1.}
    for j, key in enumerate(keys):
        fraction = targets.multiple * composition.get(key, 0.)
        intercept = 0. if targets.policy == 'composition' else base.loc[key, f'Desired exposure ({result.reporting_currency})'] - fraction*result.denominator
        desired = fraction * equity_before_fees + intercept
        actual = base.loc[key, f'Current exposure ({result.reporting_currency})']
        gap = changes[key] + fraction * fees
        positive, negative = gap.copy(), -gap
        positive[2*n+1+j] = negative[2*n+1+j] = -1
        constraint(positive, maximum=desired-actual)
        constraint(negative, maximum=actual-desired)
    objective = np.zeros(size); objective[2*n+1:direction_start] = 1
    def solve(cost):
        return milp(cost, integrality=integer, bounds=Bounds(lower, upper),
                    constraints=LinearConstraint(np.array(constraints), lo, hi), options={'time_limit': 15.})
    solution = solve(objective)
    if not solution.success:
        raise DataError('No verified feasible plan within the declared funding, collateral and contract limits.')
    constraint(objective, maximum=solution.fun+1e-7)
    turnover = np.zeros(size)
    for i, item in enumerate(instruments):
        turnover[2*i:2*i+2] = max(abs(item['unit']), item['price'], 1.)
    refined = solve(turnover)
    x = refined.x if refined.success else solution.x
    activity = np.array(constraints) @ x
    if (not np.isfinite(x).all() or np.any(activity < np.array(lo)-1e-5)
            or np.any(activity > np.array(hi)+1e-5)
            or np.any(abs(x[integer == 1]-np.round(x[integer == 1])) > 1e-5)):
        raise DataError('Solver did not return a verified feasible plan; adjust the declared constraints.')
    total_fees = float(fees @ x)
    records = []
    updated = result.exposures.copy()
    for i, item in enumerate(instruments):
        units = float(x[2*i]-x[2*i+1])
        if item['future']:
            units = int(round(units))
        change = item['unit']*units
        mask = updated.source_position_id.eq(item['id'])
        # Allocate the change proportionally only for nonzero source exposure;
        # a closed future has one zero row which receives its full new notional.
        amount = updated.loc[mask, targets.field].sum()
        if abs(amount) > 1e-9:
            updated.loc[mask, targets.field] *= (amount+change)/amount
        else:
            updated.loc[mask, targets.field] = 0.
            updated.loc[updated.index[mask][0], targets.field] = change
        if abs(units) > 1e-8:
            records.append({'Position ID': item['id'], 'Instrument': item['name'], 'Action': 'Buy' if units > 0 else 'Sell',
                            'Units / contracts': units, f'Exposure change ({result.reporting_currency})': change,
                            f'Funded trade ({result.reporting_currency})': item['price']*units, f'Fees ({result.reporting_currency})': item['fee']*abs(units)})
    equity = equity_before_fees-total_fees
    gaps = exposure_gaps(replace(result, exposures=updated, net_equity=equity), targets, allocation)
    final_contracts = future.contracts + next((r['Units / contracts'] for r in records if r['Position ID'] == 'future:overlay'), 0) if changing_future else 0
    margin_required = abs(final_contracts)*margin_per_contract if changing_future else 0.
    collateral = min(collateral_limit, future.capital_value)+future_funding-float(future_fees @ x) if changing_future else 0.
    return ExposurePlan(pd.DataFrame(records, columns=['Position ID', 'Instrument', 'Action', 'Units / contracts', f'Exposure change ({result.reporting_currency})', f'Funded trade ({result.reporting_currency})', f'Fees ({result.reporting_currency})']),
                        gaps, equity, total_fees, max(0., contribution-future_funding-float(cash @ x)),
                        margin_required, collateral, bool(gaps[f'Gap ({result.reporting_currency})'].abs().le(.01).all()))
