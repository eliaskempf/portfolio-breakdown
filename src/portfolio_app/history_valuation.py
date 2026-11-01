"""Eligible historical closes and their provenance; never fabricate observations."""
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache

from portfolio_app.history_observations import index_observations, lookup, rate


def scope_observations(journal, scope, observations, target):
    """Conservative historical currency requirements, shared by charts and gates."""
    from portfolio_app.account_journal import effective_events
    from portfolio_app.currencies import quote_unit
    from portfolio_app.history_observations import active_observations
    from portfolio_app.security_history import selected_events
    subjects = set(scope.positions) | {b.instrument_id for b in journal.securities if b.position_id in scope.positions}
    positions = {p.id: p for p in journal.positions if p.account_id in scope.accounts}
    contracts = {p.contract_id for p in positions.values()}
    subjects.update(positions)
    subjects.update(contracts)
    active = active_observations(observations)
    currencies = {quote_unit(o.currency)[0] for o in active if o.kind == 'price' and o.subject in subjects}
    currencies.update(c.currency for c in journal.contracts if c.id in contracts)
    for book in journal.securities:
        if book.position_id not in scope.positions and book.account_id not in scope.accounts:
            continue
        for event in selected_events(book):
            currencies.update((event.currency, event.valuation_currency))
            currencies.update(c.currency for c in event.charges)
            currencies.update(p['currency'] for p in event.costs)
            currencies.update(p['currency'] for lot in event.lots for p in lot.costs)
    for event in effective_events(journal):
        if event.account_id in scope.accounts or event.other_account_id in scope.accounts or event.position_id in positions:
            currencies.update((event.currency, event.other_currency))
    pairs = {(native, target) for native in currencies - {'', target}}
    pairs |= {(right, left) for left, right in tuple(pairs)}
    return tuple(o for o in active if o.kind == 'price' and o.subject in subjects
                 or o.kind == 'fx' and (o.currency, o.target) in pairs
                 or o.kind == 'value' and o.subject == scope.key)

# Provider venue codes, not ticker suffixes or an instrument's domicile.
VENUES = {
    'NYQ': ('XNYS', 'America/New_York'),
    'NMS': ('XNAS', 'America/New_York'),
    'NGM': ('XNAS', 'America/New_York'),
    'NCM': ('XNAS', 'America/New_York'),
    'PCX': ('XNYS', 'America/New_York'),
    'ASE': ('XNYS', 'America/New_York'),
    'GER': ('XETR', 'Europe/Berlin'),
    'FRA': ('XFRA', 'Europe/Berlin'),
    'STU': ('XSTU', 'Europe/Berlin'),
    'LSE': ('XLON', 'Europe/London'),
    'PAR': ('XPAR', 'Europe/Paris'),
    'AMS': ('XAMS', 'Europe/Amsterdam'),
    'EBS': ('XSWX', 'Europe/Zurich'),
}

# Public historical FX session facts, not equity/bank holiday rules. Late
# reopenings at/after 17:00 New York begin the next daily closing session.
# Sources: OANDA holiday-trading-hours and QuantConnect/Lean
# Data/market-hours/market-hours-database.json, Forex-oanda-[*].
# Keep dates explicit: an undocumented future holiday must not mask a gap.
FX_DAILY_CLOSURES = frozenset({
    '2016-01-01', '2016-12-25', '2017-01-01', '2018-12-25',
    '2019-01-01', '2019-12-25', '2020-01-01', '2020-12-25',
    '2021-01-01', '2022-12-25', '2022-12-26', '2023-01-01',
    '2023-01-02', '2023-12-24', '2023-12-25', '2023-12-31',
    '2024-01-01', '2024-12-25', '2025-01-01', '2025-12-25', '2026-01-01',
})
FX_HOLIDAY_CURRENCIES = frozenset({'EUR', 'USD', 'GBP', 'CHF', 'JPY', 'CAD', 'AUD', 'NZD'})


def fx_closed_day(day, source, target):
    return day.weekday() >= 5 or (day.isoformat() in FX_DAILY_CLOSURES
        and {source, target} <= FX_HOLIDAY_CURRENCIES)


def listing_calendar(metadata):
    calendar, zone = VENUES.get(metadata.get('exchangeName'), ('', ''))
    return (calendar, zone) if zone and metadata.get('exchangeTimezoneName') == zone else ('', '')


@lru_cache(maxsize=64)
def exchange_calendar(name, year):
    import exchange_calendars as calendars
    return calendars.get_calendar(name, start=f'{year-1}-01-01', end=f'{year+1}-12-31')


@lru_cache(maxsize=16384)
def closed_interval(calendar, observed, requested):
    """No missed scheduled session between the original close and valuation."""
    from exchange_calendars.errors import InvalidCalendarName
    try:
        first, last = date.fromisoformat(observed), date.fromisoformat(requested)
        if first >= last or (last-first).days > 31:
            return False
        exchange = exchange_calendar(calendar, first.year)
        return exchange.is_session(observed) and len(exchange.sessions_in_range(
            (first+timedelta(days=1)).isoformat(), requested)) == 0
    except (ValueError, KeyError, OverflowError, InvalidCalendarName):
        return False


@dataclass(frozen=True)
class ValuationEvidence:
    subject: str
    requested_date: str
    as_of: str
    source: str
    method: str = 'close'
    kind: str = 'price'


def valuation_fx(observations, source, target, stamp):
    """Dated closing FX, or the prior close across documented closed sessions.

    This is a daily valuation convention, not transaction conversion evidence.
    Never look past a missing scheduled close or borrow a later rate.
    """
    observations = index_observations(observations)
    requested = date.fromisoformat(stamp)
    dates = [stamp]
    if fx_closed_day(requested, source, target):
        previous = requested-timedelta(days=1)
        for _ in range(7):
            if not fx_closed_day(previous, source, target):
                dates.append(previous.isoformat())
                break
            previous -= timedelta(days=1)
    for as_of in dates:
        value = rate(observations, source, target, as_of, side='close')
        if value is not None:
            evidence = tuple(provenance(o, stamp) for o in observations.fx.get(as_of, ())
                if o.side == 'close' and ((o.currency, o.target) == (source, target)
                                         or (o.currency, o.target) == (target, source)))
            return value, evidence
    return None, ()


def price_at(observations, subjects, stamp, *, events=(), contract=None):
    """Exact close first; carry only across an evidenced exchange closure.

    A split in the intervening interval needs a new correctly based quote.
    Execution/current prices never enter this resolver.
    """
    observations = index_observations(observations)
    for subject in subjects:
        exact = lookup(observations, 'price', subject, stamp)
        if exact:
            return exact
    for subject in subjects:
        series = observations.price_series.get(subject, ())
        dates = observations.price_dates.get(subject, ())
        index = bisect_right(dates, stamp)-1
        if index < 0:
            continue
        candidates = series[index]
        if len(candidates) != 1:
            continue
        previous = candidates[0]
        if contract is not None:
            from portfolio_app.futures_sessions import closed_interval as futures_closed_interval
            if futures_closed_interval(contract, previous.date, stamp):
                return previous
            continue
        if not previous.listing or not previous.calendar or not previous.exchange_timezone:
            continue
        if any(previous.date < e.date <= stamp and e.kind in ('split', 'note') for e in events):
            continue
        if closed_interval(previous.calendar, previous.date, stamp):
            return previous
    return None


def provenance(observation, stamp):
    return ValuationEvidence(observation.subject, stamp, observation.date, observation.source,
                             ('closed_fx_session' if observation.kind == 'fx' else 'closed_market')
                             if observation.date != stamp else 'close', observation.kind)
