"""Human-readable monitoring conditions and observation details."""
import pandas as pd


def observation_time(value):
    if not value:
        return 'Not checked yet'
    stamp = pd.Timestamp(value)
    return stamp.strftime('%d %b %Y') if stamp.tzinfo is None else stamp.tz_convert('UTC').strftime('%d %b %Y, %H:%M UTC')


def sma_label(rule):
    return f'{rule.window}-{"month" if rule.period == "months" else "session"} SMA'


def alert_details(rule, evaluation):
    value = evaluation.get('value')
    if rule.kind == 'sma':
        cadence = {'daily':'Daily close', 'monthly':'Month-end close', 'intraday':'Intraday price'}[rule.cadence]
        comparison = 'at or below' if rule.direction == 'below' else 'at or above'
        condition = f'{cadence} {comparison} {rule.threshold:+g}% relative to its {sma_label(rule)}.'
        if (rule.direction == 'below' and rule.threshold < 0) or (rule.direction == 'above' and rule.threshold > 0):
            condition = f'{cadence} at least {abs(rule.threshold):g}% {rule.direction} its {sma_label(rule)}.'
        observed = 'Observation unavailable.' if value is None else f'{rule.series.name or rule.series.symbol}: {abs(value):.2f}% {"below" if value < 0 else "above"} its {sma_label(rule)}.'
    elif rule.kind == 'ltv':
        condition = f'Loan-to-value at or above {rule.threshold:g}%.'
        observed = f'LTV is {value:.2f}%.' if value is not None else evaluation.get('note', 'LTV is undefined.')
    else:
        condition = f'Maintenance headroom at or below {rule.threshold:g}%.'
        observed = f'Maintenance headroom is {value:.2f}%.' if value is not None else evaluation.get('note', 'Headroom unavailable.')
    return condition, observed
