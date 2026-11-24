"""Reporting-currency unrealized gains, with native costs kept separately."""
from dataclasses import dataclass
import math
import pandas as pd

from portfolio_app.performance.cost_basis import active_components, resolve_cost


def _no_recorded_cost(parts):
    return not any(part.get('amount') not in ('', None) for part in parts)


def is_cash_balance(row):
    return row.get('instrument_type') == 'cash' and _no_recorded_cost(active_components(row))


def cash_balance_only(positions):
    """Cash without a recorded cost is a balance, not an incomplete purchase."""
    cash = positions.get('instrument_type', pd.Series('', index=positions.index)).eq('cash')
    result = pd.Series(False, index=positions.index)
    if cash.any():
        result.loc[cash] = [_no_recorded_cost(active_components(row)) for row in positions.loc[cash].to_dict('records')]
    return result


def cash_performance_note(currency, reporting_currency):
    if currency and currency != reporting_currency:
        return 'Cash balance: FX gain needs a recorded opening/conversion basis. Interest is separate.'
    return 'Cash balance: purchase-based return is not applicable. Interest is separate.'


def position_performance(valued, fx_to_reporting=None, *, reporting_currency='EUR', historical=None):
    rates = fx_to_reporting or {}
    marks = []
    rows = valued.to_dict('records')
    # Validate cash evidence before any historical FX requests, including closed balances.
    cash_parts = {i: active_components(row) for i, row in enumerate(rows) if row.get('instrument_type') == 'cash'}
    for i, row in enumerate(rows):
        mark = {}
        marks.append(mark)
        if row['shares'] == 0:
            mark['performance_note'] = 'No shares held'
            continue
        parts = cash_parts.get(i)
        if parts is not None and _no_recorded_cost(parts):
            mark['performance_note'] = cash_performance_note(row.get('quote_currency', ''), reporting_currency)
            continue
        cost, currency = row.get('acquisition_price', float('nan')), row.get('acquisition_currency', '')
        if pd.notna(cost) and currency:
            basis = row['shares'] * cost
            if math.isfinite(basis):
                mark['cost_basis'] = basis
                rate = 1. if currency == reporting_currency else rates.get(currency, float('nan'))
                current = (row['shares'] * row['current_price'] if currency == row['quote_currency'] else
                           row['current_value_reporting'] / rate if math.isfinite(rate) and rate > 0 else float('nan'))
                if math.isfinite(current) and math.isfinite(current - basis):
                    mark['unrealized_gain'] = current - basis
                    if basis > 0 and math.isfinite(100 * (current - basis) / basis):
                        mark['native_return_pct'] = 100 * (current - basis) / basis
        resolved = resolve_cost(parts if parts is not None else active_components(row), reporting_currency, historical)
        note = resolved.note
        if resolved.amount is not None:
            mark['cost_basis_reporting'] = resolved.amount
            mark['cost_estimated'] = resolved.estimated
            current = row['current_value_reporting']
            if not math.isfinite(current):
                note = 'Missing current price or exchange rate; excluded from gains'
            elif not math.isfinite(current - resolved.amount):
                note = 'Gain exceeds supported numeric range'
            else:
                gain = current - resolved.amount
                mark['unrealized_gain_reporting'] = gain
                if resolved.amount > 0 and math.isfinite(100 * gain / resolved.amount):
                    mark['return_pct'] = 100 * gain / resolved.amount
                elif resolved.amount == 0:
                    note = (note + '; ' if note else '') + 'Return undefined for zero cost'
        mark['performance_note'] = note

    result = valued.copy()
    for column in ('cost_basis', 'unrealized_gain', 'native_return_pct', 'return_pct',
                   'cost_basis_reporting', 'unrealized_gain_reporting'):
        result[column] = pd.Series([mark.get(column, math.nan) for mark in marks], index=valued.index, dtype=float)
    result['performance_note'] = pd.Series([mark['performance_note'] for mark in marks], index=valued.index, dtype=str)
    result['cost_estimated'] = pd.Series([mark.get('cost_estimated', False) for mark in marks], index=valued.index, dtype=bool)
    result['reporting_currency'] = reporting_currency
    return result


@dataclass(frozen=True)
class PerformanceSummary:
    held_count: int
    covered_count: int
    cost_reporting: float | None
    gain_reporting: float | None
    return_pct: float | None
    estimated_count: int = 0
    cash_balance_count: int = 0

    @property
    def eligible_count(self):
        return self.held_count - self.cash_balance_count

    @property
    def cash_only(self):
        return self.cash_balance_count > 0 and self.eligible_count == 0

    @property
    def coverage(self):
        return f'{self.covered_count} of {self.eligible_count}'


def summarize_performance(performance):
    held = performance.loc[performance.shares > 0]
    balances = cash_balance_only(held)
    covered = held.loc[~balances & held.unrealized_gain_reporting.notna()]
    cash_count = int(balances.sum())
    if covered.empty:
        return PerformanceSummary(len(held), 0, None, None, None, cash_balance_count=cash_count)
    cost = float(covered.cost_basis_reporting.sum())
    gain = float(covered.unrealized_gain_reporting.sum())
    estimated = int(covered.get('cost_estimated', pd.Series(False, index=covered.index)).sum())
    return PerformanceSummary(len(held), len(covered), cost, gain, 100 * gain / cost if cost > 0 else None, estimated, cash_count)
