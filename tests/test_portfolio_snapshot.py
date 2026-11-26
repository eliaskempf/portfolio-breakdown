"""A prior close uses applied dated evidence, including books since liquidated."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from portfolio_app.ledger.account_journal import Account, Event, Journal
from portfolio_app.ledger.security_history import SecurityBook, SecurityEvent
from portfolio_app.performance.history_observations import HistoryScope, Observation, ObservationStore
from portfolio_app.performance.personal_performance import HistoryCalculation
from portfolio_app.performance.portfolio_snapshot import portfolio_snapshot


def example(*, sold=True):
    opening = SecurityEvent('open', 'opening', '2024-01-03', quantity=D(5), amount=D(50), currency='EUR')
    sale = SecurityEvent('sale', 'sell', '2024-02-01', quantity=D(5), price=D(13), currency='EUR', charges_known=True)
    later = SecurityEvent('future-note', 'note', '2024-03-01', note='Review required')
    events = (opening, sale, later) if sold else (opening,)
    book = SecurityBook('p', 'instrument', ('999', '999', 'GBP'), records=events, active_ids=tuple(e.id for e in events))
    return Journal(version=2, securities=(book,))


def price(identity, stamp, value, *, currency='EUR', **kwargs):
    return Observation(identity, 'price', 'p', stamp, D(value), currency, **kwargs)


def test_snapshot_matches_cutoff_after_later_sale_note_and_quote():
    scope = HistoryScope(('p',))
    historical = (price('a', '2024-01-03', '10'), price('b', '2024-01-04', '12'))
    full = HistoryCalculation(example(), ObservationStore((*historical, price('c', '2024-02-05', '10000'))))
    prefix = HistoryCalculation(example(sold=False), ObservationStore(historical))
    result = portfolio_snapshot(full, scope, '2024-01-04', 'EUR')
    assert result == portfolio_snapshot(prefix, scope, '2024-01-04', 'EUR')
    assert result.rows[0].quantity == 5
    assert result.value.amount == 60 and result.gain.amount == 10
    assert result.time_weighted == pytest.approx(.2)
    assert result.value.evidence[0].as_of == '2024-01-04'
    # Repeating a selected date reuses the existing calculation and immutable statement.
    assert portfolio_snapshot(full, scope, '2024-01-04', 'EUR') is result
    assert portfolio_snapshot(full, scope, '2024-02-01', 'EUR').rows == ()


def test_missing_fx_and_preopening_do_not_use_later_values_or_summary():
    full = HistoryCalculation(example(), ObservationStore((price('a', '2024-01-04', '10', currency='USD'),
        Observation('later', 'fx', 'USDEUR', '2024-01-05', D('.9'), 'USD', target='EUR'))))
    scope = HistoryScope(('p','summary-only'))
    result = portfolio_snapshot(full, scope, '2024-01-04', 'EUR')
    assert [r.quantity for r in result.rows] == [5, None]
    assert all(r.value.amount is None for r in result.rows)
    assert result.value.amount is result.time_weighted is None
    assert 'FX' in str(result.rows[0].value.reasons)
    before = portfolio_snapshot(full, scope, '2023-12-31', 'EUR')
    assert before.return_start is before.gain.amount is None
    assert all(r.quantity is None for r in before.rows)


def test_weekend_fx_with_known_subtotal_and_supplied_total():
    j = example()
    store = ObservationStore((price('close', '2024-01-05', '12', currency='USD', listing='TEST',
        calendar='XNYS', exchange_timezone='America/New_York'),
        Observation('rate', 'fx', 'USDEUR', '2024-01-05', D('.9'), 'USD', target='EUR')))
    scope = HistoryScope(('p', 'missing'))
    result = portfolio_snapshot(HistoryCalculation(j, store), scope, '2024-01-07', 'EUR')
    row = next(r for r in result.rows if r.identity == 'p')
    assert row.value.amount == 54 and result.value.amount is None
    assert {e.method for e in row.value.evidence} == {'closed_market', 'closed_fx_session'}
    total = Observation('invented-total', 'value', scope.key, '2024-01-07', D(88), 'EUR')
    supplied = portfolio_snapshot(HistoryCalculation(j, replace(store, observations=(*store.observations,total))),
                                  scope, '2024-01-07', 'EUR')
    assert supplied.value.supplied and supplied.value.amount == 88
    assert supplied.rows[0].quantity is None and supplied.rows[0].value.amount is None


def test_account_equity_shows_securities_separately_and_ignores_later_cash():
    j = example()
    j = replace(j, accounts=(Account('a', 'Invented cash'),),
                events=(Event('open-cash', '2024-01-03', 'cash_open', 'a', 'EUR', D(200)),
                        Event('later-cash', '2024-03-03', 'cash_adjust', 'a', 'EUR', D(999))),
                securities=(replace(j.securities[0], account_id='a'),))
    store = ObservationStore((price('open-price', '2024-01-03', '10'), price('prior', '2024-01-04', '12')))
    result = portfolio_snapshot(HistoryCalculation(j, store), HistoryScope(('p',), ('a',)), '2024-01-04', 'EUR')
    assert [(r.kind,r.value.amount) for r in result.rows] == [('Security',60),('Account equity',200)]
    assert result.value.amount == 260 and result.gain.amount == 10
    assert result.time_weighted == pytest.approx(.04)


def test_later_negative_observation_does_not_disqualify_earlier_return():
    scope = HistoryScope(('p',))
    marks = (price('a', '2024-01-03', '10'), price('b', '2024-01-04', '12'),
             Observation('negative', 'value', scope.key, '2024-01-05', -5, 'EUR'))
    calculation = HistoryCalculation(example(), ObservationStore(marks))
    assert portfolio_snapshot(calculation, scope, '2024-01-04', 'EUR').time_weighted == pytest.approx(.2)
    assert portfolio_snapshot(calculation, scope, '2024-01-05', 'EUR').time_weighted is None


def test_future_and_offsetting_debt_are_counted_only_in_the_account_equity():
    from test_weekend_futures_history import example as futures_example
    _, journal, scope, store = futures_example()
    loan = Event('initial-debt', '2026-01-09', 'debt_open', 'a', 'EUR', 1000, reference='invented-loan')
    journal = replace(journal, events=(*journal.events, loan))
    result = portfolio_snapshot(HistoryCalculation(journal, store), scope, '2026-01-09', 'EUR')
    assert result.value.amount == 0
    assert len(result.rows) == 1
    balances = result.rows[0].components
    assert {(c.label, c.amount) for c in balances if c.unit == 'EUR'} == {('Cash',1000),('Debt',-1000)}
    assert [c.amount for c in balances if c.unit == 'contracts'] == [1]


def test_selected_close_does_not_build_daily_series_or_repeat_work(monkeypatch):
    from datetime import date, timedelta
    opening = date(2024, 1, 3)
    marks = tuple(price(f'day-{n}', (opening + timedelta(days=n)).isoformat(), str(10+n/100))
                  for n in range(730))
    calculation = HistoryCalculation(example(sold=False), ObservationStore(marks))
    scope = HistoryScope(('p',))
    visited = []
    close = calculation.close
    def counted(scope, stamp, target):
        visited.append(stamp)
        return close(scope, stamp, target)
    monkeypatch.setattr(calculation, 'close', counted)
    stamp = marks[500].date
    view = portfolio_snapshot(calculation, scope, stamp, 'EUR')
    assert view.value.amount == 75 and view.time_weighted == pytest.approx(.5)
    assert set(visited) <= {opening.isoformat(), stamp}
    count = len(visited)
    assert portfolio_snapshot(calculation, scope, stamp, 'EUR') is view
    assert len(visited) == count


def test_receipt_moves_the_existing_claim_to_cash_only_on_its_own_date():
    from test_account_opening_claims import portfolio
    j = portfolio('sell', '2024-01-02', '2024-01-05', received='2024-01-05')
    scope = HistoryScope(('asset',), ('cash',))
    calc = HistoryCalculation(j, ObservationStore())
    def amounts(stamp):
        view = portfolio_snapshot(calc, scope, stamp, 'EUR')
        assert view.value.amount == 120
        return {c.label: c.amount for c in view.rows[0].components}
    assert amounts('2024-01-04') == {'Cash': 100, 'Outstanding payment': 20}
    assert amounts('2024-01-05') == {'Cash': 120}


def test_later_replacement_opening_does_not_requalify_an_earlier_gain():
    start = SecurityEvent('open', 'opening', '2024-01-01', source='confirmed_zero_start')
    buy = SecurityEvent('buy', 'buy', '2024-01-02', quantity=1, price=10, currency='EUR', charges_known=True)
    later = SecurityEvent('replacement', 'opening', '2024-02-01', quantity=1, amount=10, currency='EUR')
    book = SecurityBook('p', 'instrument', ('1','','EUR'), records=(start,buy,later),
                        active_ids=('open','buy','replacement'), account_id='a')
    scope = HistoryScope(('p',), ('a',))
    journal = Journal(version=2, accounts=(Account('a','Invented account'),), securities=(book,),
                      events=(Event('account', '2024-01-01', 'cash_open', 'a', 'EUR', 100),))
    store = ObservationStore((price('b', '2024-01-02', '12'),))
    result = portfolio_snapshot(HistoryCalculation(journal, store), scope, '2024-01-02', 'EUR')
    assert result.gain.amount == 2 and result.gain.status == 'Complete'


def test_tour_restores_date_view_and_originating_performance_period():
    from portfolio_app.interface.shared.state_lifecycle import capture_tour, transition
    state = {'strategic_historical_open': True, 'strategic_historical_date': '2025-01-02',
             'strategic_category': 'invented', 'strategic_view': 'Performance',
             'personal_history_invented_period': '3Y'}
    before = dict(state)
    captured = capture_tour(state)
    transition(state, 'tour')
    assert not state
    state.update(captured)
    assert state == before
