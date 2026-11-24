"""Standalone bank accounts, with invented journals, prices and categories."""
from dataclasses import replace

import pytest

from portfolio_app.application import load_leverage_context
from portfolio_app.derivatives.account_margin import margin_coverage
from portfolio_app.exposure.portfolio_exposure import calculate_portfolio_exposure
from portfolio_app.ledger import account_journal as ledger
from portfolio_app.ledger.bank_accounts import bank_position_ids, cash_statement, save_bank_account
from portfolio_app.performance.history_observations import HistoryScope, ObservationStore
from portfolio_app.performance.history_scope import category_history_scope
from portfolio_app.performance.personal_performance import calculate_history
from portfolio_app.portfolio.actual_portfolio import journal_positions
from portfolio_app.portfolio.allocation import load_allocation
from portfolio_app.portfolio.holdings import DataError
from portfolio_app.portfolio.portfolio import prepare_portfolio
from portfolio_app.portfolio.positions import read_snapshot
from portfolio_app.studies.futures_workspace import fork_futures_workspace
from portfolio_app.workspace.storage import revision
from test_actual_portfolio import invented


def save(directory, account, **options):
    save_bank_account(directory, account, holdings_revision=revision(directory/'holdings.csv'),
                      allocation_revision=revision(directory/'allocation.yaml'),
                      journal_revision=revision(directory/ledger.FILE), **options)
    return ledger.load_journal(directory)[0]


def bank(identity, kind='Savings'):
    return ledger.Account(identity, 'Invented ' + identity, 'bank', kind, 'EUR')


def test_opening_income_transfers_and_category_history(tmp_path):
    savings = bank('reserve')
    journal = save(tmp_path, savings, opening=10000, day='2026-01-01', create_category=True)
    savings = journal.accounts[0]
    journal = save(tmp_path, bank('checking', 'Checking'), opening=100, day='2026-01-01')
    journal = ledger.append_events(journal, (
        ledger.Event('deposit', '2026-01-02', 'cash_flow', savings.id, 'EUR', 500),
        ledger.Event('interest', '2026-01-03', 'interest', savings.id, 'EUR', 25),
        ledger.Event('transfer', '2026-01-04', 'transfer', 'reserve', 'EUR', 20, other_account_id='checking'),
    ))
    state = ledger.replay(journal)
    assert state.cash['reserve', 'EUR'] == 10505
    assert state.cash['checking', 'EUR'] == 120
    assert [row['Balance'] for row in cash_statement(state, journal, savings)] == [10505, 10525, 10500, 10000]
    assert cash_statement(state, journal, journal.accounts[1])[0]['Transaction'] == 'From Invented reserve'
    config = load_allocation(tmp_path/'allocation.yaml')
    scope = category_history_scope(journal, read_snapshot(tmp_path/'holdings.csv').holdings, config, savings.bucket_id)
    assert scope.accounts == ('reserve',)
    for selected, contributions in [(scope, 480), (HistoryScope(accounts=('reserve', 'checking')), 500)]:
        result = calculate_history(journal, selected, ObservationStore(), '2026-01-01', '2026-01-04')
        assert result.net_contributions == contributions and result.gain == 25
    ledger.save_journal(tmp_path, journal, revision(tmp_path/ledger.FILE))
    renamed = save(tmp_path, replace(savings, name='Invented house deposit', bucket_id=''))
    assert renamed.events == journal.events and bank_position_ids(renamed).keys() == bank_position_ids(journal).keys()


def test_savings_increases_equity_without_increasing_broker_cash(tmp_path):
    service, broker, _ = invented(tmp_path)
    ledger.save_journal(tmp_path, broker, None)
    mixed = save(tmp_path, bank('savings'), opening=5000, day='2026-01-02')
    original = read_snapshot(tmp_path/'holdings.csv').holdings
    valued = prepare_portfolio(original, service)
    def metrics(journal):
        projected = journal_positions(valued, journal, service)
        result = calculate_portfolio_exposure(projected, reporting_currency='EUR')
        return result.net_equity, result.market_amount(gross=True), margin_coverage(
            journal, original, projected, 'a', 'EUR').settled_cash
    equity, exposure, available = metrics(broker)
    assert metrics(mixed) == (equity+5000, exposure, available)


def test_equity_and_savings_share_portfolio_gain_and_return(tmp_path):
    from portfolio_app.ledger.security_history import SecurityBook, SecurityEvent
    from portfolio_app.performance.history_observations import Observation

    start, deposit, end = '2026-01-02', '2026-01-03', '2026-01-05'
    path = tmp_path/'holdings.csv'
    path.write_text('position_key,id,name,shares,acquisition_price,acquisition_currency\n'
                    'stock,stock,Invented equity,1,100,EUR\n')
    opening = SecurityEvent('opening', 'opening', start, quantity=1, amount=100, currency='EUR')
    stock = SecurityBook('stock', 'stock', ('1', '100', 'EUR'), records=(opening,), active_ids=(opening.id,))
    ledger.save_journal(tmp_path, ledger.Journal(version=7, securities=(stock,)), None)
    journal = save(tmp_path, bank('savings'), opening=200, day=start, create_category=True)
    journal = ledger.append_events(journal, (
        ledger.Event('deposit', deposit, 'cash_flow', 'savings', 'EUR', 100),
        ledger.Event('interest', end, 'interest', 'savings', 'EUR', 3)))
    quotes = ObservationStore((Observation('first', 'price', 'stock', start, 100, 'EUR'),
                               Observation('last', 'price', 'stock', end, 110, 'EUR')))
    scope = category_history_scope(journal, read_snapshot(path).holdings, load_allocation(tmp_path/'allocation.yaml'))
    whole = calculate_history(journal, scope, quotes, start, end)
    assert (whole.opening_value, whole.net_contributions, whole.ending_value) == (300, 100, 413)
    assert whole.gain == 13 and whole.time_weighted == pytest.approx(13/400)
    assert whole.money_weighted is not None


def test_later_cash_opening_enters_portfolio_as_capital_and_can_move_earlier(tmp_path):
    from datetime import date
    from portfolio_app.ledger.bank_accounts import bank_opening
    from portfolio_app.ledger.security_history import SecurityBook, SecurityEvent
    from portfolio_app.performance.history_observations import Observation
    from portfolio_app.performance.history_view import history_view

    start, prior, entry, end = '2026-01-02', '2026-05-31', '2026-06-01', '2026-06-03'
    opening = SecurityEvent('stock-opening', 'opening', start, quantity=1, amount=100, currency='EUR')
    stock = SecurityBook('stock', 'stock', ('1', '100', 'EUR'), records=(opening,), active_ids=(opening.id,))
    ledger.save_journal(tmp_path, ledger.Journal(version=7, securities=(stock,)), None)
    savings = bank('savings')
    journal = save(tmp_path, savings, opening=2000, day=entry)
    original = bank_opening(journal, savings)
    journal = ledger.append_event(journal, ledger.Event('interest', end, 'interest', savings.id, 'EUR', 20))
    ledger.save_journal(tmp_path, journal, revision(tmp_path/ledger.FILE))
    quotes = ObservationStore(tuple(Observation('quote-'+d, 'price', 'stock', d, p, 'EUR')
                                   for d, p in [(start, 100), (prior, 110), (entry, 110), (end, 110)]))
    scope = HistoryScope(('stock',), (savings.id,))
    view = history_view(journal, scope, quotes, 'All', today=date.fromisoformat(end))
    assert [(p['Value'], p['Invested capital'], p['Total gain']) for p in view.points] == [
        (100, 100, 0), (110, 100, 10), (2110, 2100, 10), (2130, 2100, 30)]
    assert view.all_result.net_contributions == 2000
    assert view.all_result.time_weighted == pytest.approx(1.1 * 2130/2110 - 1)
    assert view.all_result.money_weighted is not None
    cash = history_view(journal, HistoryScope(accounts=(savings.id,)), quotes, 'All', today=date.fromisoformat(end))
    assert cash.all_start == entry and cash.all_result.time_weighted == pytest.approx(.01)
    assert calculate_history(journal, scope, quotes, entry, end).net_contributions == 0

    with pytest.raises(DataError, match='first account transaction'):
        save(tmp_path, savings, day='2026-06-04')
    moved = save(tmp_path, savings, day=start)
    assert bank_opening(moved, savings).replaces == original.id
    revised = calculate_history(moved, scope, quotes, start, end)
    assert (revised.opening_value, revised.net_contributions, revised.gain) == (2100, 0, 30)
    assert revised.time_weighted == pytest.approx(30/2100)


def test_staggered_cash_accounts_keep_transfers_internal_and_require_entry_fx(tmp_path):
    from portfolio_app.performance.history_observations import Observation

    start, entry, end = '2026-01-01', '2026-02-01', '2026-02-02'
    save(tmp_path, bank('first'), opening=1000, day=start)
    journal = save(tmp_path, bank('second'), opening=500, day=entry)
    journal = ledger.append_events(journal, (
        ledger.Event('transfer', entry, 'transfer', 'first', 'EUR', 100, other_account_id='second'),
        ledger.Event('interest', end, 'interest', 'second', 'EUR', 15)))
    scope = HistoryScope(accounts=('first', 'second'))
    result = calculate_history(journal, scope, ObservationStore(), start, end)
    assert (result.opening_capital, result.net_contributions, result.ending_value) == (1000, 500, 1515)
    assert result.time_weighted == pytest.approx(.01)

    usd = replace(bank('third'), denomination='USD')
    foreign = save(tmp_path, usd, opening=100, day=entry)
    foreign_scope = HistoryScope(accounts=('first', 'third'))
    result = calculate_history(foreign, foreign_scope, ObservationStore(), start, end)
    assert result.opening_value == 1000 and result.net_contributions is None and result.time_weighted is None
    rates = ObservationStore(tuple(Observation('fx-'+d, 'fx', 'USD/EUR', d, rate, 'USD', target='EUR')
                                   for d, rate in [(entry, '.8'), (end, '.9')]))
    result = calculate_history(foreign, foreign_scope, rates, start, end)
    assert (result.net_contributions, result.gain) == (80, 10)


def test_linked_purchase_before_savings_opening_retains_its_funding():
    from portfolio_app.ledger.security_history import SecurityBook, SecurityEvent
    from portfolio_app.performance.history_observations import Observation

    start, buy, opening, end = '2026-01-05', '2026-01-06', '2026-01-07', '2026-01-08'
    events = (SecurityEvent('start', 'opening', start, source='confirmed_zero_start'),
              SecurityEvent('buy', 'buy', buy, quantity=1, price=100, currency='EUR', charges_known=True))
    stock = SecurityBook('stock', 'stock', ('1', '100', 'EUR'), account_id='savings',
                         records=events, active_ids=('start', 'buy'))
    journal = ledger.Journal(version=7, securities=(stock,), accounts=(bank('savings'),),
                             events=(ledger.Event('balance', opening, 'cash_open', 'savings', 'EUR', 200),))
    quotes = ObservationStore(tuple(Observation('quote-'+day, 'price', 'stock', day, price, 'EUR')
                                   for day, price in [(buy, 100), (opening, 100), (end, 110)]))
    scope = HistoryScope(('stock',), ('savings',))
    result = calculate_history(journal, scope, quotes, start, end)
    assert (result.net_contributions, result.ending_value, result.gain) == (300, 310, 10)
    assert result.time_weighted == pytest.approx(10/300)


@pytest.mark.parametrize('adopt', [False, True])
def test_cash_comparison_matches_account_history_and_excludes_pending_rows(tmp_path, adopt):
    from datetime import date
    from portfolio_app.performance.gain_comparison import known_category_performance

    prices, _, _ = invented(tmp_path)
    options = {'adopt': 'position-0'} if adopt else {}
    journal = save(tmp_path, bank('reserve'), opening=20000, day='2026-01-02',
                   create_category=True, **options)
    journal = ledger.append_events(journal, (
        ledger.Event('deposit', '2026-01-03', 'cash_flow', 'reserve', 'EUR', 2000),
        ledger.Event('interest', '2026-01-04', 'interest', 'reserve', 'EUR', 22),
    ))
    holdings = read_snapshot(tmp_path/'holdings.csv').holdings
    config = load_allocation(tmp_path/'allocation.yaml')
    category = next(b.id for b in config.buckets if b.name == 'Cash')
    def compare(journal, bucket):
        valued = journal_positions(prepare_portfolio(holdings, prices), journal, prices)
        return known_category_performance(valued, holdings, config, journal, ObservationStore(), valued,
                                          bucket, today=date(2026, 1, 6)).set_index('Category')
    for bucket, label in [('', 'Cash'), (category, 'Invented reserve')]:
        row = compare(journal, bucket).loc[label]
        assert row['Gain'] == 22 and row['Cost'] == 22000
        assert row['Return (%)'] == pytest.approx(0.1)

    pending = ledger.append_event(journal, ledger.Event('due', '2026-01-05', 'interest', 'reserve',
                                   'EUR', 10, value_date='2026-01-07'))
    assert compare(pending, '').loc['Cash', 'Gain'] == 32
    assert compare(pending, category)['Gain'].isna().all()


def test_legacy_sandbox_and_adopted_cash_survive_bank_creation(tmp_path):
    src = tmp_path/'base'
    src.mkdir()
    service, _, _ = invented(src)
    sandbox = fork_futures_workspace(src, tmp_path/'simulation')
    old = load_leverage_context(sandbox, 'EUR').future
    journal = save(sandbox, bank('existing'), opening=20000, day='2026-01-02', adopt='position-0')
    assert load_leverage_context(sandbox, 'EUR').future == old and old is not None
    assert not journal.has_broker_workspace
    assert load_leverage_context(sandbox, 'USD').unsupported_records
    projected = journal_positions(prepare_portfolio(read_snapshot(sandbox/'holdings.csv').holdings, service), journal, service)
    assert projected.position_id.tolist() == ['position-0']
    assert projected.current_value_reporting.tolist() == [20000]
    with pytest.raises(DataError, match='already belongs'):
        save(sandbox, bank('duplicate'), opening=20000, day='2026-01-02', adopt='position-0')


def test_new_category_and_opening_roll_back_together(tmp_path, monkeypatch):
    original_save = ledger.save_journal
    def interrupted(*args):
        raise OSError('Interrupted')
    monkeypatch.setattr(ledger, 'save_journal', interrupted)
    with pytest.raises(OSError, match='Interrupted'):
        save(tmp_path, bank('savings'), opening=250, day='2026-01-02', create_category=True)
    assert not (tmp_path/'allocation.yaml').exists() and not (tmp_path/ledger.FILE).exists()
    monkeypatch.setattr(ledger, 'save_journal', original_save)
    journal = save(tmp_path, bank('savings'), opening=250, day='2026-01-02', create_category=True)
    assert len(journal.accounts) == 1


def test_duplicate_cash_categories_require_an_explicit_path(tmp_path):
    from portfolio_app.portfolio.allocation import Allocation, Bucket, save_allocation
    from test_accounts_ui import launch_actual
    from test_ui import activate, by_label

    invented(tmp_path)
    holdings = read_snapshot(tmp_path/'holdings.csv').holdings
    config = Allocation((Bucket('personal', 'Personal'), Bucket('joint', 'Joint'),
                         Bucket('first', 'Cash', 'personal'), Bucket('second', 'Cash', 'joint')))
    save_allocation(tmp_path/'allocation.yaml', config, holdings, None)
    app = launch_actual(tmp_path)
    activate(app, 'Positions')
    by_label(app.button, 'Add position').click().run()
    by_label(app.get('button_group'), 'Position type').set_value('Cash account').run()
    category = by_label(app.selectbox, 'Category')
    assert category.value == ''
    assert 'Personal › Cash' in category.options and 'Joint › Cash' in category.options
    by_label(app.text_input, 'Account name').set_value('Invented term deposit')
    by_label(app.selectbox, 'Account type').set_value('Term deposit').run()
    by_label(app.number_input, 'Opening balance (EUR)').set_value(200)
    by_label(app.selectbox, 'Category').set_value('second').run()
    by_label(app.button, 'Save cash account').click().run()
    assert not app.exception
    account = ledger.load_journal(tmp_path)[0].accounts[0]
    assert account.bucket_id == 'second' and account.cash_type == 'Term deposit'


def test_bank_only_workspace_reopens_and_roundtrips_through_backup(tmp_path):
    from portfolio_app.workspace.workspace import validate_workspace
    from portfolio_app.workspace.backup import create_backup, inspect_backup, commit_restore
    workspace = tmp_path/'invented'
    journal = save(workspace, bank('savings'), opening=0, day='2026-01-02')
    validate_workspace(workspace)
    draft = inspect_backup(create_backup(workspace))
    try:
        assert draft.summary['positions'] == 1
        restored = commit_restore(draft, tmp_path/'restored', workspace)
        assert ledger.load_journal(restored)[0] == journal
    finally:
        draft.close()


def test_adopted_bank_uses_same_category_as_existing_position_editor(tmp_path):
    from portfolio_app.portfolio.allocation import Allocation, Bucket, save_allocation
    from portfolio_app.portfolio.category_commands import move_category_positions
    service, _, _ = invented(tmp_path)
    path = tmp_path/'holdings.csv'
    config_path = tmp_path/'allocation.yaml'
    config = Allocation((Bucket('reserve', 'Reserve'), Bucket('cash', 'Cash')))
    save_allocation(config_path, config, read_snapshot(path).holdings, None)
    account = replace(bank('adopted'), bucket_id='cash')
    journal = save(tmp_path, account, opening=20000, day='2026-01-02', adopt='position-0')
    move_category_positions(tmp_path, config, ['position-0'], 'reserve',
                            holdings_revision=revision(path), allocation_revision=revision(config_path))
    holdings = read_snapshot(path).holdings
    projected = journal_positions(prepare_portfolio(holdings, service), journal, service)
    assert projected.bucket_id.tolist() == ['reserve']
    assert category_history_scope(journal, holdings, config, 'reserve').accounts == (account.id,)
    journal = save(tmp_path, replace(account, bucket_id=''))
    assert read_snapshot(path).holdings.bucket_id.tolist() == ['']
    assert ledger.replay(journal).cash[account.id, 'EUR'] == 20000


def test_account_journal_filter_includes_incoming_bank_transfer(tmp_path):
    from test_account_activity_ui import launch_activity
    from test_ui import by_label
    from list_helpers import list_data

    save(tmp_path, bank('savings'), opening=100, day='2026-01-02')
    journal = save(tmp_path, bank('checking', 'Checking'), opening=0, day='2026-01-02')
    transfer = ledger.Event('transfer', '2026-01-03', 'transfer', 'savings', 'EUR', 25, other_account_id='checking')
    ledger.save_journal(tmp_path, ledger.append_event(journal, transfer), revision(tmp_path/ledger.FILE))
    app = launch_activity(tmp_path)
    by_label(app.multiselect, 'Accounts').set_value(['checking']).run()
    rows = list_data(app, 'Recorded transactions')['rows']
    assert [r['Action'] for r in rows] == ['Transfer', 'Opening cash']
    assert rows[0]['Account'] == 'Invented savings → Invented checking'


def test_bank_opening_corrections_preserve_history_and_can_restore_missing_opening(tmp_path):
    from portfolio_app.ledger.account_activity import correct_entry
    from portfolio_app.ledger.bank_accounts import bank_opening
    prices, _, _ = invented(tmp_path)
    account = bank('savings')
    journal = save(tmp_path, account, opening=100, day='2026-01-02')
    original = bank_opening(journal, account)
    for changes, reverse in [({}, True), ({'account_id': 'other'}, False), ({'currency': 'USD'}, False)]:
        with pytest.raises(DataError, match='Edit cash account'):
            correct_entry(journal, original.id, changes, reverse=reverse)
    updated = save(tmp_path, account, opening=125)
    corrected = bank_opening(updated, account)
    assert corrected.amount == 125 and corrected.replaces == original.id and original in updated.events
    # A workspace written before the opening was protected can be repaired in place.
    legacy = ledger.append_event(updated, ledger.Event('old-void', '2026-01-03', 'void', replaces=corrected.id))
    ledger.save_journal(tmp_path, legacy, revision(tmp_path/ledger.FILE))
    projected = journal_positions(prepare_portfolio(read_snapshot(tmp_path/'holdings.csv').holdings, prices), legacy, prices)
    missing = projected.set_index('position_id').loc['account-cash:savings:EUR', 'current_value_reporting']
    from math import isnan
    assert isnan(missing)
    restored = save(tmp_path, account, opening=110, day='2026-01-01')
    assert bank_opening(restored, account).amount == 110
    assert ledger.replay(restored).cash[account.id, 'EUR'] == 110


def test_bank_capital_includes_adopted_cash_and_claims_once_and_excludes_broker_collateral(tmp_path):
    from portfolio_app.portfolio.actual_portfolio import capital_plan_positions
    prices, broker, _ = invented(tmp_path)
    ledger.save_journal(tmp_path, replace(broker, cash_links=()), None)
    journal = save(tmp_path, bank('savings'), opening=20000, day='2026-01-02', adopt='position-0', create_category=True)
    journal = ledger.append_events(journal, (
        ledger.Event('interest', '2026-01-03', 'interest', 'savings', 'EUR', 10),
        ledger.Event('pending', '2026-01-04', 'interest', 'savings', 'EUR', 5, value_date='2026-01-05')))
    holdings = read_snapshot(tmp_path/'holdings.csv').holdings
    planned = capital_plan_positions(journal_positions(prepare_portfolio(holdings, prices), journal, prices))
    assert set(planned.position_id) == {'position-0', 'account-pending:pending'}
    assert planned.fixed_cash.all() and planned.current_value_reporting.sum() == 20015
    assert planned.bucket_id.tolist() == [holdings.bucket_id.iloc[0]] * 2
    with pytest.raises(DataError, match='opening snapshot'):
        save(tmp_path, bank('savings'), opening=19999)


@pytest.mark.parametrize('category_mode', ['targets', 'flexible'])
def test_portfolio_plan_includes_savings_but_only_spends_supplied_contribution(category_mode):
    import pandas as pd
    from portfolio_app.portfolio.allocation import Allocation, Bucket
    from portfolio_app.portfolio.rebalance_tables import allocation_table
    from portfolio_app.portfolio.scoped_rebalancing import portfolio_contribution
    config = Allocation((Bucket('equity', 'Equity', target=.75, allocation_mode=category_mode),
                         Bucket('cash', 'Cash', target=.25)))
    positions = pd.DataFrame([
        dict(position_id='stock', name='Invented equity', bucket_id='equity', shares=10, fixed_cash=False,
             current_value_reporting=7500., target_allocation=.75, within_bucket_target=1.),
        dict(position_id='bank', name='Invented savings', bucket_id='cash', shares=1, fixed_cash=True,
             current_value_reporting=2500., target_allocation=float('nan'), within_bucket_target=float('nan'))])
    plan = portfolio_contribution(positions, config, 500, eligible_ids=['stock', 'bank'])
    table = allocation_table(positions, plan.trades, new_money=500).set_index('Investment')
    assert table.loc['Invented savings', ['Current %', 'Trade', 'After']].tolist() == [25, 0, 2500]
    assert table.loc['Invented equity', 'Trade'] == 500 and plan.unallocated_cash == 0
    assert table.After.sum() == 10500
