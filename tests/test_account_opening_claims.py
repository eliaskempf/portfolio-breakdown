"""Ordinary settled-cash snapshots and the securities claims already owed at that boundary."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from portfolio_app.ledger.account_journal import Account, Event, Journal
from portfolio_app.ledger.security_history import SecurityBook, SecurityEvent
from portfolio_app.performance.history_observations import HistoryScope, Observation, ObservationStore
from portfolio_app.performance.personal_performance import (
    HistoryCalculation, calculate_history, estimated_return_inputs, opening_value_at,
)

OPEN, END = '2024-01-03', '2024-01-06'


def portfolio(kind, trade, due, *, received='', estimated=False, currency='EUR', amount='20'):
    opening = SecurityEvent('inventory', 'opening', '2024-01-01', quantity=int(kind == 'sell'),
                            amount=20 if kind == 'sell' else 0, currency=currency)
    event = SecurityEvent('payment', kind, trade, quantity=int(kind in ('buy', 'sell')), amount=amount,
                          currency=currency, charges_known=True, value_date=due, estimated=estimated)
    book = SecurityBook('asset', 'invented', ('1', '20', currency), account_id='cash',
                        records=(opening, event), active_ids=(opening.id, event.id))
    events = (Event('open', OPEN, 'cash_open', 'cash', currency, 100),)
    if received:
        events += (Event('received', received, 'receive', 'cash', currency, reference='security:payment'),)
    return Journal(version=5, accounts=(Account('cash', 'Invented'),), securities=(book,), events=events)


def test_settled_purchase_before_cash_snapshot_still_qualifies_its_dated_flow():
    before, trade = '2024-01-01', '2024-01-02'
    journal = portfolio('buy', trade, trade, estimated=True)
    scope = HistoryScope(accounts=('cash',))
    calc = HistoryCalculation(journal, ObservationStore())
    assert trade not in calc.event_dates(scope)  # No separate change to the settled cash balance.
    first = calc.period_capital(scope, before, before, 'EUR')
    next_day = calc.period_capital(scope, before, trade, 'EUR')
    assert first.net_contributions == 0 and not first.estimated
    assert next_day.net_contributions == -20 and next_day.estimated
    assert first.opening_value.amount is None and next_day.opening_value.amount is None


@pytest.mark.parametrize('trade,due,received', [
    (OPEN, END, ''), (OPEN, END, END), ('2024-01-02', OPEN, OPEN), ('2024-01-02', OPEN, ''),
    (OPEN, OPEN, ''),
])
@pytest.mark.parametrize('kind', ['buy', 'sell'])
def test_existing_snapshot_counts_deferred_securities_payments_once(trade, due, received, kind):
    deferred = due > trade
    journal = portfolio(kind, trade, due, received=received, estimated=True)
    store = ObservationStore(tuple(Observation(d, 'price', 'asset', d, 20, 'EUR') for d in (OPEN, END)))
    calculation = HistoryCalculation(journal, store)
    for positions in ((), ('asset',)):
        scope = HistoryScope(positions, ('cash',))
        expected = 100 + ((-20 if kind == 'buy' else 20) if deferred else 0) + (20 if positions and kind == 'buy' else 0)
        for context in (None, calculation):
            assert opening_value_at(journal, scope, store, OPEN, 'EUR', calculation=context).amount == expected
            r = calculate_history(journal, scope, store, OPEN, END, calculation=context)
            assert r.opening_value == r.ending_value == expected
            assert r.net_contributions == r.gain == r.time_weighted == 0
            assert r.estimated == deferred
        assert estimated_return_inputs(journal, scope, OPEN, END, calculation=calculation) == deferred


def test_unsettled_unknown_payment_never_supplies_zero_opening_equity():
    journal = portfolio('buy', OPEN, END, amount=None)
    scope = HistoryScope(accounts=('cash',))
    store = ObservationStore((Observation('supplied-close', 'value', scope.key, END, 100, 'EUR'),))
    opening = opening_value_at(journal, scope, store, OPEN, 'EUR')
    assert opening.amount is None and any(r.kind == 'transaction' for r in opening.reasons)
    result = calculate_history(journal, scope, store, OPEN, END)
    assert result.ending_value == 100 and result.gain is None and result.time_weighted is None


def test_snapshot_uses_rounded_account_payment_and_dated_currency_evidence():
    journal = portfolio('buy', OPEN, END, currency='USD', amount='20.017')
    journal = replace(journal, events=(*journal.events, Event('fee', OPEN, 'fee', 'cash', 'USD', 5)))
    scope = HistoryScope(accounts=('cash',))
    marks = tuple(Observation(d, 'fx', 'USD/EUR', d, D('.8'), 'USD', target='EUR') for d in (OPEN, END))
    store = ObservationStore(marks)
    r = calculate_history(journal, scope, store, OPEN, END)
    assert r.opening_value == D('79.98') * D('.8')
    assert r.ending_value == D('74.98') * D('.8')
    assert r.gain == -4
    missing = opening_value_at(journal, scope, ObservationStore(marks[1:]), OPEN, 'EUR')
    assert missing.amount is None and all(reason.kind == 'fx' for reason in missing.reasons)


@pytest.mark.parametrize('cash', [0, 100])
def test_weekend_cash_and_receivable_share_the_supported_fx_close(cash):
    saturday, monday = '2024-01-06', '2024-01-08'
    journal = portfolio('distribution', saturday, monday, currency='USD')
    journal = replace(journal, events=(Event('opening', saturday, 'cash_open', 'cash', 'USD', cash),))
    marks = tuple(Observation(d, 'fx', 'USD/EUR', d, rate, 'USD', target='EUR') for d, rate in (
        ('2024-01-05', D('.8')), (monday, D('.9'))))
    store = ObservationStore(marks)
    scope = HistoryScope(accounts=('cash',))
    r = calculate_history(journal, scope, store, saturday, monday)
    assert r.opening_value == (cash+20)*D('.8') and r.ending_value == (cash+20)*D('.9')
    assert r.gain == (cash+20)*D('.1')
    assert r.time_weighted == pytest.approx(.125)


def test_confirmed_new_funding_keeps_the_deferred_payment_out_of_opening_equity():
    journal = portfolio('buy', OPEN, END, estimated=True)
    book = journal.securities[0]
    # Confirm the security was empty just before the new account's first day.
    empty = replace(book.records[0], date='2024-01-02', source='confirmed_zero_start')
    journal = replace(journal, securities=(replace(book, records=(empty, book.records[1])),),
                      events=(Event('zero', OPEN, 'cash_open', 'cash', 'EUR', 0, source='confirmed_zero_start'),
                              Event('deposit', OPEN, 'cash_flow', 'cash', 'EUR', 100)))
    store = ObservationStore(tuple(Observation(d, 'price', 'asset', d, 20, 'EUR') for d in (OPEN, END)))
    for positions, net in (((), 80), (('asset',), 100)):
        scope = HistoryScope(positions, ('cash',))
        r = calculate_history(journal, scope, store, OPEN, END)
        assert r.opening_value == 0 and r.net_contributions == r.ending_value == net
        assert r.gain == r.time_weighted == 0 and r.estimated


def test_corrected_trade_after_the_opening_is_a_later_withdrawal():
    journal = portfolio('buy', OPEN, END)
    book = journal.securities[0]
    corrected = replace(book.records[-1], date='2024-01-04', id='changed', replaces='payment')
    book = replace(book, records=(*book.records, corrected), active_ids=('inventory', 'changed'))
    journal = replace(journal, securities=(book,))
    r = calculate_history(journal, HistoryScope(accounts=('cash',)), ObservationStore(), OPEN, END)
    assert r.opening_value == 100 and r.net_contributions == -20 and r.ending_value == 80
    assert r.gain == r.time_weighted == 0


def test_a_preopening_payment_does_not_establish_earlier_account_equity():
    from datetime import date
    from portfolio_app.performance.history_view import history_view

    earlier = '2024-01-02'
    journal = portfolio('sell', earlier, END)
    scope = HistoryScope(accounts=('cash',))
    view = history_view(journal, scope, ObservationStore(), 'All', today=date.fromisoformat(END))
    assert view.all_start == earlier and view.all_result.opening_value is None
    assert view.all_result.time_weighted is None
    assert calculate_history(journal, scope, ObservationStore(), OPEN, END).time_weighted == 0


@pytest.mark.parametrize('account_first', [True, False])
def test_opening_reuses_verified_cash_postings_from_the_same_journal(monkeypatch, account_first):
    from portfolio_app.performance import personal_performance as performance

    journal = portfolio('sell', OPEN, END)
    scope = HistoryScope(accounts=('cash',))
    calc = HistoryCalculation(journal, ObservationStore())
    if account_first:
        assert calc.accounts(OPEN).pending['security:payment'].amount == 20
    else:
        assert calc.opening(scope, OPEN, 'EUR').amount == 120

    def unexpected(*args, **kwargs):
        pytest.fail('The same validated security cash was derived again for the opening')

    monkeypatch.setattr(performance, 'cash_postings', unexpected)
    assert calc.accounts(OPEN).pending['security:payment'].amount == 20
    assert calc.opening(scope, OPEN, 'EUR').amount == 120
    assert calc.opening(scope, END, 'EUR').amount == 120


def test_opening_values_cash_debt_and_claim_with_the_same_closing_fx(monkeypatch):
    from portfolio_app.performance import personal_performance as performance

    journal = portfolio('sell', OPEN, END, currency='USD')
    journal = replace(journal, events=(*journal.events, Event('loan', OPEN, 'debt_open', 'cash', 'USD', 10, reference='loan')))
    store = ObservationStore((Observation('flow', 'fx', 'USD/EUR', OPEN, D('.4'), 'USD', target='EUR',
                                          side='after', boundary='funding'),
                              Observation('close', 'fx', 'USD/EUR', OPEN, D('.8'), 'USD', target='EUR')))
    calls = []
    original = performance.valuation_fx_quotes
    def rate(*args):
        calls.append(args[1:])
        return original(*args)
    monkeypatch.setattr(performance, 'valuation_fx_quotes', rate)
    scope = HistoryScope(accounts=('cash',))
    assert opening_value_at(journal, scope, store, OPEN, 'EUR').amount == 88
    assert calls == [('USD', 'EUR', OPEN)]
