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


@pytest.mark.parametrize('start', ['2024-01-01', '2024-01-02'])
@pytest.mark.parametrize('include_security', [False, True])
@pytest.mark.parametrize('fee', [0, 1])
def test_first_day_linked_purchase_counts_cash_and_funding_once(start, include_security, fee):
    from portfolio_app.security_history import Charge, SecurityBook, SecurityEvent
    from portfolio_app.history_observations import Observation
    events = (SecurityEvent('empty', 'opening', '2024-01-02', source='confirmed_zero_start'),
              SecurityEvent('buy', 'buy', '2024-01-02', sequence=1, quantity=1, price=100,
                            currency='EUR', charges=(Charge(D(fee), 'EUR'),), charges_known=True))
    book = SecurityBook('stock', 'invented', ('0', '', ''), account_id='a', records=events,
                        active_ids=tuple(e.id for e in events))
    journal = Journal(version=5, accounts=(Account('a', 'Invented new account'),), securities=(book,), events=(
        Event('zero', '2024-01-02', 'cash_open', 'a', 'EUR', D(0), source='confirmed_zero_start'),
        Event('fund', '2024-01-02', 'cash_flow', 'a', 'EUR', D(100 + fee), sequence=1)))
    observations = ObservationStore((Observation('price', 'price', 'stock', '2024-01-03', D(100), 'EUR'),))
    scope = HistoryScope(('stock',) if include_security else (), ('a',))
    calculation = HistoryCalculation(journal, observations)
    assert replay(journal).cash['a', 'EUR'] == 0
    assert calculation.accounts('2024-01-02').cash['a', 'EUR'] == 0
    result = calculate_history(journal, scope, observations, start, '2024-01-03', calculation=calculation)
    assert result.opening_value == 0
    assert result.ending_value == (100 if include_security else 0)
    assert result.net_contributions == (100 + fee if include_security else 0)
    assert result.gain == (-fee if include_security else 0)
    assert result.opening_capital == 0
    assert result.invested_capital == (100 + fee if include_security else 0)
    assert result.total_gain == result.gain
    assert result.time_weighted == pytest.approx(-fee / (100 + fee) if include_security else 0)
    assert not result.value_reasons and not result.flow_reasons


def test_inception_cannot_contradict_earlier_activity_or_existing_balances():
    journal = newly_funded()
    for bad in (
        replace(journal, events=(replace(journal.events[0], amount=D(1)), *journal.events[1:])),
        replace(journal, events=(Event('earlier', '2024-01-02', 'cash_flow', 'a', 'EUR', D(10)), *journal.events)),
        replace(journal, events=(*journal.events, Event('old-debt', '2024-01-03', 'debt_open', 'a', 'EUR', D(10), reference='loan'))),
    ):
        with pytest.raises(DataError, match='inception'):
            replay(bad)


def test_first_day_import_zero_opening_agrees_with_account_inception(tmp_path):
    from portfolio_app.account_journal import save_journal, load_journal
    from portfolio_app.history_commands import reconstruction_candidate
    from portfolio_app.security_history import SecurityBook, SecurityEvent
    buy = SecurityEvent('buy', 'buy', '2024-01-03', quantity=1, price=100, currency='EUR', charges_known=True)
    book = reconstruction_candidate(SecurityBook('stock', 'invented', ('0', '', ''), account_id='a',
        records=(buy,)), ('buy',), starts_at_zero=True)
    assert book.records[-1].date == '2024-01-02'
    journal = replace(newly_funded(), version=5, securities=(book,))
    save_journal(tmp_path, journal, None)
    assert replay(load_journal(tmp_path)[0]).cash['a', 'EUR'] == 10
    opening = next(e for e in book.records if e.kind == 'opening')
    for bad in (replace(opening, source='manual'), replace(opening, quantity=D(1))):
        changed = replace(book, records=tuple(bad if e.id == opening.id else e for e in book.records))
        with pytest.raises(DataError, match='inception'):
            replay(replace(journal, securities=(changed,)))


def test_note_correction_retains_confirmed_zero_inception():
    from portfolio_app.account_activity import correct_entry
    from portfolio_app.account_inception import account_inceptions
    corrected = correct_entry(newly_funded(), 'zero', {'note': 'Invented clarification'})
    assert account_inceptions(corrected) == {'a': '2024-01-03'}
    assert corrected.events[-1].replaces == 'zero'
    result = calculate_history(corrected, HistoryScope(accounts=('a',)), ObservationStore(), '2024-01-01', '2024-01-04')
    assert result.gain == 10 and result.time_weighted == pytest.approx(.1)


def test_internal_transfer_across_mixed_opening_boundaries_is_not_profit():
    journal = Journal(accounts=(Account('old', 'Invented existing'), Account('new', 'Invented new')), events=(
        Event('old-open', '2024-01-01', 'cash_open', 'old', 'EUR', D(100)),
        Event('new-open', '2024-01-02', 'cash_open', 'new', 'EUR', D(0), source='confirmed_zero_start'),
        Event('move', '2024-01-02', 'transfer', 'old', 'EUR', D(100), other_account_id='new')))
    scope = HistoryScope(accounts=('old', 'new'))
    observations = ObservationStore()
    before = calculate_history(journal, scope, observations, '2024-01-01', '2024-01-03')
    assert before.gain == 0 and before.time_weighted == 0
    mixed = calculate_history(journal, scope, observations, '2024-01-02', '2024-01-03')
    assert mixed.ending_value == 100 and mixed.gain is None and mixed.time_weighted is None
    assert any('different opening boundaries' in reason for reason in mixed.flow_reasons)


@pytest.mark.parametrize('kind', ['transfer_in', 'inventory_in'])
def test_first_day_external_security_inflow_is_a_contribution(kind):
    from portfolio_app.security_history import SecurityBook, SecurityEvent
    from portfolio_app.history_observations import Observation
    fields = {'amount': D(100), 'currency': 'EUR'} if kind == 'transfer_in' else {
        'valuation_amount': D(100), 'valuation_currency': 'EUR', 'flow_classification': 'external'}
    entries = (SecurityEvent('empty', 'opening', '2024-01-02', source='confirmed_zero_start'),
               SecurityEvent('gift', kind, '2024-01-02', quantity=1, charges_known=True, **fields))
    book = SecurityBook('stock', 'invented', ('0', '', ''), account_id='a', records=entries,
                        active_ids=tuple(e.id for e in entries))
    journal = Journal(version=5, accounts=(Account('a', 'Invented new'),), securities=(book,), events=(
        Event('zero', '2024-01-02', 'cash_open', 'a', 'EUR', D(0), source='confirmed_zero_start'),))
    observations = ObservationStore((Observation('price', 'price', 'stock', '2024-01-03', D(100), 'EUR'),))
    result = calculate_history(journal, HistoryScope(('stock',), ('a',)), observations, '2024-01-02', '2024-01-03')
    assert result.opening_value == 0 and result.ending_value == 100
    assert result.net_contributions == 100 and result.gain == 0 and result.time_weighted == 0
    assert result.opening_capital == 0 and result.invested_capital == 100 and result.total_gain == 0


def test_internal_security_transfer_with_mixed_openings_keeps_terminal_value():
    from portfolio_app.security_history import SecurityBook, SecurityEvent
    from portfolio_app.history_observations import Observation
    old_events = (SecurityEvent('old-open', 'opening', '2024-01-01', quantity=1, amount=100, currency='EUR'),
                  SecurityEvent('move', 'transfer', '2024-01-02', quantity=1, other_position_id='new'))
    old = SecurityBook('old', 'invented', ('1', '100', 'EUR'), records=old_events,
                       active_ids=tuple(e.id for e in old_events))
    new_events = (SecurityEvent('new-open', 'opening', '2024-01-02', source='confirmed_zero_start'),)
    new = SecurityBook('new', 'invented', ('0', '', ''), account_id='a', records=new_events,
                       active_ids=('new-open',))
    journal = Journal(version=5, accounts=(Account('a', 'Invented new'),), securities=(old, new), events=(
        Event('zero', '2024-01-02', 'cash_open', 'a', 'EUR', D(0), source='confirmed_zero_start'),))
    observations = ObservationStore((Observation('old-price', 'price', 'old', '2024-01-01', D(100), 'EUR'),
        Observation('new-price', 'price', 'new', '2024-01-03', D(100), 'EUR')))
    scope = HistoryScope(('old', 'new'), ('a',))
    before = calculate_history(journal, scope, observations, '2024-01-01', '2024-01-03')
    assert before.gain == 0 and before.time_weighted == 0
    mixed = calculate_history(journal, scope, observations, '2024-01-02', '2024-01-03')
    assert mixed.ending_value == 100 and mixed.gain is None and mixed.time_weighted is None
    assert any('different opening boundaries' in reason for reason in mixed.flow_reasons)


def test_first_day_reward_still_requires_confirmed_charges():
    from portfolio_app.security_history import SecurityBook, SecurityEvent
    from portfolio_app.history_observations import Observation
    entries = (SecurityEvent('empty', 'opening', '2024-01-02', source='confirmed_zero_start'),
               SecurityEvent('reward', 'reward', '2024-01-02', quantity=1,
                             valuation_amount=100, valuation_currency='EUR', charges_known=False))
    book = SecurityBook('stock', 'invented', ('0', '', ''), account_id='a', records=entries,
                        active_ids=tuple(e.id for e in entries))
    journal = Journal(version=5, accounts=(Account('a', 'Invented new'),), securities=(book,), events=(
        Event('zero', '2024-01-02', 'cash_open', 'a', 'EUR', D(0), source='confirmed_zero_start'),))
    observations = ObservationStore((Observation('price', 'price', 'stock', '2024-01-03', D(100), 'EUR'),))
    result = calculate_history(journal, HistoryScope(('stock',), ('a',)), observations, '2024-01-02', '2024-01-03')
    assert result.ending_value == 100 and result.gain is None and result.time_weighted is None
    assert any('charges have not been confirmed' in reason for reason in result.flow_reasons)


def test_fee_paid_with_existing_asset_cannot_disappear_at_new_account_boundary():
    from portfolio_app.security_history import AssetCharge, SecurityBook, SecurityEvent
    from portfolio_app.history_observations import Observation
    old_events = (SecurityEvent('old-open', 'opening', '2024-01-01', quantity=10, amount=100, currency='EUR'),)
    new_events = (SecurityEvent('new-zero', 'opening', '2024-01-02', source='confirmed_zero_start'),
        SecurityEvent('buy', 'buy', '2024-01-02', sequence=1, quantity=1, price=100, currency='EUR',
                      charges_known=True, asset_charges=(AssetCharge('old', D(1)),)))
    books = (SecurityBook('old', 'invented-old', ('10', '100', 'EUR'), records=old_events, active_ids=('old-open',)),
        SecurityBook('new', 'invented-new', ('0', '', ''), account_id='a', records=new_events, active_ids=('new-zero', 'buy')))
    journal = Journal(version=5, accounts=(Account('a', 'Invented new'),), securities=books, events=(
        Event('zero', '2024-01-02', 'cash_open', 'a', 'EUR', 0, source='confirmed_zero_start'),
        Event('fund', '2024-01-02', 'cash_flow', 'a', 'EUR', 100, sequence=1)))
    observations = ObservationStore(tuple(Observation(p+stamp, 'price', p, stamp,
        D(10) if p == 'old' else D(100), 'EUR') for p in ('old', 'new')
        for stamp in ('2024-01-01', '2024-01-02', '2024-01-03')))
    scope = HistoryScope(('old', 'new'), ('a',))
    before = calculate_history(journal, scope, observations, '2024-01-01', '2024-01-03')
    assert before.gain == -10 and before.time_weighted == pytest.approx(-.05)
    mixed = calculate_history(journal, scope, observations, '2024-01-02', '2024-01-03')
    assert mixed.ending_value == 190 and mixed.gain is None and mixed.time_weighted is None
    assert any('different opening boundaries' in reason for reason in mixed.flow_reasons)


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
