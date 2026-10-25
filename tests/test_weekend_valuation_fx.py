"""Invented cross-currency holdings; independent Friday-close return oracle."""
from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pytest

from portfolio_app.account_journal import Account, Event, Journal
from portfolio_app.history_observations import Observation as O, ObservationStore, CoverageReview, HistoryScope, rate
from portfolio_app.history_valuation import valuation_fx
from portfolio_app.history_view import history_view
from portfolio_app.personal_performance import calculate_history, value_at, convert
from portfolio_app.security_history import SecurityBook, SecurityEvent as E, Charge, history_fingerprint

FRIDAY, SUNDAY, MONDAY, TUESDAY = '2024-01-05', '2024-01-07', '2024-01-08', '2024-01-09'


def example(fee=0):
    events = (E('opening', 'opening', FRIDAY, quantity=1, amount=80, currency='EUR'),
              E('purchase', 'buy', MONDAY, quantity=1, amount=50, currency='EUR', charges_known=True,
                charges=(Charge(D(fee), 'EUR'),) if fee else ()))
    book = SecurityBook('invented', 'SYNTH', ('1', '80', 'EUR'), records=events,
                        active_ids=tuple(e.id for e in events))
    journal = Journal(version=2, securities=(book,))
    scope = HistoryScope(('invented',))
    prices = tuple(O('price-'+stamp, 'price', 'invented', stamp, D(amount), 'USD',
                     listing='INVENTED', calendar='XNYS', exchange_timezone='America/New_York')
                   for stamp, amount in ((FRIDAY, 100), (MONDAY, 80), (TUESDAY, 90)))
    fx = tuple(O('fx-'+stamp, 'fx', 'USD/EUR', stamp, D(amount), 'USD', target='EUR')
               for stamp, amount in ((FRIDAY, '.8'), (MONDAY, '.9'), (TUESDAY, '.9')))
    store = ObservationStore((*prices, *fx), (CoverageReview('review', scope, FRIDAY, TUESDAY, history_fingerprint(journal)),))
    return journal, scope, store


@pytest.mark.parametrize('fee', [0, 5])
def test_monday_contribution_uses_friday_equity_and_fx(fee):
    journal, scope, store = example(fee)
    result = calculate_history(journal, scope, store, FRIDAY, TUESDAY)
    # Friday equity = 1 * USD100 * .8 = EUR80. Monday inflow = EUR50 + fee.
    # Monday closing equity = 2 * USD80 * .9 = EUR144; Tuesday = EUR162.
    expected = (D(144)/(D(80)+D(50)+D(fee))) * (D(162)/D(144)) - 1
    assert result.time_weighted == pytest.approx(float(expected))
    assert result.gain == 162-80-50-fee
    assert result.money_weighted is not None
    assert rate(store, 'USD', 'EUR', SUNDAY) is None
    assert convert(D(10), 'USD', 'EUR', SUNDAY, store) is None
    assert journal.securities[0].records[1].date == MONDAY


def test_weekend_value_and_chart_retain_separate_price_and_fx_provenance():
    journal, scope, store = example()
    value = value_at(journal, scope, store, SUNDAY, 'EUR')
    assert value.amount == 80
    assert {(e.kind, e.as_of, e.requested_date, e.method) for e in value.evidence} == {
        ('price', FRIDAY, SUNDAY, 'closed_market'), ('fx', FRIDAY, SUNDAY, 'closed_fx_session')}
    view = history_view(journal, scope, store, 'All', today=date.fromisoformat(SUNDAY))
    assert view.points[-1]['Price as of'] == view.points[-1]['FX as of'] == FRIDAY
    assert len(store.observations) == 6  # No invented weekend observations.


def test_actual_weekend_rate_wins_and_friday_inverse_rate_is_supported():
    _, _, store = example()
    direct = next(o for o in store.observations if o.kind == 'fx' and o.date == FRIDAY)
    inverse = replace(direct, currency='EUR', target='USD', subject='EUR/USD', amount=D('1.25'))
    store = replace(store, observations=tuple(inverse if o == direct else o for o in store.observations))
    assert valuation_fx(store, 'USD', 'EUR', SUNDAY)[0] == D('.8')
    exact = O('sunday', 'fx', 'USD/EUR', SUNDAY, D('.85'), 'USD', target='EUR')
    store = replace(store, observations=(*store.observations, exact))
    result, evidence = valuation_fx(store, 'USD', 'EUR', SUNDAY)
    assert result == D('.85') and evidence[0].as_of == SUNDAY and evidence[0].method == 'close'


def test_missing_friday_weekday_and_holiday_rates_are_not_filled():
    thursday = O('thursday', 'fx', 'USD/EUR', '2024-01-04', D('.8'), 'USD', target='EUR')
    monday = replace(thursday, id='monday', date=MONDAY, amount=D('.9'))
    assert valuation_fx(ObservationStore((thursday, monday)), 'USD', 'EUR', SUNDAY)[0] is None
    friday = replace(thursday, id='friday', date=FRIDAY)
    assert valuation_fx(ObservationStore((friday,)), 'USD', 'EUR', MONDAY)[0] is None
    # A Thursday public holiday still needs that date's FX; an equity closure
    # cannot imply that the currency market was closed too.
    july = replace(thursday, date='2024-07-03')
    assert valuation_fx(ObservationStore((july,)), 'USD', 'EUR', '2024-07-04')[0] is None
    boundary = replace(friday, side='before', boundary='invented-flow')
    assert valuation_fx(ObservationStore((boundary,)), 'USD', 'EUR', SUNDAY)[0] is None


def test_conflicting_rates_are_not_hidden_and_cash_uses_same_weekend_policy():
    _, _, store = example()
    conflicting = O('inverse', 'fx', 'EUR/USD', FRIDAY, D(2), 'EUR', target='USD')
    with pytest.raises(ValueError, match='Conflicting'):
        valuation_fx(replace(store, observations=(*store.observations, conflicting)), 'USD', 'EUR', SUNDAY)
    journal = Journal(accounts=(Account('cash', 'Invented cash'),),
                      events=(Event('opening', FRIDAY, 'cash_open', 'cash', 'USD', D(100)),))
    result = value_at(journal, HistoryScope(accounts=('cash',)), store, SUNDAY, 'EUR')
    assert result.amount == 80 and result.evidence[0].as_of == FRIDAY


def test_weekday_price_gap_still_blocks_twr():
    journal, scope, store = example()
    store = replace(store, observations=tuple(o for o in store.observations if not (o.kind == 'price' and o.date == FRIDAY)))
    assert value_at(journal, scope, store, SUNDAY, 'EUR').amount is None


def test_new_year_contribution_uses_previous_close_without_a_holiday_rate():
    journal, scope, store = example()
    stamps = {FRIDAY: '2024-12-31', MONDAY: '2025-01-02', TUESDAY: '2025-01-03'}
    book = journal.securities[0]
    journal = replace(journal, securities=(replace(book, records=tuple(replace(e, date=stamps[e.date]) for e in book.records)),))
    store = replace(store, observations=tuple(replace(o, date=stamps[o.date]) for o in store.observations),
        reviews=(CoverageReview('review', scope, stamps[FRIDAY], stamps[TUESDAY], history_fingerprint(journal)),))
    holiday = value_at(journal, scope, store, '2025-01-01', 'EUR')
    assert holiday.amount == 80
    assert {e.as_of for e in holiday.evidence} == {'2024-12-31'}
    result = calculate_history(journal, scope, store, stamps[FRIDAY], stamps[TUESDAY])
    assert result.time_weighted == pytest.approx(162/130-1)
    assert convert(D(10), 'USD', 'EUR', '2025-01-01', store) is None


def test_documented_holiday_weekend_chain_and_unsupported_dates():
    close = O('close', 'fx', 'USD/EUR', '2020-12-31', D('.8'), 'USD', target='EUR')
    store = ObservationStore((close,))
    assert valuation_fx(store, 'USD', 'EUR', '2021-01-03')[0] == D('.8')
    # Missing the last scheduled close still blocks: never skip a trading day.
    assert valuation_fx(ObservationStore((replace(close, date='2020-12-30'),)), 'USD', 'EUR', '2021-01-03')[0] is None
    for previous, requested in (('2025-04-17', '2025-04-18'), ('2017-12-29', '2018-01-01')):
        # Good Friday has regular FX hours; the other holiday is undocumented.
        assert valuation_fx(ObservationStore((replace(close, date=previous),)), 'USD', 'EUR', requested)[0] is None
    unsupported = replace(close, date='2024-12-31', currency='TRY', subject='TRY/EUR')
    assert valuation_fx(ObservationStore((unsupported,)), 'TRY', 'EUR', '2025-01-01')[0] is None
