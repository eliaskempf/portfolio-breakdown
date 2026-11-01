"""Invented account funding and futures marks; no market requests."""
from dataclasses import replace
from datetime import date

import pytest

from portfolio_app.account_journal import Account, Event, Journal, PositionSpec
from portfolio_app.derivative_market import make_contract
from portfolio_app.history_observations import HistoryScope, Observation, ObservationStore
from portfolio_app.history_valuation import price_at
from portfolio_app.history_view import history_view
from portfolio_app.personal_performance import calculate_history, value_at


def example():
    contract = make_contract('FMWN', '2027-03-19')
    journal = Journal(accounts=(Account('a', 'Invented account'),), contracts=(contract,),
        positions=(PositionSpec('p', 'a', contract.id),), events=(
            Event('cash', '2026-01-09', 'cash_open', 'a', 'EUR', 1000),
            Event('position', '2026-01-09', 'snapshot', position_id='p', quantity=1, price=100),
            Event('funding', '2026-01-11', 'cash_flow', 'a', 'EUR', 100)))
    store = ObservationStore((
        Observation('friday', 'price', contract.id, '2026-01-09', 100, 'EUR', source='Invented settlement'),
        Observation('monday', 'price', contract.id, '2026-01-12', 110, 'EUR', source='Invented settlement')))
    return contract, journal, HistoryScope(accounts=('a',)), store


def test_weekend_funding_has_twr_and_retains_original_settlement_provenance():
    contract, journal, scope, store = example()
    weekend = value_at(journal, scope, store, '2026-01-11', 'EUR')
    assert weekend.amount == 1100
    assert {(e.as_of, e.requested_date, e.method) for e in weekend.evidence} == {
        ('2026-01-09', '2026-01-11', 'closed_market')}
    result = calculate_history(journal, scope, store, '2026-01-09', '2026-01-12')
    assert result.time_weighted == pytest.approx(float(10*contract.multiplier/1100))
    assert result.net_contributions == 100 and result.gain == 10*contract.multiplier
    view = history_view(journal, scope, store, 'All', today=date(2026, 1, 11))
    assert view.all_end == '2026-01-11'
    assert view.points[-1]['Price as of'] == '2026-01-09'
    assert len(store.observations) == 2  # No fabricated weekend mark or posting.


@pytest.mark.parametrize('observed,requested,available', [
    ('2026-01-09', '2026-01-10', True),
    ('2026-01-09', '2026-01-11', True),
    ('2026-01-08', '2026-01-11', False),
    ('2026-01-09', '2026-01-12', False),
    ('2026-04-02', '2026-04-06', True),
    ('2026-04-02', '2026-04-07', False),
])
def test_only_verified_closures_allow_exact_contract_mark_reuse(observed, requested, available):
    contract, _, _, store = example()
    mark = replace(store.observations[0], date=observed)
    store = ObservationStore((mark,))
    assert (price_at(store, (contract.id,), requested, contract=contract) is not None) == available
    assert price_at(store, (contract.id,), requested, contract=replace(contract, exchange='UNKNOWN')) is None


def test_expiry_session_does_not_reuse_the_previous_mark():
    contract = make_contract('FMWN', '2026-03-20')
    store = ObservationStore((Observation('before-expiry', 'price', contract.id, '2026-03-19', 100, 'EUR'),))
    assert price_at(store, (contract.id,), '2026-03-21', contract=contract) is None


def test_missing_session_still_blocks_twr_and_explicit_weekend_mark_wins():
    contract, journal, scope, store = example()
    missing = replace(store, observations=(replace(store.observations[0], date='2026-01-08'), store.observations[1]))
    assert calculate_history(journal, scope, missing, '2026-01-09', '2026-01-12').time_weighted is None
    weekend = replace(store.observations[0], id='sunday', date='2026-01-11', amount=105)
    exact = replace(store, observations=(*store.observations, weekend))
    assert price_at(exact, (contract.id,), weekend.date, contract=contract) == weekend
