"""Synthetic quotes and explicit clocks; no market requests or personal data."""
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from portfolio_app.derivative_market import make_contract
from portfolio_app.derivative_types import PriceObservation
from portfolio_app.futures_sessions import market_session, trading_age
from portfolio_app.futures_valuation import freshness


def at(stamp):
    return datetime.fromisoformat(stamp + '+00:00')


def contract(product='FMWN'):
    return make_contract(product, '2027-03-19')


@pytest.mark.parametrize('now,status', [
    ('2026-01-09T22:00', 'market_closed'),
    ('2026-01-10T12:00', 'market_closed'),
    ('2026-01-12T00:09', 'market_closed'),
    ('2026-01-12T00:20', 'delayed'),
    ('2026-01-12T00:26', 'stale'),
])
def test_weekend_and_reopening_count_only_trading_minutes(now, status):
    c = contract()
    q = PriceObservation(c.id, '2026-01-09', 100, kind='mark', quote_at=at('2026-01-09T20:45').isoformat())
    value = freshness(q, now=at(now), contract=c)
    assert value.status == status
    assert value.qualified == (status == 'stale')


@pytest.mark.parametrize('now,status', [
    ('2026-01-09T20:00', 'stale'),
    ('2026-01-09T21:01', 'market_closed'),
    ('2026-01-10T12:00', 'market_closed'),
    ('2026-01-12T00:09', 'market_closed'),
    ('2026-01-12T00:11', 'stale'),
])
def test_latest_session_last_trade_is_quiet_while_closed(now, status):
    c = contract()
    q = PriceObservation(c.id, '2026-01-09', 100, kind='mark', quote_at=at('2026-01-09T19:00').isoformat())
    value = freshness(q, now=at(now), contract=c)
    assert value.status == status
    assert value.qualified == (status == 'stale')
    assert q.quote_at in value.detail


def test_prior_session_quote_failure_and_conflict_remain_qualified_while_closed():
    c = contract()
    q = PriceObservation(c.id, '2026-01-09', 100, kind='mark', quote_at=at('2026-01-09T19:00').isoformat())
    assert freshness(q, now=at('2026-01-10T12:00'), contract=c, error='Invented outage').qualified
    assert freshness(q, now=at('2026-01-10T12:00'), contract=c, disputed=True).qualified
    assert freshness(q, now=at('2026-01-10T12:00')).qualified
    older = replace(q, date='2026-01-08', quote_at=at('2026-01-08T19:00').isoformat())
    assert freshness(older, now=at('2026-01-10T12:00'), contract=c).qualified


@pytest.mark.parametrize('day,kind,fallback,qualified', [
    ('2026-01-09', 'daily_settlement', False, False),
    ('2026-01-08', 'daily_settlement', False, True),
    ('2026-01-09', 'manual', False, True),
    ('2026-01-09', 'manual', True, True),
])
def test_only_latest_session_settlement_is_unqualified_when_closed(day, kind, fallback, qualified):
    c = contract()
    q = PriceObservation(c.id, day, 100, kind=kind)
    assert freshness(q, now=at('2026-01-10T12:00'), contract=c, fallback=fallback).qualified == qualified
    assert freshness(q, now=at('2026-01-12T00:11'), contract=c, fallback=fallback).qualified


def test_easter_and_currency_payment_holiday_are_distinct():
    c = contract('FMWO')
    assert not market_session(c, at('2026-04-06T12:00')).is_open
    assert trading_age(c, at('2026-04-02T19:45'), at('2026-04-07T00:20')) == timedelta(minutes=25)
    assert market_session(c, at('2026-01-19T12:00')).is_open
    assert not market_session(contract('FTUK'), at('2026-05-04T12:00')).is_open
    assert market_session(c, at('2026-05-04T12:00')).is_open


def test_dst_openings_and_unverified_schedules():
    c = contract()
    for stamp, local_hour in [('2026-03-27T10:00', 1), ('2026-03-30T10:00', 2),
                              ('2026-10-23T10:00', 2), ('2026-10-26T10:00', 1)]:
        session = market_session(c, at(stamp))
        assert session.opening.hour == local_hour
        assert session.opening.astimezone(at(stamp).tzinfo).hour == 0
    assert market_session(replace(c, exchange='UNKNOWN'), at('2026-01-10T12:00')) is None
    assert market_session(c, at('2027-03-19T12:00')) is None
    assert market_session(make_contract('FMWN', '2032-03-19'), at('2031-01-10T12:00')) is None
    assert trading_age(c, at('2026-01-10T11:45'), at('2026-01-10T12:00')) is None
