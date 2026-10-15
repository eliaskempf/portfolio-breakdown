"""Pure monitoring definitions and calculations; never places orders or changes holdings."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import math

import pandas as pd

from portfolio_app.holdings import DataError


def utc_now():
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class SeriesSpec:
    symbol: str
    name: str = ''
    currency: str = ''
    basis: str = 'close'
    source: str = 'Yahoo Finance'
    convention: str = 'Closing price'

    def __post_init__(self):
        if not self.symbol.strip() or self.basis not in {'close', 'adjusted'}:
            raise DataError('Choose an instrument and a supported price basis.')
        if self.symbol.startswith('MSCI:') and self.basis != 'close':
            raise DataError('MSCI references retain their published index return convention.')


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    kind: str = 'sma'
    revision: int = 1
    series: SeriesSpec | None = None
    window: int = 200
    period: str = 'sessions'
    cadence: str = 'daily'
    developing: bool = False
    direction: str = 'below'
    threshold: float = -2.0
    warning_band: float = 1.0
    account_id: str = ''
    backing: tuple[str, ...] = ()
    cash: tuple[str, ...] = ()
    currency: str = 'EUR'
    enabled: bool = True
    delivery: str = 'dialog'
    created_at: str = ''
    max_age_minutes: int = 10080

    def __post_init__(self):
        if any(type(x) is not int for x in (self.revision, self.window, self.max_age_minutes)) or any(type(x) is not bool for x in (self.enabled, self.developing)):
            raise DataError('Rule periods and revisions must be integers; switches must be booleans.')
        if not isinstance(self.id, str) or not isinstance(self.name, str) or not self.id or not self.name.strip() or self.revision < 1:
            raise DataError('Give the rule a name and a valid identity.')
        if self.kind not in {'sma', 'ltv', 'coverage'} or self.direction not in {'above', 'below'}:
            raise DataError('Unsupported monitoring rule.')
        if not all(math.isfinite(x) for x in (self.threshold, self.warning_band)) or self.warning_band < 0:
            raise DataError('Thresholds must be finite and the approaching band cannot be negative.')
        if self.delivery not in {'dialog', 'banner', 'center'} or self.max_age_minutes <= 0:
            raise DataError('Choose valid delivery and freshness settings.')
        if self.kind == 'sma':
            if not self.series or self.window < 2 or self.window > 1000:
                raise DataError('Choose a series and an SMA window from 2 to 1000 periods.')
            if self.period not in {'sessions', 'months'} or self.cadence not in {'daily', 'monthly', 'intraday'}:
                raise DataError('Choose a supported period and evaluation cadence.')
            if self.developing and self.cadence != 'intraday':
                raise DataError('Developing periods require intraday evaluation.')
            if self.series.symbol.startswith('MSCI:') and self.cadence == 'intraday':
                raise DataError('This MSCI source supplies daily levels, not intraday observations.')
        elif not self.account_id or self.currency not in {'EUR', 'USD', 'GBP'}:
            raise DataError('Choose an account and its monitoring currency.')
        if self.kind == 'ltv' and (self.direction != 'above' or self.threshold <= 0 or self.warning_band > self.threshold):
            raise DataError('LTV needs a positive maximum ratio and a lower nonnegative warning ratio.')
        if self.kind == 'coverage' and self.direction != 'below':
            raise DataError('Maintenance headroom alerts compare against a lower boundary.')
        if any(not isinstance(x, str) or not x for x in self.backing) or any(x not in {'EUR','USD','GBP'} for x in self.cash):
            raise DataError('Choose valid backing assets and cash currencies.')
        if len(set(self.backing)) != len(self.backing) or len(set(self.cash)) != len(self.cash):
            raise DataError('Backing assets must not be counted twice.')


@dataclass(frozen=True)
class MarketSeries:
    dates: tuple[str, ...] = ()
    closes: tuple[float, ...] = ()
    complete: tuple[bool, ...] = ()
    currency: str = ''
    fetched_at: str = ''
    quote: float | None = None
    quote_at: str = ''
    quality: str = 'unavailable'
    note: str = ''
    timezone: str = 'UTC'


@dataclass(frozen=True)
class Evaluation:
    observed_at: str
    value: float | None = None
    reference: float | None = None
    price: float | None = None
    condition: str = 'normal'
    quality: str = 'valid'
    note: str = ''
    inputs: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.condition not in {'normal', 'approaching', 'triggered'} or self.quality not in {'valid', 'unavailable', 'stale', 'not_applicable'}:
            raise DataError('Invalid monitoring evaluation state.')
        if not isinstance(self.observed_at, str) or not isinstance(self.note, str) or not isinstance(self.inputs, dict):
            raise DataError('Invalid monitoring evaluation details.')
        if any(x is not None and (isinstance(x, bool) or not isinstance(x, (int,float)) or not math.isfinite(x)) for x in (self.value, self.reference, self.price)):
            raise DataError('Monitoring values must be finite numbers or unavailable.')


def condition(value, rule):
    signed = (value - rule.threshold) * (1 if rule.direction == 'above' else -1)
    return 'triggered' if signed >= 0 else 'approaching' if signed >= -rule.warning_band else 'normal'


def unavailable(note, *, quality='unavailable', observed_at=''):
    return Evaluation(observed_at, quality=quality, note=note)


def indicator_frame(rule, series, *, now=None):
    """Calculate against completed observations; never forward-fill missing months."""
    now = now or utc_now()
    if len(series.dates) != len(series.closes) or len(series.complete) != len(series.dates):
        raise DataError('History observations and completion flags do not match.')
    if not series.dates:
        return pd.DataFrame(columns=['price', 'sma', 'distance'])
    index = pd.DatetimeIndex([pd.Timestamp(x).date() for x in series.dates])
    if index.has_duplicates or not index.is_monotonic_increasing:
        raise DataError('History must have unique, ordered trading dates.')
    if any(not math.isfinite(x) or x <= 0 for x in series.closes):
        raise DataError('History contains invalid prices.')
    prices = pd.Series(series.closes, index=index, dtype=float)
    completed = prices.loc[list(series.complete)]
    local_today = pd.Timestamp(now).tz_convert(series.timezone).date()
    completed = completed.loc[completed.index.date <= local_today]
    if rule.period == 'months':
        monthly = completed.resample('ME').last()
        # A month becomes complete when the next local calendar month begins.
        monthly = monthly.loc[monthly.index.to_period('M') < pd.Period(local_today, freq='M')]
        averages = monthly.rolling(rule.window, min_periods=rule.window).mean()
        # Mapping the *month*, not resampling a forward-filled daily series, keeps
        # missing source months invalid until a full consecutive window exists.
        keys = completed.index.to_period('M')
        ready = averages.copy()
        ready.index = ready.index.to_period('M')
        sma = pd.Series([ready.get(k, float('nan')) if k < pd.Period(local_today, freq='M')
                         else ready.get(k-1, float('nan')) for k in keys], index=completed.index)
        # During any historical month use the preceding completed month, except
        # its final observation (the month-end decision includes that close).
        for key in keys.unique():
            rows = completed.index[keys == key]
            sma.loc[rows] = ready.get(key-1, float('nan'))
            if key < pd.Period(local_today, freq='M'):
                sma.loc[rows[-1]] = ready.get(key, float('nan'))
    else:
        sma = completed.rolling(rule.window, min_periods=rule.window).mean()
    result = pd.DataFrame({'price': completed, 'sma': sma})
    if rule.cadence == 'monthly':
        result = result.groupby(result.index.to_period('M')).tail(1)
        result = result.loc[result.index.to_period('M') < pd.Period(local_today, freq='M')]
    result['distance'] = 100 * (result.price / result.sma - 1)
    return result


def evaluate_series(rule, series, *, now=None):
    now = now or utc_now()
    if series.quality != 'valid':
        return unavailable(series.note or 'Market data unavailable.', quality=series.quality)
    if rule.series.currency and series.currency != rule.series.currency:
        return unavailable('The source returned a different currency. Review this rule.')
    frame = indicator_frame(rule, series, now=now)
    if frame.empty:
        return unavailable('No completed observations available.')
    latest = frame.iloc[-1]
    stamp = frame.index[-1].date().isoformat()
    price, average = float(latest.price), float(latest.sma)
    if rule.cadence == 'intraday':
        if series.quote is None or not series.quote_at:
            return unavailable('A timestamped intraday observation is unavailable.')
        stamp, price = series.quote_at, series.quote
        if rule.developing:
            history = pd.Series(series.closes, index=pd.DatetimeIndex(series.dates)).loc[list(series.complete)]
            day = pd.Timestamp(stamp).tz_convert(series.timezone).date()
            if rule.period == 'months':
                history = history.resample('ME').last()
                history = history.loc[history.index.to_period('M') < pd.Period(day, freq='M')]
            else:
                history = history.loc[history.index.date < day]
            tail = history.tail(rule.window-1)
            average = (tail.sum()+price)/rule.window if len(tail) == rule.window-1 and tail.notna().all() else float('nan')
    if not math.isfinite(average) or average <= 0:
        return unavailable(f'Need {rule.window} complete {rule.period} of history.', observed_at=stamp)
    # A monthly decision stays current until the next completed month; assess
    # source freshness using the latest daily input, not that decision's date.
    age_stamp = stamp if rule.cadence == 'intraday' else series.dates[-1]
    observed = pd.Timestamp(age_stamp)
    if observed.tzinfo is None:
        observed = observed.tz_localize(series.timezone)
    age = (pd.Timestamp(now)-observed).total_seconds()/60
    if age < -1 or age > rule.max_age_minutes:
        return unavailable('The source observation is outside this rule’s freshness limit.', quality='stale', observed_at=stamp)
    value = 100 * (price/average-1)
    return Evaluation(stamp, value, average, price, condition(value, rule), note=series.note,
                      inputs={'currency': series.currency, 'basis': rule.series.basis})


def evaluate_account(rule, journal, holdings, valued, *, reporting_currency='EUR', fx_rates=None, now=None):
    from portfolio_app.account_journal import replay, effective_events
    from portfolio_app.account_margin import conversion_rate, margin_coverage
    from decimal import Decimal
    now = now or utc_now()
    stamp = now.isoformat()
    if rule.account_id not in {a.id for a in journal.accounts}:
        return unavailable('The monitored account no longer exists.')
    if rule.kind == 'coverage':
        coverage = margin_coverage(journal, holdings, valued, rule.account_id, rule.currency,
                                   reporting_currency=reporting_currency, fx_rates=fx_rates)
        if coverage.eligible_collateral is None or coverage.maintenance_requirement is None:
            return unavailable('Complete collateral valuations, FX and a maintenance requirement are needed.')
        if coverage.maintenance_requirement <= 0:
            return unavailable('Percentage headroom is not applicable to a zero maintenance requirement.', quality='not_applicable')
        value = float(100*coverage.maintenance_headroom/coverage.maintenance_requirement)
        inputs = {'collateral': float(coverage.eligible_collateral), 'maintenance': float(coverage.maintenance_requirement),
                  'headroom': float(coverage.maintenance_headroom), 'currency': rule.currency,
                  'backing': [{'asset': r.holding_id or 'Cash', 'reserved': float(r.reserved), 'haircut_pct': float(100*r.haircut),
                               'eligible_value': float(r.eligible_value) if r.eligible_value is not None else None, 'issue': r.issue} for r in coverage.collateral],
                  'requirements': [{'date': r.as_of, 'source': r.source} for r in journal.margin_requirements if r.account_id == rule.account_id]}
        return Evaluation(stamp, value, condition=condition(value, rule), inputs=inputs, note=' '.join(coverage.issues))
    state = replay(journal)
    def convert(amount, currency):
        rate = conversion_rate(fx_rates, currency, rule.currency, reporting_currency)
        if rate is None:
            raise DataError('Missing FX for account monitoring.')
        return Decimal(str(amount))*rate
    try:
        debts = {f'loan:{unit}:{identity}': float(convert(amount, unit)) for (account, unit, identity), amount in state.loans.items() if account == rule.account_id}
        debts.update({f'negative-cash:{unit}': float(convert(-amount, unit)) for (account, unit), amount in state.cash.items() if account == rule.account_id and amount < 0})
        owners = {link.position_id: link.account_id for link in journal.holding_links}
        cash_ids = {link.position_id for link in journal.cash_links}
        rows = valued.set_index('position_id')
        assets = {}
        for identity in rule.backing:
            if owners.get(identity) != rule.account_id or identity in cash_ids or identity not in rows.index:
                raise DataError('A selected backing asset is missing or no longer belongs to this account.')
            row = rows.loc[identity]
            if row.get('instrument_type') == 'cash':
                raise DataError('Select cash through its canonical account balance.')
            amount = float(row.current_value_reporting)
            if not math.isfinite(amount) or amount < 0:
                raise DataError('A selected backing asset has no valid valuation.')
            assets[identity] = float(convert(amount, reporting_currency))
        for unit in rule.cash:
            assets[f'cash:{unit}'] = float(convert(max(Decimal(0), state.cash.get((rule.account_id, unit), Decimal(0))), unit))
    except (DataError, ValueError, KeyError) as exc:
        return unavailable(str(exc))
    debt, backing = sum(debts.values()), sum(assets.values())
    inputs = {'obligations': debts, 'backing': assets, 'debt': debt, 'asset_value': backing, 'currency': rule.currency,
              'debt_entries': [{'date': e.date, 'action': e.kind} for e in effective_events(journal) if e.account_id == rule.account_id and e.kind.startswith(('debt', 'borrow', 'repay', 'cash'))]}
    if backing <= 0:
        return Evaluation(stamp, condition='triggered', inputs=inputs, note='Positive debt with no backing assets; LTV is undefined.') if debt > 0 else unavailable('No debt or backing assets; LTV is not applicable.', quality='not_applicable')
    value = 100*debt/backing
    return Evaluation(stamp, value, condition=condition(value, rule), inputs=inputs)
