"""Explicitly invented account funding is distinct from an existing snapshot."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from portfolio_app.account_journal import Account, Event, Journal, replay
from portfolio_app.history_observations import HistoryScope, ObservationStore
from portfolio_app.personal_performance import HistoryCalculation, calculate_history, value_at
from portfolio_app.holdings import DataError


def newly_funded():
    return Journal(accounts=(Account('a', 'Invented new account'),), events=(
        Event('zero', '2024-01-03', 'cash_open', 'a', 'EUR', D(0), source='confirmed_zero_start'),
        Event('fund', '2024-01-03', 'cash_flow', 'a', 'EUR', D(100)),
        Event('income', '2024-01-04', 'interest', 'a', 'EUR', D(10)),
    ))


def test_explicit_inception_supports_zero_prehistory_and_counts_funding_once():
    journal = newly_funded()
    scope, observations = HistoryScope(accounts=('a',)), ObservationStore()
    calculation = HistoryCalculation(journal, observations)
    for start in ('2024-01-01', '2024-01-03'):
        result = calculate_history(journal, scope, observations, start, '2024-01-04', calculation=calculation)
        assert result.opening_value == 0 and result.ending_value == 110
        assert result.net_contributions == 100 and result.gain == 10
        assert result.time_weighted == pytest.approx(.1)
        assert result.money_weighted is not None
        assert not result.value_reasons and not result.flow_reasons
    assert value_at(journal, scope, observations, '2024-01-02', 'EUR').amount == 0
    assert replay(journal).cash['a', 'EUR'] == 110


@pytest.mark.parametrize('amount', [0, 100])
def test_ordinary_opening_balance_never_proves_zero_before_it(amount):
    journal = Journal(accounts=(Account('a', 'Invented snapshot'),), events=(
        Event('open', '2024-01-03', 'cash_open', 'a', 'EUR', D(amount)),))
    scope = HistoryScope(accounts=('a',))
    assert value_at(journal, scope, ObservationStore(), '2024-01-02', 'EUR').amount is None
    result = calculate_history(journal, scope, ObservationStore(), '2024-01-01', '2024-01-04')
    assert result.time_weighted is None and result.money_weighted is None


def test_inception_cannot_contradict_earlier_activity_or_existing_balances():
    journal = newly_funded()
    for bad in (
        replace(journal, events=(replace(journal.events[0], amount=D(1)), *journal.events[1:])),
        replace(journal, events=(Event('earlier', '2024-01-02', 'cash_flow', 'a', 'EUR', D(10)), *journal.events)),
        replace(journal, events=(*journal.events, Event('old-debt', '2024-01-03', 'debt_open', 'a', 'EUR', D(10), reference='loan'))),
    ):
        with pytest.raises(DataError, match='inception'):
            replay(bad)


@pytest.mark.parametrize('explicit', [True, False])
def test_future_snapshot_does_not_imply_zero_contracts_after_account_start(explicit):
    from portfolio_app.account_journal import PositionSpec
    from portfolio_app.derivative_types import ContractSpec
    from portfolio_app.history_observations import Observation
    contract = ContractSpec('SYNTH', '2024-12-20', 'EUR', D(1), D(1))
    opening = Event('zero', '2024-01-01', 'cash_open', 'a', 'EUR', D(0) if explicit else D(100),
                    source='confirmed_zero_start' if explicit else 'manual')
    funding = (Event('fund', '2024-01-01', 'cash_flow', 'a', 'EUR', D(100)),) if explicit else ()
    snapshot = Event('contract', '2024-01-03', 'snapshot', 'a', 'EUR', position_id='p', quantity=1, price=D(50))
    journal = Journal(accounts=(Account('a', 'Invented'),), contracts=(contract,),
        positions=(PositionSpec('p', 'a', contract.id),), events=(opening, *funding, snapshot))
    scope = HistoryScope(accounts=('a',))
    observations = ObservationStore((Observation('mark', 'price', 'p', '2024-01-03', D(50), 'EUR'),))
    calculation = HistoryCalculation(journal, observations)
    for context in (None, calculation):
        before = value_at(journal, scope, observations, '2024-01-02', 'EUR', calculation=context)
        assert before.amount is None and any('snapshot' in reason for reason in before.reasons)
        assert value_at(journal, scope, observations, '2024-01-03', 'EUR', calculation=context).amount == 100
        prior = value_at(journal, scope, observations, '2023-12-31', 'EUR', calculation=context)
        assert prior.amount == (0 if explicit else None)
    result = calculate_history(journal, scope, observations, '2024-01-01', '2024-01-02', calculation=calculation)
    assert result.time_weighted is None and result.money_weighted is None
    traded = replace(journal, events=(*journal.events[:-1], replace(snapshot, kind='trade')))
    assert value_at(traded, scope, observations, '2024-01-02', 'EUR').amount == 100


def test_new_account_ui_saves_explicit_funding_without_changing_existing_account(tmp_path):
    from datetime import date
    from portfolio_app.account_journal import load_journal, save_journal
    from test_actual_portfolio import invented
    from test_accounts_ui import launch_actual
    from test_ui import by_label, activate
    _, journal, _ = invented(tmp_path)
    save_journal(tmp_path, journal, None)
    app = launch_actual(tmp_path)
    activate(app, 'Futures')
    by_label(app.get('button_group'), 'Account workspace').set_value('Cash & financing').run()
    by_label(app.selectbox, 'Cash action').set_value('Create account').run()
    by_label(app.date_input, 'Balance / financing date').set_value(date(2026, 1, 5)).run()
    by_label(app.text_input, 'Account name').set_value('Invented newly funded account').run()
    by_label(app.radio, 'Account start').set_value('Newly funded account').run()
    by_label(app.date_input, 'Funding date').set_value(date(2026, 1, 3)).run()
    by_label(app.text_input, 'Initial contribution (EUR)').set_value('500').run()
    by_label(app.button, 'Review changes').click().run()
    by_label(app.button, 'Save reviewed changes').click().run()
    assert not app.exception
    saved, _ = load_journal(tmp_path)
    assert saved.events[:-2] == journal.events
    zero, funding = saved.events[-2:]
    assert zero.kind == 'cash_open' and zero.amount == 0 and zero.source == 'confirmed_zero_start'
    assert funding.kind == 'cash_flow' and funding.amount == 500
    assert zero.date == funding.date == '2026-01-03'
    assert replay(saved).cash[funding.account_id, 'EUR'] == 500
