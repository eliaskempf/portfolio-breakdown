"""Daily inflow-at-start / outflow-at-end TWR, independent of rendering.

Between external-flow boundaries, value ratios telescope. Incoming-only days
need their opening value; outgoing-only days need their closing value. This
avoids demanding prices which cancel algebraically (including a first purchase
from zero), while never filling a required boundary or spanning an unknown flow.
"""
from datetime import date, timedelta
from decimal import Decimal, getcontext, localcontext
from itertools import groupby
import math

from portfolio_app.performance.history_diagnostics import HistoryIssue as Issue

ZERO = Decimal(0)


def daily_time_weighted(calculation, scope, start, end, target, opening, ending, flows):
    if opening < 0 or ending < 0:
        return None, Issue('Time-weighted return is unavailable across negative equity', 'negative_equity')
    _, (factor, base, invested, reason) = _daily_boundaries(
        calculation, scope, start, end, target, opening, ending, flows)
    if reason:
        return None, reason
    return _finish_return(factor, base, invested, ending)


def _daily_boundaries(calculation, scope, start, end, target, opening, ending, flows):
    # Immutable context: reuse funded-boundary prefixes for range/hover requests.
    cache = calculation._get(('daily_twr', scope, start, target), dict)
    factor, base, invested, reason = Decimal(1), opening, opening > 0, ''
    valuation_context = getcontext()

    def close(day):
        # Other history metrics share this dated monetary value. Use their
        # precision even while the return ratios compound at higher precision.
        with localcontext(valuation_context):
            return calculation.close(scope, day, target)

    with localcontext() as context:
        context.prec = 50
        for stamp, entries in groupby(flows, key=lambda f: f.date):
            entries = tuple(entries)
            if stamp in cache:
                factor, base, invested, reason = cache[stamp]
                if reason:
                    break
                continue
            incoming = sum((f.amount for f in entries if f.amount > 0), ZERO)
            outgoing = sum((-f.amount for f in entries if f.amount < 0), ZERO)
            reason = ''
            if incoming:
                previous_day = (date.fromisoformat(stamp)-timedelta(days=1)).isoformat()
                value = close(previous_day) if stamp != start else None
                before = opening if stamp == start else value.amount
                if before is None:
                    reason = Issue(f'{previous_day}: daily TWR needs a supported opening valuation. '
                                   + ' '.join(value.reasons), 'boundary', value.reasons)
                elif before < 0:
                    reason = Issue('Time-weighted return is unavailable across negative equity', 'negative_equity')
                elif base:
                    factor *= before/base
                elif before:
                    reason = Issue('Value appeared without recorded funding', 'funding')
                if not reason:
                    base = before + incoming
                    invested |= base > 0
            if outgoing and not reason:
                value = close(stamp)
                after = ending if stamp == end else value.amount
                if after is None:
                    reason = Issue(f'{stamp}: daily TWR needs a supported closing valuation. '
                                   + ' '.join(value.reasons), 'boundary', value.reasons)
                elif after < 0:
                    reason = Issue('Time-weighted return is unavailable across negative equity', 'negative_equity')
                elif not base:
                    reason = Issue('Withdrawal without a supported funded interval', 'funding')
                else:
                    factor *= (after+outgoing)/base
                    base = after
            cache[stamp] = factor, base, invested, reason
            if reason:
                break
    return cache, (factor, base, invested, reason)


def _finish_return(factor, base, invested, ending):
    if not invested or (not base and ending):
        return None, Issue('Time-weighted return needs a funded interval with supported boundaries', 'funding')
    with localcontext() as context:
        context.prec = 50
        result = float(factor * (ending/base if base else 1) - 1)
    return (result, '') if math.isfinite(result) else (None, Issue('Return exceeds supported numeric range', 'numeric'))


def chart_returns(calculation, scope, start, target, values, full_result, negative_dates):
    """Evaluate dated closes against one sequence of funded flow boundaries."""
    from bisect import bisect_left

    opening = full_result.opening_value
    if opening is None or opening < 0:
        return {}
    dates = [day for day, value in values.items()
             if value is not None and value >= 0 and (not negative_dates or day < negative_dates[0])]
    if full_result.flow_reasons:
        # For a fixed inception, ledger coverage issues and missing flow facts
        # accumulate. Find the first affected close without scanning every prefix.
        def unsupported(day):
            _, missing = calculation.flows(scope, start, day, target)
            return bool(missing or calculation.coverage(scope, start, day))
        dates = dates[:bisect_left(dates, True, key=unsupported)]
    if not dates:
        return {}
    end = dates[-1]
    flows, _ = calculation.flows(scope, start, end, target)
    boundaries, _ = _daily_boundaries(calculation, scope, start, end, target, opening, values[end], flows)
    flow_days = iter(dict.fromkeys(flow.date for flow in flows))
    next_day = next(flow_days, None)
    state = Decimal(1), opening, opening > 0, ''
    result = {}
    for day in dates:
        while next_day is not None and next_day <= day:
            state = boundaries.get(next_day)
            if state is None or state[3]:
                return result
            next_day = next(flow_days, None)
        result[day] = _finish_return(*state[:3], values[day])[0]
    return result
