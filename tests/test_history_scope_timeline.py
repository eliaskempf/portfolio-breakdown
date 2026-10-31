"""Synthetic regressions: later cash facts must not disappear from returns."""
from dataclasses import replace
from datetime import date

import pytest

from portfolio_app.account_journal import Account, Event, Journal
from portfolio_app.gain_accounting import scope_dates
from portfolio_app.history_observations import CoverageReview, HistoryScope, Observation, ObservationStore
from portfolio_app.history_view import history_view
from portfolio_app.security_history import Charge, SecurityBook, SecurityEvent, history_fingerprint


@pytest.mark.parametrize('scope_kind', ['account', 'security_fee'])
def test_missing_ending_fx_cannot_hide_later_security_cash(scope_kind):
    events = (SecurityEvent('opening', 'opening', '2024-01-01', quantity=1, amount=100, currency='USD'),
        SecurityEvent('buy', 'buy', '2024-01-03' if scope_kind == 'account' else '2024-01-02',
            quantity=1, price=100, currency='USD', charges_known=True,
            charges=() if scope_kind == 'account' else (Charge(2, 'USD', '2024-01-03'),)))
    book = SecurityBook('asset', 'INVENTED', ('1', '100', 'USD'), account_id='a',
        records=events, active_ids=tuple(e.id for e in events))
    journal = Journal(version=2, accounts=(Account('a', 'Invented account'),), securities=(book,),
        events=(Event('cash', '2024-01-01', 'cash_open', account_id='a', currency='USD', amount=1000),))
    scope = HistoryScope(accounts=('a',)) if scope_kind == 'account' else HistoryScope(('asset',))
    store = ObservationStore(tuple(Observation('fx'+str(day), 'fx', 'USD/EUR', f'2024-01-{day:02}',
        '.9', 'USD', target='EUR') for day in (1, 2)) + tuple(
        Observation('price'+str(day), 'price', 'asset', f'2024-01-{day:02}', 100, 'USD') for day in (1, 2, 3)),
        (CoverageReview('review', scope, '2024-01-01', '2024-01-03', history_fingerprint(journal)),))
    assert scope_dates(journal, scope)[-1] == '2024-01-03'
    view = history_view(journal, scope, store, 'All', today=date(2024, 1, 3))
    assert view.all_end == '2024-01-02'
    assert view.all_result.time_weighted is None and view.all_result.money_weighted is None
    assert any('latest recorded event' in reason for reason in view.all_result.time_weighted_reasons)
    # Supplying the missing closing FX restores the full interval, not a shorter return.
    complete = replace(store, observations=(*store.observations,
        Observation('fx3', 'fx', 'USD/EUR', '2024-01-03', '.9', 'USD', target='EUR')))
    full = history_view(journal, scope, complete, 'All', today=date(2024, 1, 3))
    assert full.all_end == '2024-01-03'
    assert full.all_result.time_weighted == pytest.approx(0 if scope_kind == 'account' else 200/202-1)


def test_account_dates_exclude_security_opening_external_charges_and_unselected_facts():
    events = (SecurityEvent('opening', 'opening', '2023-01-01'),
        SecurityEvent('old-buy', 'buy', '2023-01-02', quantity=1, price=100, currency='EUR', charges_known=True),
        SecurityEvent('buy', 'buy', '2024-01-02', quantity=1, price=100, currency='EUR', charges_known=True,
            value_date='2024-01-05', charges=(Charge(1, 'EUR', '2024-01-03'), Charge(2, 'EUR', '2024-01-04', external=True))),
        SecurityEvent('pending', 'buy', '2024-01-10', quantity=1, price=100, currency='EUR'))
    book = SecurityBook('asset', 'INVENTED', ('0', '', ''), account_id='a', records=events,
        active_ids=('opening', 'old-buy', 'buy'))
    journal = Journal(version=2, accounts=(Account('a', 'Invented'),), securities=(book,),
        events=(Event('cash', '2024-01-01', 'cash_open', account_id='a', currency='EUR', amount=1000),))
    assert scope_dates(journal, HistoryScope(accounts=('a',))) == ['2024-01-01', '2024-01-02', '2024-01-03']
    # Value date alone doesn't post cash; an explicit receive event remains required.
    assert scope_dates(journal, HistoryScope(('asset',)))[-1] == '2024-01-04'


def test_receiving_position_includes_transfer_date_from_source_book():
    opening = SecurityEvent('source-open', 'opening', '2024-01-01', quantity=1, amount=100, currency='EUR')
    transfer = SecurityEvent('move', 'transfer', '2024-01-03', quantity=1, other_position_id='destination')
    source = SecurityBook('source', 'INVENTED', ('1', '100', 'EUR'), records=(opening, transfer),
        active_ids=(opening.id, transfer.id))
    destination = SecurityBook('destination', 'INVENTED', ('0', '', ''),
        records=(SecurityEvent('dest-open', 'opening', '2024-01-01'),), active_ids=('dest-open',))
    journal = Journal(version=2, securities=(source, destination))
    scope = HistoryScope(('destination',))
    assert scope_dates(journal, scope) == ['2024-01-01', '2024-01-03']
    view = history_view(journal, scope, ObservationStore(), 'All', today=date(2024, 1, 3))
    assert view.all_end == '2024-01-01'
    assert any('latest recorded event' in r for r in view.all_result.time_weighted_reasons)


def test_separate_fee_negative_equity_withholds_direct_and_chart_returns():
    from portfolio_app.personal_performance import calculate_history, known_negative_equity_dates, value_at
    events = (SecurityEvent('zero', 'opening', '2024-01-01'),
        SecurityEvent('buy', 'buy', '2024-01-02', quantity=1, price=10, currency='EUR', charges_known=True),
        SecurityEvent('sell', 'sell', '2024-01-03', quantity=1, price=10, currency='EUR', charges_known=True,
            charges=(Charge(200, 'EUR', '2024-01-04'),)))
    book = SecurityBook('asset', 'INVENTED', ('0', '', ''), account_id='a', records=events,
        active_ids=tuple(e.id for e in events))
    journal = Journal(version=2, accounts=(Account('a', 'Invented'),), securities=(book,), events=(
        Event('cash', '2024-01-01', 'cash_open', account_id='a', currency='EUR', amount=100),
        Event('income', '2024-01-05', 'interest', account_id='a', currency='EUR', amount=200)))
    scope = HistoryScope(('asset',), ('a',))
    store = ObservationStore((Observation('price', 'price', 'asset', '2024-01-02', 10, 'EUR'),),
        (CoverageReview('review', scope, '2024-01-01', '2024-01-05', history_fingerprint(journal)),))
    # Cash100 + trade round trip0 - deferred fee200 = -100, then income restores100.
    assert value_at(journal, scope, store, '2024-01-04', 'EUR').amount == -100
    assert known_negative_equity_dates(journal, scope, store, '2024-01-01', '2024-01-05') == ('2024-01-04',)
    direct = calculate_history(journal, scope, store, '2024-01-01', '2024-01-05')
    chart = history_view(journal, scope, store, 'All', today=date(2024, 1, 5)).all_result
    for result in (direct, chart):
        assert result.gain == 0
        assert result.time_weighted is None and result.money_weighted is None


def test_selected_month_cannot_publish_a_return_before_a_later_missing_event():
    events = (SecurityEvent('opening', 'opening', '2024-01-01'),
        SecurityEvent('buy', 'buy', '2024-04-03', quantity=1, price=100, currency='USD', charges_known=True))
    book = SecurityBook('asset', 'INVENTED', ('0', '', ''), account_id='a', records=events,
        active_ids=tuple(e.id for e in events))
    journal = Journal(version=2, accounts=(Account('a', 'Invented'),), securities=(book,),
        events=(Event('cash', '2024-01-01', 'cash_open', account_id='a', currency='USD', amount=1000),))
    scope = HistoryScope(accounts=('a',))
    store = ObservationStore(tuple(Observation('fx'+stamp, 'fx', 'USD/EUR', stamp, '.9', 'USD', target='EUR')
        for stamp in ('2024-01-01', '2024-04-01', '2024-04-02')),
        (CoverageReview('review', scope, '2024-01-01', '2024-04-03', history_fingerprint(journal)),))
    view = history_view(journal, scope, store, '1M', today=date(2024, 4, 3))
    assert view.start == '2024-04-01' and view.end == '2024-04-02'
    assert view.result.time_weighted is None and view.result.money_weighted is None
    assert any('latest recorded event' in r for r in view.result.time_weighted_reasons)


@pytest.mark.parametrize('movement', ['swap', 'asset_fee'])
def test_csv_companion_position_dates_include_later_atomic_movements(movement):
    from portfolio_app.security_history import AssetCharge
    opening = SecurityEvent('source-open', 'opening', '2024-01-01', quantity=2, amount=20, currency='EUR')
    event = (SecurityEvent('swap', 'swap', '2024-01-03', quantity=1,
                          other_position_id='destination', received_quantity=1)
             if movement == 'swap' else
             SecurityEvent('buy', 'buy', '2024-01-03', quantity=1, amount=10, currency='EUR',
                           charges_known=True, asset_charges=(AssetCharge('destination', 1),)))
    source = SecurityBook('source', 'INVENTED', ('2', '20', 'EUR'), records=(opening, event),
                          active_ids=(opening.id, event.id))
    destination = SecurityBook('destination', 'OTHER', ('2', '20', 'EUR'),
        records=(SecurityEvent('dest-open', 'opening', '2024-01-01', quantity=2, amount=20, currency='EUR'),),
        active_ids=('dest-open',))
    journal = Journal(version=4, securities=(source, destination))
    scope = HistoryScope(('destination',))
    assert scope_dates(journal, scope) == ['2024-01-01', '2024-01-03']
    store = ObservationStore((Observation('price', 'price', 'destination', '2024-01-01', 10, 'EUR'),))
    view = history_view(journal, scope, store, 'All', today=date(2024, 1, 3))
    assert any('latest recorded event' in r for r in view.all_result.time_weighted_reasons)
