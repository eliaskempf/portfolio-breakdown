"""Verified Eurex sessions used only for quote freshness, never for cash posting.

Sources: https://www.eurex.com/ex-en/trade/trading-calendar/holiday-regulations
and the 2026 trading-hours calendar linked there (including the CEST footnote).
Unknown products/calendars and expiry-day schedules do not receive an exemption.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from portfolio_app.futures_clock import EXCHANGE_ZONE

_EXTENDED = frozenset(('FMWN', 'FMWO', 'FMWB', 'FESX', 'FDAX', 'FDXM', 'FDXS'))
# Published Eurex-wide closures, 2026–2030. Currency payment holidays are NOT
# exchange closures: e.g. a USD holiday does not close MSCI World USD futures.
_EASTER = {2026: ('04-03', '04-06'), 2027: ('03-26', '03-29'),
           2028: ('04-14', '04-17'), 2029: ('03-30', '04-02'), 2030: ('04-19', '04-22')}
_FIXED = frozenset(('01-01', '05-01', '12-24', '12-25', '12-26', '12-31'))
_UK_2026 = frozenset(('05-04', '05-25', '08-31', '12-28'))


def _supported(contract, day):
    return (contract is not None and contract.exchange == 'EUREX'
            and contract.product in _EXTENDED | {'FTUK'} and day.year in _EASTER
            and (contract.product != 'FTUK' or day.year == 2026)
            and day < date.fromisoformat(contract.last_trade_date or contract.expiry))


def _bounds(product, day):
    short = day.strftime('%m-%d')
    if day.weekday() >= 5 or short in _FIXED or short in _EASTER[day.year]:
        return None
    if product == 'FTUK' and short in _UK_2026:
        return None
    # Extended sessions start 00:10 UTC: 01:10 CET / 02:10 CEST.
    midday = datetime.combine(day, time(12), EXCHANGE_ZONE)
    opening = time(7, 50) if product == 'FTUK' else time(2 if midday.dst() else 1, 10)
    return datetime.combine(day, opening, EXCHANGE_ZONE), datetime.combine(day, time(22), EXCHANGE_ZONE)


@dataclass(frozen=True)
class MarketSession:
    opening: datetime
    closing: datetime
    is_open: bool


def market_session(contract, now):
    local = now.astimezone(EXCHANGE_ZONE)
    day = local.date()
    if not _supported(contract, day):
        return None
    for _ in range(14):
        if not _supported(contract, day):
            return None
        bounds = _bounds(contract.product, day)
        if bounds and bounds[0] <= local:
            opening, closing = bounds
            return MarketSession(opening, closing, local < closing)
        day -= timedelta(days=1)
    return None


def trading_age(contract, stamp, now):
    """Elapsed scheduled trading time, or None when the schedule is unverified."""
    start, end = stamp.astimezone(EXCHANGE_ZONE), now.astimezone(EXCHANGE_ZONE)
    if end < start or end-start > timedelta(days=14):
        return None
    if not _supported(contract, start.date()) or not _supported(contract, end.date()):
        return None
    initial = _bounds(contract.product, start.date())
    if initial is None or not initial[0] <= start <= initial[1] + timedelta(minutes=30):
        return None
    elapsed = timedelta()
    day = start.date()
    while day <= end.date():
        if not _supported(contract, day):
            return None
        bounds = _bounds(contract.product, day)
        if bounds:
            elapsed += max(timedelta(), min(end, bounds[1])-max(start, bounds[0]))
        day += timedelta(days=1)
    return elapsed
