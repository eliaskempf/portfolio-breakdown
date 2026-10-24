"""Eligible historical closes and their provenance; never fabricate observations."""
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache

from portfolio_app.history_observations import index_observations, lookup

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
                             'closed_market' if observation.date != stamp else 'close')
