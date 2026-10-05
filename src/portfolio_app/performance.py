"""Reporting-currency unrealized gains, with native costs kept separately."""
from dataclasses import dataclass
import math
import pandas as pd

from portfolio_app.cost_basis import active_components, resolve_cost


def position_performance(valued, fx_to_reporting=None, *, reporting_currency='EUR', historical=None):
    result = valued.copy()
    for column in ('cost_basis', 'unrealized_gain', 'native_return_pct', 'return_pct',
                   'cost_basis_reporting', 'unrealized_gain_reporting'):
        result[column] = float('nan')
    result['performance_note'] = ''
    result['cost_estimated'] = False
    result['reporting_currency'] = reporting_currency
    rates = fx_to_reporting or {}
    for index, row in result.iterrows():
        if row.shares == 0:
            result.at[index, 'performance_note'] = 'No shares held'
            continue
        cost, currency = row.get('acquisition_price', float('nan')), row.get('acquisition_currency', '')
        if pd.notna(cost) and currency:
            basis = row.shares * cost
            if math.isfinite(basis):
                result.at[index, 'cost_basis'] = basis
                rate = 1. if currency == reporting_currency else rates.get(currency, float('nan'))
                current = (row.shares * row.current_price if currency == row.quote_currency else
                           row.current_value_reporting / rate if math.isfinite(rate) and rate > 0 else float('nan'))
                if math.isfinite(current) and math.isfinite(current - basis):
                    result.at[index, 'unrealized_gain'] = current - basis
                    if basis > 0 and math.isfinite(100 * (current - basis) / basis):
                        result.at[index, 'native_return_pct'] = 100 * (current - basis) / basis
        resolved = resolve_cost(active_components(row), reporting_currency, historical)
        note = resolved.note
        if resolved.amount is not None:
            result.at[index, 'cost_basis_reporting'] = resolved.amount
            result.at[index, 'cost_estimated'] = resolved.estimated
            current = row.current_value_reporting
            if not math.isfinite(current):
                note = 'Missing current price or exchange rate; excluded from gains'
            elif not math.isfinite(current - resolved.amount):
                note = 'Gain exceeds supported numeric range'
            else:
                gain = current - resolved.amount
                result.at[index, 'unrealized_gain_reporting'] = gain
                if resolved.amount > 0 and math.isfinite(100 * gain / resolved.amount):
                    result.at[index, 'return_pct'] = 100 * gain / resolved.amount
                elif resolved.amount == 0:
                    note = (note + '; ' if note else '') + 'Return undefined for zero cost'
        result.at[index, 'performance_note'] = note
    return result


@dataclass(frozen=True)
class PerformanceSummary:
    held_count: int
    covered_count: int
    cost_reporting: float | None
    gain_reporting: float | None
    return_pct: float | None
    estimated_count: int = 0


def summarize_performance(performance):
    held = performance.loc[performance.shares > 0]
    covered = held.loc[held.unrealized_gain_reporting.notna()]
    if covered.empty:
        return PerformanceSummary(len(held), 0, None, None, None)
    cost = float(covered.cost_basis_reporting.sum())
    gain = float(covered.unrealized_gain_reporting.sum())
    estimated = int(covered.get('cost_estimated', pd.Series(False, index=covered.index)).sum())
    return PerformanceSummary(len(held), len(covered), cost, gain, 100 * gain / cost if cost > 0 else None, estimated)
