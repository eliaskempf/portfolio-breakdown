"""Daily inflow-at-start / outflow-at-end TWR, independent of rendering.

Between external-flow boundaries, value ratios telescope. Incoming-only days
need their opening value; outgoing-only days need their closing value. This
avoids demanding prices which cancel algebraically (including a first purchase
from zero), while never filling a required boundary or spanning an unknown flow.
"""
from datetime import date, timedelta
from decimal import Decimal, localcontext
from itertools import groupby
import math

from portfolio_app.history_diagnostics import HistoryIssue as Issue

ZERO = Decimal(0)


def daily_time_weighted(calculation, scope, start, end, target, opening, ending, flows):
    if opening < 0 or ending < 0:
        return None, Issue('Time-weighted return is unavailable across negative equity', 'negative_equity')
    # Immutable context: reuse funded-boundary prefixes for range/hover requests.
    cache = calculation._get(('daily_twr', scope, start, target), dict)
    factor, base, invested = Decimal(1), opening, opening > 0
    with localcontext() as context:
        context.prec = 50
        for stamp, entries in groupby(flows, key=lambda f: f.date):
            entries = tuple(entries)
            if stamp in cache:
                factor, base, invested, reason = cache[stamp]
                if reason:
                    return None, reason
                continue
            incoming = sum((f.amount for f in entries if f.amount > 0), ZERO)
            outgoing = sum((-f.amount for f in entries if f.amount < 0), ZERO)
            reason = ''
            if incoming:
                previous_day = (date.fromisoformat(stamp)-timedelta(days=1)).isoformat()
                value = calculation.value(scope, previous_day, target) if stamp != start else None
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
                value = calculation.value(scope, stamp, target)
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
                return None, reason
        if not invested or (not base and ending):
            return None, Issue('Time-weighted return needs a funded interval with supported boundaries', 'funding')
        result = float(factor * (ending/base if base else 1) - 1)
    return (result, '') if math.isfinite(result) else (None, Issue('Return exceeds supported numeric range', 'numeric'))
