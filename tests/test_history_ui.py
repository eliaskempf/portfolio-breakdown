"""UI tests use only a generated synthetic workspace."""
from decimal import Decimal as D
from dataclasses import replace
from streamlit.testing.v1 import AppTest

from portfolio_app.account_journal import save_journal, Journal
from portfolio_app.positions import read_snapshot
from portfolio_app.security_history import SecurityBook, SecurityEvent as E
from portfolio_app.history_observations import Observation, ObservationStore, HistoryScope, CoverageReview,save_observations
from portfolio_app.security_history import history_fingerprint


def fixture(tmp_path):
    (tmp_path/'holdings.csv').write_text('id,name,shares,acquisition_price,acquisition_currency\ninvented,Invented,10,10,EUR\n')
    events=(E('opening','opening','2024-01-01',quantity=D(10),amount=D(100),currency='EUR'),)
    b=SecurityBook('position-0','invented',('10','10','EUR'),records=events,active_ids=('opening',))
    journal=Journal(version=2,securities=(b,))
    save_journal(tmp_path,journal,None)
    scope=HistoryScope(('position-0',))
    store=ObservationStore((Observation('a','price','position-0','2024-01-01',D(10),'EUR'),
                            Observation('b','price','position-0','2025-01-01',D(12),'EUR')),
        (CoverageReview('r',scope,'2024-01-01','2025-01-01',history_fingerprint(journal)),))
    save_observations(tmp_path,store,None)


def launch(tmp_path):
    return AppTest.from_string(f'''from pathlib import Path
from portfolio_app.history_ui import render_position_history
from portfolio_app.positions import read_snapshot
path=Path({str(tmp_path)!r})
render_position_history(path,read_snapshot(path/'holdings.csv').holdings.iloc[0])
''',default_timeout=15).run()


def label(elements,value):
    return next(e for e in elements if e.label==value)


def transaction_rows(directory):
    from portfolio_app.account_journal import load_journal
    from portfolio_app.history_ui import _transaction_table, _visible_records
    journal, _ = load_journal(directory)
    if not journal.securities:
        return _transaction_table([], None)
    book = journal.securities[0]
    return _transaction_table(_visible_records(book), book)


def open_transaction_editor(app, directory):
    from portfolio_app.account_journal import load_journal
    from portfolio_app.history_ui import _key, _visible_records
    book = load_journal(directory)[0].securities[0]
    records = _visible_records(book)
    event = next((e for e in records if e.kind != 'opening'), records[0])
    key = _key(directory, book.position_id)
    app.session_state[key+'edit_record'] = event.id
    app.session_state[key+'screen'] = 'edit'
    app.run()


def test_history_views_and_incomplete_new_transaction_render(tmp_path):
    fixture(tmp_path)
    app=launch(tmp_path)
    assert not app.exception
    label(app.button,'Add buy').click().run()
    assert not app.exception
    label(app.text_input,'Quantity').set_value('1').run()
    label(app.button,'Save transaction').click().run()
    assert not app.exception
    from portfolio_app.account_journal import load_journal
    book=load_journal(tmp_path)[0].securities[0]
    assert len(book.records)==2 and book.active_ids==('opening',)
    label(app.button,'Review against portfolio').click().run()
    assert not app.exception
    assert any(e.label=='Apply history' for e in app.button)


def test_snapshot_only_history_has_no_empty_chart(tmp_path):
    (tmp_path/'holdings.csv').write_text('id,name,shares\ninvented,Invented,10\n')
    app=launch(tmp_path)
    assert not app.exception
    assert not app.get('plotly_chart')
    label(app.button,'Add starting balance').click().run()
    assert not app.exception
    assert any(e.label=='Save starting balance' for e in app.button)
    assert not app.get('data_editor')
    assert not app.toggle


def test_save_rejects_concurrent_history_change_and_retains_draft(tmp_path):
    from portfolio_app.account_journal import load_journal
    fixture(tmp_path)
    app = launch(tmp_path)
    label(app.button, 'Add buy').click().run()
    label(app.text_input, 'Quantity').set_value('3').run()
    label(app.text_input, 'Unit price').set_value('12').run()
    journal, revision = load_journal(tmp_path)
    other = E('concurrent-buy', 'buy', '2024-02-01', quantity=D(1), price=D(11), currency='EUR')
    changed = replace(journal, securities=(replace(journal.securities[0], records=(*journal.securities[0].records, other)),))
    save_journal(tmp_path, changed, revision)
    label(app.button, 'Save transaction').click().run()
    assert not app.exception
    assert any('History or holdings changed' in error.value for error in app.error)
    assert label(app.text_input, 'Quantity').value == '3'
    assert label(app.text_input, 'Unit price').value == '12'
    assert load_journal(tmp_path)[0] == changed


def test_opening_form_saves_evidence_without_changing_balances(tmp_path):
    (tmp_path/'holdings.csv').write_text('id,name,shares,acquisition_price,acquisition_currency\ninvented,Invented,10,10,EUR\n')
    app=launch(tmp_path)
    label(app.button,'Add starting balance').click().run()
    label(app.button,'Save starting balance').click().run()
    assert not app.exception
    from portfolio_app.account_journal import load_journal
    book=load_journal(tmp_path)[0].securities[0]
    assert not book.active and book.records[0].quantity == 10
    label(app.button,'Review against portfolio').click().run()
    app.multiselect[0].set_value([book.records[0].id]).run()
    label(app.button,'Apply history').click().run()
    assert not app.exception
    assert load_journal(tmp_path)[0].securities[0].active
    assert read_snapshot(tmp_path/'holdings.csv').holdings.iloc[0].shares == 10


def test_buys_and_sells_save_before_starting_balance_and_review_from_zero(tmp_path):
    from datetime import date
    from portfolio_app.account_journal import load_journal
    from portfolio_app.security_history import effective_holdings
    (tmp_path/'holdings.csv').write_text('id,name,shares,acquisition_price,acquisition_currency\ninvented,Invented,10,15.5,EUR\n')
    app=launch(tmp_path)
    assert transaction_rows(tmp_path).empty
    assert not app.checkbox and not app.date_input
    for kind,quantity,price,fee,stamp in [('buy','10','10','10',date(2024,1,2)),
                                        ('buy','5','20','',date(2024,2,1)),
                                        ('sell','5','22','',date(2024,3,1))]:
        label(app.button,'Add '+kind).click().run()
        assert label(app.selectbox,'Transaction').value == kind
        assert label(app.text_input,'Quantity').value == ''
        label(app.date_input,'Transaction date').set_value(stamp).run()
        label(app.text_input,'Quantity').set_value(quantity).run()
        label(app.text_input,'Unit price').set_value(price).run()
        label(app.text_input,'Fees').set_value(fee or '0').run()
        label(app.button,'Save transaction').click().run()
        assert not app.exception
    journal,revision=load_journal(tmp_path)
    assert [e.kind for e in journal.securities[0].records] == ['buy','buy','sell']
    assert not journal.securities[0].active
    label(app.button,'Review against portfolio').click().run()
    assert any(e.label=='Apply history' for e in app.button)
    assert load_journal(tmp_path)[1] == revision  # Merely reviewing writes nothing.
    assert not app.exception
    comparison=app.table[0].value
    assert comparison['Current portfolio'].tolist() == ['10','155.00','15.50']
    assert comparison['From history'].tolist() == ['10','155.00','15.50']
    assert load_journal(tmp_path)[1] == revision
    label(app.button,'Apply history').click().run()
    assert not app.exception
    journal,_=load_journal(tmp_path)
    current=effective_holdings(read_snapshot(tmp_path/'holdings.csv').holdings,journal.securities).iloc[0]
    assert current.shares == 10 and current.acquisition_price == 15.5
    assert len(journal.securities[0].records) == 4  # Three trades plus the reviewed default zero boundary.
    label(app.button,'Review against portfolio').click().run()
    assert not any('zero shares beforehand' in e.label for e in app.checkbox)
    assert any(e.label=='Apply history' for e in app.button)


def test_review_can_leave_portfolio_unchanged(tmp_path):
    fixture(tmp_path)
    app=launch(tmp_path)
    label(app.button,'Review against portfolio').click().run()
    label(app.button,'Keep portfolio unchanged').click().run()
    assert not app.exception
    assert transaction_rows(tmp_path).Status.tolist()==['Applied']


def test_fee_confirmation_repairs_old_draft_without_changing_holding(tmp_path):
    from portfolio_app.account_journal import load_journal
    from portfolio_app.security_history import Charge, effective_holdings
    (tmp_path/'holdings.csv').write_text('id,name,shares,acquisition_price,acquisition_currency\ninvented,Invented,10,10.5,EUR\n')
    event=E('old-buy','buy','2024-01-02',quantity=D(10),price=D(10),currency='EUR',charges=(Charge(D(5),'EUR'),))
    book=SecurityBook('position-0','invented',('10','10.5','EUR'),records=(event,))
    save_journal(tmp_path,Journal(version=2,securities=(book,)),None)
    app=launch(tmp_path)
    label(app.button,'Review against portfolio').click().run()
    assert not app.exception
    assert app.table[0].value['From history'].tolist()==['10','Unknown','Unknown']
    assert not label(app.button,'Apply history').disabled
    assert any(e.value == 'Cost unavailable' for e in app.caption)
    assert label(app.text_input,'Fees (EUR)').value=='5'
    label(app.button,'Save fees').click().run()
    assert not app.exception
    saved,_=load_journal(tmp_path)
    assert not saved.securities[0].active
    assert saved.securities[0].records[0]==event
    assert saved.securities[0].records[-1].replaces==event.id
    assert app.table[0].value['From history'].tolist()==['10','105.00','10.50']
    label(app.button,'Apply history').click().run()
    saved,_=load_journal(tmp_path)
    assert effective_holdings(read_snapshot(tmp_path/'holdings.csv').holdings,saved.securities).iloc[0].acquisition_price==10.5


def test_review_cash_names_and_one_explicit_apply_action(tmp_path):
    from portfolio_app.account_journal import Account, Event, load_journal, replay
    (tmp_path/'holdings.csv').write_text('id,name,shares,acquisition_price,acquisition_currency\ninvented,Invented,10,10,EUR\n')
    account='synthetic-internal-account-id'
    trade=E('buy','buy','2024-01-02',quantity=D(5),price=D(12),currency='EUR',charges_known=True)
    book=SecurityBook('position-0','invented',('10','10','EUR'),account_id=account,records=(trade,))
    journal=Journal(version=2,accounts=(Account(account,'Invented brokerage'),Account('other','Unrelated account')),
        events=(Event('cash','2024-01-01','cash_open',account,'EUR',D(1000)),
                Event('other-cash','2024-01-01','cash_open','other','EUR',D(200))),securities=(book,))
    save_journal(tmp_path,journal,None)
    app=launch(tmp_path)
    label(app.button,'Review against portfolio').click().run()
    assert not app.exception and not app.checkbox
    comparison=app.table[0].value
    assert comparison['From history'].tolist()==['5','60.00','12.00']
    cash=app.table[1].value
    assert cash.Account.tolist()==['Invented brokerage']
    assert cash['After applying'].tolist()==['940.00']
    label(app.button,'Apply history').click().run()
    assert not app.exception
    saved,_=load_journal(tmp_path)
    assert replay(saved).cash[account,'EUR']==940
    assert not saved.securities[0].reviews[-1].complete_from  # Applying needs no completeness declaration.
    assert read_snapshot(tmp_path/'holdings.csv').holdings.iloc[0].shares==10


def test_unknown_fees_stay_unknown_until_explicit_zero(tmp_path):
    from portfolio_app.account_journal import load_journal
    (tmp_path/'holdings.csv').write_text('id,name,shares,acquisition_price,acquisition_currency\ninvented,Invented,1,10,EUR\n')
    app=launch(tmp_path)
    label(app.button,'Add buy').click().run()
    label(app.text_input,'Quantity').set_value('1').run()
    label(app.text_input,'Unit price').set_value('10').run()
    label(app.button,'Save transaction').click().run()
    assert not load_journal(tmp_path)[0].securities[0].records[0].charges_known
    label(app.button,'Review against portfolio').click().run()
    assert not label(app.button,'Apply history').disabled
    assert label(app.button,'Save fees').disabled
    label(app.text_input,'Fees (EUR)').set_value('0').run()
    label(app.button,'Save fees').click().run()
    assert not app.exception
    assert app.table[0].value['From history'].tolist()==['1','10.00','10.00']
    assert not label(app.button,'Apply history').disabled


def test_gross_and_cash_total_entry_do_not_double_count_fees(tmp_path):
    from portfolio_app.account_journal import load_journal
    for kind,mode,amount in [('buy','Trade value (excluding fees)','100'),
                             ('buy','Total paid (including fees)','105'),
                             ('sell','Trade value (excluding fees)','100'),
                             ('sell','Total received (after fees)','95')]:
        directory=tmp_path/(kind+str(amount));directory.mkdir()
        (directory/'holdings.csv').write_text('id,name,shares\ninvented,Invented,1\n')
        app=launch(directory)
        label(app.button,'Add '+kind).click().run()
        label(app.text_input,'Quantity').set_value('10').run()
        label(app.selectbox,'Amount entry').select(mode).run()
        label(app.text_input,mode).set_value(amount).run()
        label(app.text_input,'Fees').set_value('5').run()
        label(app.button,'Save transaction').click().run()
        assert not app.exception
        event=load_journal(directory)[0].securities[0].records[0]
        assert event.gross==100 and event.charges[0].amount==5 and event.charges_known


def test_review_can_supply_missing_historical_fx_before_activation(tmp_path):
    from portfolio_app.account_journal import load_journal
    (tmp_path/'holdings.csv').write_text('id,name,shares,acquisition_price,acquisition_currency\ninvented,Invented,10,9,EUR\n')
    trade=E('buy','buy','2024-01-02',quantity=D(10),price=D(10),currency='USD',charges_known=True)
    book=SecurityBook('position-0','invented',('10','9','EUR'),records=(trade,))
    save_journal(tmp_path,Journal(version=2,securities=(book,)),None)
    app=launch(tmp_path)
    label(app.button,'Review against portfolio').click().run()
    assert not label(app.button,'Apply history').disabled
    assert app.table[0].value['From history'].tolist()==['10','Unknown','Unknown']
    label(app.text_input,'2024-01-02 · EUR per 1 USD').set_value('0.9').run()
    label(app.button,'Save exchange rate').click().run()
    assert not app.exception
    assert app.table[0].value['From history'].tolist()==['10','90.00','9.00']
    assert not load_journal(tmp_path)[0].securities[0].active
    assert not label(app.button,'Apply history').disabled


def test_internal_boundary_is_not_a_selectable_transaction(tmp_path):
    from portfolio_app.history_commands import reconstruction_candidate
    from portfolio_app.account_journal import load_journal
    (tmp_path/'holdings.csv').write_text('id,name,shares,acquisition_price,acquisition_currency\ninvented,Invented,2,10,EUR\n')
    trades=(E('a','buy','2024-01-02',quantity=D(1),price=D(10),currency='EUR',charges_known=True),
            E('b','buy','2024-02-02',quantity=D(1),price=D(10),currency='EUR',charges_known=True))
    book=SecurityBook('position-0','invented',('2','10','EUR'),records=trades)
    book=reconstruction_candidate(book,('a','b'),starts_at_zero=True)
    save_journal(tmp_path,Journal(version=2,securities=(book,)),None)
    app=launch(tmp_path)
    assert len(transaction_rows(tmp_path))==2
    label(app.button,'Review against portfolio').click().run()
    assert label(app.multiselect,'Included transactions').value==['a','b']
    assert len(label(app.multiselect,'Included transactions').options)==2
    label(app.button,'Apply history').click().run()
    assert not app.exception
    assert len(load_journal(tmp_path)[0].securities[0].records)==3


def test_direct_editor_preserves_precision_and_retains_original(tmp_path):
    from portfolio_app.account_journal import load_journal
    from portfolio_app.security_history import Charge
    (tmp_path/'holdings.csv').write_text('id,name,shares,acquisition_price,acquisition_currency\ninvented,Invented,10,10,EUR\n')
    trade=E('a','buy','2024-01-02',quantity=D(10),amount=D('1326.999906'),currency='EUR',charges=(Charge(D(1),'EUR'),),charges_known=True)
    book=SecurityBook('position-0','invented',('10','10','EUR'),records=(trade,))
    save_journal(tmp_path,Journal(version=2,securities=(book,)),None)
    app=launch(tmp_path)
    assert transaction_rows(tmp_path)['Trade value'].tolist()==['1327.00 EUR']
    assert transaction_rows(tmp_path).Fees.tolist()==['1.00 EUR']
    open_transaction_editor(app, tmp_path)
    assert not app.exception
    assert label(app.text_input,'Trade value (excluding fees)').value=='1326.999906'
    before=load_journal(tmp_path)[1]
    label(app.button,'Save changes').click().run()
    assert load_journal(tmp_path)[1]==before
    open_transaction_editor(app, tmp_path)
    label(app.text_input,'Trade value (excluding fees)').set_value('1426.999906').run()
    label(app.button,'Save changes').click().run()
    assert not app.exception
    saved=load_journal(tmp_path)[0].securities[0]
    assert saved.records[0]==trade and saved.records[-1].amount==D('1426.999906')
    assert not saved.active
    assert len(transaction_rows(tmp_path))==1
    assert transaction_rows(tmp_path)['Trade value'].tolist()==['1427.00 EUR']
    open_transaction_editor(app, tmp_path)
    assert label(app.text_input,'Trade value (excluding fees)').value=='1426.999906'
    label(app.text_input,'Trade value (excluding fees)').set_value('42').run()
    label(app.button,'Cancel').click().run()
    assert len(load_journal(tmp_path)[0].securities[0].records)==2


def test_transfer_entry_uses_holding_names_and_preserves_destination_identity(tmp_path):
    fixture(tmp_path)
    with (tmp_path/'holdings.csv').open('a') as handle:
        handle.write('invented,Invented,0,0,EUR\n')
    app = launch(tmp_path)
    label(app.button, 'Other transaction').click().run()
    label(app.selectbox, 'Transaction').set_value('transfer').run()
    destination = label(app.selectbox, 'Receiving holding')
    assert len(destination.options) == 1 and 'Invented' in destination.options[0]
    assert not any('position ID' in field.label for field in app.text_input)
    destination.set_value('position-1').run()
    label(app.text_input, 'Quantity').set_value('1').run()
    label(app.text_input, 'Fee').set_value('0').run()
    label(app.button, 'Save transaction').click().run()
    assert not app.exception
    from portfolio_app.account_journal import load_journal
    event = load_journal(tmp_path)[0].securities[0].records[-1]
    assert event.kind == 'transfer' and event.other_position_id == 'position-1'


def test_prepared_history_reuses_results_and_invalidates_financial_inputs(tmp_path, monkeypatch):
    from dataclasses import replace
    from test_history_view import fixture as chart_fixture
    import portfolio_app.history_view as charts
    import portfolio_app.input_cache as cache
    from portfolio_app.history_ui import prepared_history_view
    monkeypatch.setattr(cache.st, 'session_state', {})
    journal, scope, store = chart_fixture()
    calls = []
    calculate = charts.history_view
    def counted(*args, **kwargs):
        calls.append(True)
        return calculate(*args, **kwargs)
    monkeypatch.setattr(charts, 'history_view', counted)
    def view(journal=journal, scope=scope, store=store, revision='first', **kw):
        return prepared_history_view(tmp_path, journal, scope, store, revision, 'All', **kw)
    first = view()
    assert view() == first and len(calls) == 1
    first.points[-1]['Value'] = -1
    assert view().points[-1]['Value'] != -1  # Caller cannot mutate the cached chart.
    corrected = replace(store, observations=(*store.observations[:-1], replace(store.observations[-1], amount=D(20))))
    assert view(store=corrected, revision='corrected').result.ending_value == 200
    unreviewed = replace(corrected, reviews=())
    assert view(store=unreviewed, revision='unreviewed').result.money_weighted is not None
    book = replace(journal.securities[0], active_ids=())
    changed = replace(journal, securities=(book,))
    assert view(journal=changed).result.ending_value != view().result.ending_value
    assert view(target='USD').result.ending_value is None
    assert view(scope=HistoryScope(('missing',))).result.ending_value is None


def test_summary_cache_does_not_replace_chart_cache(tmp_path, monkeypatch):
    from portfolio_app import input_cache
    from portfolio_app.history_ui import prepared_history_view
    from test_history_view import fixture
    monkeypatch.setattr(input_cache.st, 'session_state', {})
    journal, scope, store = fixture()
    chart = prepared_history_view(tmp_path, journal, scope, store, 'synthetic')
    summary = prepared_history_view(tmp_path, journal, scope, store, 'synthetic', include_points=False)
    assert summary == replace(chart, points=())
    assert prepared_history_view(tmp_path, journal, scope, store, 'synthetic') == chart


def test_direct_editor_keeps_concurrent_revision_guard(tmp_path):
    from portfolio_app.account_journal import load_journal
    fixture(tmp_path)
    app = launch(tmp_path)
    open_transaction_editor(app, tmp_path)
    label(app.text_input, 'Quantity').set_value('11').run()
    journal, revision = load_journal(tmp_path)
    other = E('other', 'buy', '2024-02-01', quantity=1, price=10, currency='EUR')
    changed = replace(journal, securities=(replace(journal.securities[0], records=(*journal.securities[0].records, other)),))
    save_journal(tmp_path, changed, revision)
    label(app.button, 'Save changes').click().run()
    assert not app.exception
    assert any('History or holdings changed' in error.value for error in app.error)
    assert label(app.text_input, 'Quantity').value == '11'
    assert load_journal(tmp_path)[0] == changed


def test_automatic_history_consumes_prepared_summary_and_rebuilds_on_new_evidence(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import portfolio_app.history_ui as ui
    import portfolio_app.gain_accounting as gains
    import portfolio_app.personal_history_refresh as refresh
    fixture(tmp_path)
    calls, rendered = [], []
    original = gains.summarize_known_gain
    def counted(*args, **kwargs):
        if kwargs.get('current_values') is not None:
            calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(gains, 'summarize_known_gain', counted)
    monkeypatch.setattr(refresh.refresher, 'request', lambda *args: SimpleNamespace(pending=False))
    monkeypatch.setattr(ui, '_render_history_results', lambda *args, **kwargs: rendered.append(kwargs['prepared']))
    monkeypatch.setattr(ui, '_history_inputs', lambda *args: None)
    app = AppTest.from_string(f'''
from pathlib import Path
from dataclasses import replace
import pandas as pd
import streamlit as st
from portfolio_app.allocation import Allocation, Bucket
from portfolio_app.positions import read_snapshot
from portfolio_app.history_ui import prepare_portfolio_history, render_portfolio_history
from portfolio_app.history_observations import save_observations
root = Path({str(tmp_path)!r})
holdings = read_snapshot(root / 'holdings.csv').holdings.assign(bucket_id='core')
current = holdings.assign(current_value_reporting=120, unrealized_gain_reporting=20)
prepared = prepare_portfolio_history(root, holdings, config=Allocation((Bucket('core', 'Core'),)),
    displayed=current, current_valued=current)
render_portfolio_history(root, prepared, automatic=True)
# Simulate an actual revision arriving from the background worker between
# page preparation and fragment rendering; old facts must not survive it.
store = prepared['observations']
changed = replace(store, observations=(*store.observations[:-1], replace(store.observations[-1], amount=15)))
save_observations(root, changed, prepared['revision'])
render_portfolio_history(root, prepared, automatic=True)
''').run()
    assert not app.exception
    assert len(calls) == 2  # One initial summary; one after the observation revision.
    assert len(rendered) == 2
    assert rendered[0]['view'].all_known.value == 120
    assert rendered[1]['view'].all_known.value == 150
    assert rendered[0]['calculation'] is not rendered[1]['calculation']


def test_interval_switches_reuse_ledgers_and_previous_points(tmp_path, monkeypatch):
    from datetime import date
    from test_history_view import fixture as chart_fixture
    import portfolio_app.history_ui as ui
    import portfolio_app.history_view as charts
    import portfolio_app.personal_performance as performance
    import portfolio_app.input_cache as cache
    monkeypatch.setattr(cache.st, 'session_state', {})
    class FixedDate(date):
        @classmethod
        def today(cls): return cls(2024, 6, 16)
    monkeypatch.setattr(ui, 'date', FixedDate)
    journal, scope, store = chart_fixture()
    calls, replays = [], []
    calculate, replay = charts.calculate_history, performance.replay_securities
    def counted(*args, **kwargs):
        calls.append((args[3], args[4], kwargs.get('annualized', True)))
        return calculate(*args, **kwargs)
    def replayed(*args, **kwargs):
        replays.append(kwargs.get('as_of'))
        return replay(*args, **kwargs)
    monkeypatch.setattr(charts, 'calculate_history', counted)
    monkeypatch.setattr(performance, 'replay_securities', replayed)
    def view(period): return ui.prepared_history_view(tmp_path, journal, scope, store, 'synthetic', period)
    all_view = view('All')
    before_replays, before_calls = len(replays), len(calls)
    month = view('1M')
    assert len(replays) == before_replays  # Range changes never reconstruct the same ledger state.
    assert len(calls) == before_calls + 2  # New range result and new requested-start hover point.
    assert view('All') == all_view
    assert len(calls) == before_calls + 2
    assert month == charts.history_view(journal, scope, store, '1M', today=FixedDate.today())


def test_reused_context_expires_for_workspace_currency_date_and_evidence(tmp_path, monkeypatch):
    from datetime import date
    from test_history_view import fixture as chart_fixture
    import portfolio_app.history_ui as ui
    import portfolio_app.input_cache as cache
    monkeypatch.setattr(cache.st, 'session_state', {})
    journal, scope, store = chart_fixture()
    def context(directory=tmp_path, journal=journal, revision='synthetic', target='EUR'):
        return ui.prepared_history_calculation(directory, journal, store, revision, target=target)
    first = context()
    assert context() is first
    assert context(directory=tmp_path/'other-workspace') is not first
    first = context()
    assert context(target='USD') is not first
    first = context()
    assert context(revision='changed-observations') is not first
    first = context()
    changed = replace(journal, securities=(replace(journal.securities[0], active_ids=()),))
    assert context(journal=changed) is not first
    first = context()
    class Tomorrow(date):
        @classmethod
        def today(cls): return date.today().fromordinal(date.today().toordinal()+1)
    monkeypatch.setattr(ui, 'date', Tomorrow)
    assert context() is not first
