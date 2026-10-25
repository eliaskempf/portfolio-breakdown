"""Eligible historical closes and their provenance; never fabricate observations."""
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache

from portfolio_app.history_observations import index_observations, lookup, rate

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


def price_at(observations, subjects, stamp, *, events=()):
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
