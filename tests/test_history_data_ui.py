"""Focused history corrections use only invented holdings and static evidence."""
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from portfolio_app.ledger.account_journal import Journal
from portfolio_app.interface.views.history.history_data_ui import (
    missing_inputs, readable_reasons, save_manual_observation, scope_names, scoped_observations,
)
from portfolio_app.performance.history_observations import (
    HistoryScope, Observation, ObservationStore, active_observations, load_observations, save_observations,
)
from portfolio_app.portfolio.holdings import DataError
from portfolio_app.ledger.security_history import SecurityBook, SecurityEvent


def fixture(tmp_path):
    (tmp_path/'holdings.csv').write_text('id,name,shares,acquisition_price,acquisition_currency\n'
                                       'invented,Invented Orchard,1,10,USD\n'
                                       'unrelated,Unrelated Holding,1,10,GBP\n')
    book = SecurityBook('position-0', 'invented', ('1', '10', 'USD'), records=(
        SecurityEvent('opening','opening','2024-01-01',quantity=D(1),amount=D(10),currency='USD'),
    ), active_ids=('opening',))
    other = replace(book, position_id='position-1', instrument_id='unrelated',
                    records=(replace(book.records[0], id='opening-other'),), active_ids=('opening-other',))
    journal = Journal(version=2, securities=(book, other))
    scope = HistoryScope(('position-0',))
    store = ObservationStore((
        Observation('p1','price','position-0','2024-01-01',D(10),'USD'),
        Observation('p2','price','position-1','2024-01-01',D(20),'GBP'),
        Observation('fx1','fx','USD/EUR','2024-01-01',D('.9'),'USD','EUR'),
        Observation('fx2','fx','GBP/JPY','2024-01-01',D(190),'GBP','JPY'),
        Observation('v1','value',scope.key,'2024-01-01',D(9),'EUR'),
        Observation('v2','value',HistoryScope(('position-1',)).key,'2024-01-01',D(20),'GBP'),
    ))
    save_observations(tmp_path, store, None)
    return journal, scope, store, load_observations(tmp_path)[1]


def view(*reasons):
    result = SimpleNamespace(value_reasons=tuple(reasons), flow_reasons=(), capital_reasons=(),
                             money_weighted_reasons=(), time_weighted_reasons=())
    return SimpleNamespace(result=result, all_result=result, missing=(), all_start='2024-01-01',
                           all_end='2024-01-03', today='2024-01-03')


def test_missing_inputs_are_explicit_dated_actions_not_coverage_or_general_errors(tmp_path):
    journal, scope, store, _ = fixture(tmp_path)
    result = missing_inputs(view(
        'position-0: missing price on 2024-01-02',
        'position-0: missing value or USD/EUR FX on 2024-01-02',
        'position-0: missing value or USD/EUR FX on 2024-01-01',
        'position-1: missing price on 2024-01-02',
        'Review complete scope membership, transactions, income and charges for this interval',
        'position-0: no opening quantity on 2024-01-02'), journal, scope, store, 'EUR')
    assert len(result) == 2
    assert {(i.kind, i.date) for i in result} == {('price','2024-01-02'), ('fx','2024-01-02')}
    assert all(i.currency == 'USD' for i in result)


def test_saved_evidence_and_names_exclude_unrelated_holdings_and_currency_pairs(tmp_path):
    journal, scope, store, _ = fixture(tmp_path)
    assert {o.id for o in scoped_observations(journal,scope,store,'EUR')} == {'p1','fx1','v1'}
    names = scope_names(tmp_path,journal,scope)
    assert names['position-0'] == names['invented'] == 'Invented Orchard'
    assert 'position-1' not in names and 'unrelated' not in names
    messages = readable_reasons(tmp_path,journal,scope,(
        'position-0: missing price on 2024-01-02',
        scope.key+': missing value',
        'security:some-internal-sha: supply exact before/after flow valuations'))
    assert messages[0] == 'Invented Orchard: missing price on 2024-01-02'
    assert 'some-internal-sha' not in ' '.join(messages)
    assert scope.key not in ' '.join(messages)


def test_replacement_is_explicit_original_retained_and_stale_save_rejected(tmp_path):
    _, _, store, revision = fixture(tmp_path)
    correction = Observation('correction','price','position-0','2024-01-01',D('10.25'),'USD')
    with pytest.raises(DataError, match='Replace saved value'):
        save_manual_observation(tmp_path,store,revision,correction)
    assert load_observations(tmp_path)[0] == store
    changed = save_manual_observation(tmp_path,store,revision,correction,replace_existing=True)
    assert changed.observations[0] == store.observations[0]
    assert changed.observations[-1].replaces == 'p1'
    assert next(o for o in active_observations(changed) if o.id == 'correction').amount == D('10.25')
    with pytest.raises(DataError):
        save_manual_observation(tmp_path,store,revision,replace(correction,id='stale'),replace_existing=True)
    assert load_observations(tmp_path)[0] == changed


def test_public_editor_is_lazy_and_has_no_coverage_checkbox(tmp_path):
    app = AppTest.from_string(f'''
from pathlib import Path
from types import SimpleNamespace
from portfolio_app.ledger.account_journal import Journal
from portfolio_app.performance.history_observations import ObservationStore, HistoryScope
from portfolio_app.interface.views.history.history_data_ui import render_history_data
render_history_data(Path({str(tmp_path)!r}),Journal(),HistoryScope(),ObservationStore(),None,
                    SimpleNamespace(), 'EUR','test_')
''').run()
    assert not app.exception
    assert [e.label for e in app.expander] == ['Correct market data']
    assert not app.checkbox and not app.selectbox and not app.text_input


def test_price_form_has_readable_scope_and_explicit_replace_button(tmp_path):
    fixture(tmp_path)
    app = AppTest.from_string(f'''
from pathlib import Path
from portfolio_app.ledger.account_journal import Journal
from portfolio_app.ledger.security_history import SecurityBook
from portfolio_app.performance.history_observations import load_observations, HistoryScope
from portfolio_app.interface.views.history.history_data_ui import _market_form, scope_names
path=Path({str(tmp_path)!r})
book=SecurityBook('position-0','invented',('1','10','USD'))
journal=Journal(version=2,securities=(book,))
scope=HistoryScope(('position-0',))
store,revision=load_observations(path)
_market_form(path,journal,scope,store,revision,scope_names(path,journal,scope),'EUR','form_',end='2024-01-01')
''').run()
    assert not app.exception
    assert not app.checkbox and not app.selectbox
    assert [e.label for e in app.button] == ['Replace saved value']
    assert app.text_input[0].value == 'USD'
    app.text_input[1].set_value('10.12345').run()
    app.button[0].click().run()
    assert not app.exception
    saved = load_observations(tmp_path)[0]
    assert saved.observations[-1].amount == D('10.12345')
    assert saved.observations[-1].replaces == 'p1'


def test_advanced_flow_values_and_cash_classification_keep_readable_labels(tmp_path):
    from portfolio_app.ledger.account_journal import Account, Event, save_journal
    journal = Journal(version=2, accounts=(Account('account-private-id', 'Invented account'),), events=(
        Event('open', '2024-01-01', 'cash_open', account_id='account-private-id', currency='EUR', amount=D(100)),
        Event('payment-private-id', '2024-01-02', 'cash_flow', account_id='account-private-id', currency='EUR', amount=D(20)),
        Event('adjustment-private-id', '2024-01-03', 'cash_adjust', account_id='account-private-id', currency='EUR', amount=D(5)),
    ))
    save_journal(tmp_path, journal, None)
    app = AppTest.from_string(f'''
from pathlib import Path
from types import SimpleNamespace
from portfolio_app.ledger.account_journal import load_journal
from portfolio_app.performance.history_observations import HistoryScope, load_observations
from portfolio_app.interface.views.history.history_data_ui import _advanced, scope_names
path=Path({str(tmp_path)!r})
journal,_=load_journal(path)
scope=HistoryScope(accounts=('account-private-id',))
store,revision=load_observations(path)
view=SimpleNamespace(all_start='2024-01-01',all_end='2024-01-03',today='2024-01-03')
_advanced(path,journal,scope,store,revision,view,'EUR',scope_names(path,journal,scope),'advanced_')
''').run()
    assert not app.exception
    app.selectbox[0].set_value('Value around a cash flow').run()
    assert not app.exception
    assert app.selectbox[1].options == ['2024-01-02 · €20.00']
    assert all('private-id' not in option for item in app.selectbox for option in item.options)
    app.text_input[1].set_value('100').run()
    app.button[0].click().run()
    assert not app.exception
    saved = load_observations(tmp_path)[0]
    assert saved.observations[0].side == 'before'
    assert saved.observations[0].boundary == 'payment-private-id'
    assert saved.observations[0].subject == HistoryScope(accounts=('account-private-id',)).key
    app.selectbox[0].set_value('Cash adjustment meaning').run()
    assert not app.exception
    assert 'Invented account' in app.selectbox[1].label
    assert 'private-id' not in app.selectbox[1].label
    app.selectbox[1].set_value('income').run()
    app.button[0].click().run()
    assert not app.exception
    assert dict(load_observations(tmp_path)[0].classifications)['adjustment-private-id'] == 'income'


def test_instrument_level_price_is_corrected_instead_of_creating_competing_position_price(tmp_path):
    journal, scope, store, revision = fixture(tmp_path)
    store = replace(store, observations=tuple(replace(o, subject='invented') if o.id == 'p1' else o for o in store.observations))
    save_observations(tmp_path, store, revision)
    app = AppTest.from_string(f'''
from pathlib import Path
from portfolio_app.ledger.account_journal import Journal
from portfolio_app.ledger.security_history import SecurityBook
from portfolio_app.performance.history_observations import load_observations, HistoryScope
from portfolio_app.interface.views.history.history_data_ui import _market_form, scope_names
path=Path({str(tmp_path)!r})
journal=Journal(version=2,securities=(SecurityBook('position-0','invented',('1','10','USD')),))
scope=HistoryScope(('position-0',))
store,revision=load_observations(path)
_market_form(path,journal,scope,store,revision,scope_names(path,journal,scope),'EUR','form_',end='2024-01-01')
''').run()
    assert not app.exception
    assert app.button[0].label == 'Replace saved value'
    app.text_input[1].set_value('11').run()
    app.button[0].click().run()
    assert not app.exception
    corrected = load_observations(tmp_path)[0].observations[-1]
    assert corrected.subject == 'invented' and corrected.replaces == 'p1'


def test_price_correction_retains_quote_provenance_and_manual_download_precedence(tmp_path):
    from portfolio_app.performance.personal_market import merge_download
    original = Observation('download', 'price', 'position-0', '2024-01-02', D(10), 'USD',
        source='Invented exchange feed', listing='SYNTH', calendar='XNYS', exchange_timezone='America/New_York')
    store = ObservationStore((original,))
    save_observations(tmp_path, store, None)
    revised = save_manual_observation(tmp_path, store, load_observations(tmp_path)[1],
        replace(original, id='edit', amount=D(11), source='manual', listing='', calendar='', exchange_timezone=''),
        replace_existing=True)
    saved = revised.observations[-1]
    assert revised.observations[0] == original and saved.replaces == original.id
    assert (saved.source, saved.listing, saved.calendar, saved.exchange_timezone) == (
        'manual: Invented exchange feed', 'SYNTH', 'XNYS', 'America/New_York')
    assert merge_download(revised, (replace(original, id='redownload', amount=D(12)),)) == revised
    again = save_manual_observation(tmp_path, revised, load_observations(tmp_path)[1],
        replace(saved, id='edit-again', amount=D(13)), replace_existing=True)
    assert again.observations[-1].source == saved.source
    assert again.observations[-1].replaces == 'edit'


def test_new_manual_quote_source_is_protected_without_changing_holding_listing(tmp_path):
    from portfolio_app.performance.personal_market import merge_download
    (tmp_path/'holdings.csv').write_text('id,name,shares,ticker\ninvented,Invented asset,1,HELD\n')
    before = (tmp_path/'holdings.csv').read_bytes()
    app = AppTest.from_string(f'''
from pathlib import Path
from portfolio_app.ledger.account_journal import Journal
from portfolio_app.ledger.security_history import SecurityBook
from portfolio_app.performance.history_observations import load_observations, HistoryScope
from portfolio_app.interface.views.history.history_data_ui import _market_form, scope_names
path=Path({str(tmp_path)!r})
journal=Journal(version=2,securities=(SecurityBook('position-0','invented',('1','10','USD')),))
scope=HistoryScope(('position-0',))
store,revision=load_observations(path)
_market_form(path,journal,scope,store,revision,scope_names(path,journal,scope),'EUR','form_',end='2024-01-02')
''').run()
    assert not app.exception
    assert [e.label for e in app.expander] == ['Quote provenance']
    for label, value in [('Price currency','USD'), ('Price (USD)','10.5'),
                         ('Source','Invented closing bulletin'), ('Quote listing','ALTERNATIVE'),
                         ('Exchange calendar','XNYS'), ('Exchange timezone','America/New_York')]:
        next(t for t in app.text_input if t.label == label).set_value(value).run()
    next(b for b in app.button if b.label == 'Save value').click().run()
    assert not app.exception
    store = load_observations(tmp_path)[0]
    quote = store.observations[-1]
    assert (quote.source, quote.listing, quote.calendar, quote.exchange_timezone) == (
        'manual: Invented closing bulletin', 'ALTERNATIVE', 'XNYS', 'America/New_York')
    assert (tmp_path/'holdings.csv').read_bytes() == before
    assert merge_download(store, (replace(quote, id='download', source='Invented provider', amount=D(99)),)) == store
    # A new date must not inherit another quote's metadata accidentally.
    app.date_input[0].set_value(date(2024, 1, 3)).run()
    assert next(t for t in app.text_input if t.label == 'Quote listing').value == ''
    assert next(t for t in app.text_input if t.label == 'Source').value == 'manual'


def test_distinct_holding_names_and_unrepairable_reasons_remain_available(tmp_path):
    from portfolio_app.interface.views.history.history_data_ui import brief_reasons
    from portfolio_app.performance.history_diagnostics import HistoryIssue
    journal, scope, _, _ = fixture(tmp_path)
    reason = 'missing price on 2024-01-02'
    children = (f'position-0: {reason}', f'position-1: {reason}')
    reasons = [HistoryIssue('Boundary. '+' '.join(children), 'boundary', children),
               'Incomplete history', 'Negative equity']
    names = {'position-0': 'Xetra-Gold', 'position-1': 'Gold'}
    visible = readable_reasons(tmp_path, journal, scope, reasons, names=names)
    assert visible == ('Boundary.', 'Xetra-Gold: '+reason, 'Gold: '+reason, 'Incomplete history', 'Negative equity')
    assert brief_reasons(visible).endswith('(+2 more)')


def test_additional_nonmarket_reasons_open_without_a_market_editor(tmp_path):
    from test_ui import open_popover
    app = AppTest.from_string(f"""
from pathlib import Path
from portfolio_app.ledger.account_journal import Journal
from portfolio_app.performance.history_observations import ObservationStore, HistoryScope
from portfolio_app.interface.views.history.history_data_ui import render_return_inputs
render_return_inputs(Path({str(tmp_path)!r}), Journal(), HistoryScope(), ObservationStore(), None,
    ('Unreviewed transactions', 'Negative equity', 'Unknown payment amount', 'Unresolved withholding'), 'test_')
""").run()
    open_popover(app, 'Return details')
    assert not app.exception
    assert 'Unresolved withholding' in [line.value for line in app.text]
    assert not app.text_input and not app.button


def test_visible_history_reasons_share_names_and_limit_after_formatting(tmp_path):
    from portfolio_app.interface.views.history.history_data_ui import HistoryReasonFormatter

    journal, scope, _, _ = fixture(tmp_path)
    reads = []
    def load_names():
        reads.append(True)
        return scope_names(tmp_path, journal, scope)
    formatter = HistoryReasonFormatter(journal, load_names)
    assert formatter.format(()) == () and not reads
    first = 'position-0: missing price'
    reasons = (first, first, 'unknown-a: missing FX', 'unknown-b: missing FX', scope.key+': missing value')
    expected = readable_reasons(tmp_path, journal, scope, reasons)
    def bounded_input():
        yield from reasons
        raise AssertionError('The visible explanations were already complete')
    assert formatter.format(bounded_input(), limit=3) == expected[:3]
    assert formatter.format((first,)) == expected[:1]
    assert len(reads) == 1


def test_missing_inputs_share_indexed_dated_rates_and_last_active_price_currency(tmp_path, monkeypatch):
    from portfolio_app.performance import history_valuation
    from portfolio_app.performance.history_observations import index_observations
    journal, scope, store, _ = fixture(tmp_path)
    # The most recently saved row corrects an earlier boundary in another currency.
    store = replace(store, observations=store.observations + (
        Observation('later', 'price', 'position-0', '2024-01-03', D(11), 'GBP'),
        Observation('correction', 'price', 'position-0', '2024-01-01', D(9), 'CAD',
                    side='after', boundary='opening', replaces='p1'),))
    index = index_observations(store)
    lookups = []
    actual_fx = history_valuation.valuation_fx
    def counted(source_index, source, target, stamp):
        assert source_index is index
        lookups.append((source, target, stamp))
        return actual_fx(source_index, source, target, stamp)
    monkeypatch.setattr(history_valuation, 'valuation_fx', counted)
    reasons = ('position-0: missing price on 2024-01-02',
               'position-0: missing value or USD/EUR FX on 2024-01-01',
               'position-0: missing value or USD/EUR FX on 2024-01-02')
    tasks = missing_inputs(view(*reasons, *reasons), journal, scope, store, 'EUR')
    assert [(item.kind, item.currency) for item in tasks] == [('fx', 'USD'), ('price', 'CAD')]
    assert lookups == [('USD', 'EUR', '2024-01-01'), ('USD', 'EUR', '2024-01-02')]


def test_domestic_corrections_do_not_build_a_market_index_without_a_dated_issue(tmp_path, monkeypatch):
    from portfolio_app.interface.views.history import history_data_ui as corrections
    journal, scope, store, _ = fixture(tmp_path)
    def unexpected(_):
        pytest.fail('A domestic cash flow needs no historical rate lookup')
    monkeypatch.setattr(corrections, 'index_observations', unexpected)
    assert not missing_inputs(view('position-0: missing value or USD/USD FX on 2024-01-01'),
                              journal, scope, store, 'USD')


def test_correction_tasks_preserve_conflicting_rate_errors(tmp_path):
    journal, scope, store, _ = fixture(tmp_path)
    store = replace(store, observations=store.observations + (
        Observation('opposite', 'fx', 'EUR/USD', '2024-01-01', D(2), 'EUR', 'USD'),))
    with pytest.raises(DataError, match='Conflicting direct and inverse'):
        missing_inputs(view('position-0: missing value or USD/EUR FX on 2024-01-01'),
                       journal, scope, store, 'EUR')


def test_missing_exact_marks_fall_back_to_the_scoped_contract_currency():
    from portfolio_app.derivatives.derivative_types import ContractSpec
    from portfolio_app.ledger.account_journal import Account, PositionSpec
    contract = ContractSpec('INVENTED', '2026-12-18', 'GBP', 10, 1)
    account = Account('account', 'Invented account')
    position = PositionSpec('contract-position', account.id, contract.id)
    journal = Journal(version=2, accounts=(account,), contracts=(contract,), positions=(position,))
    reasons = [f'{identity}: missing exact-contract mark on 2024-01-02' for identity in (position.id, contract.id)]
    tasks = missing_inputs(view(*reasons), journal, HistoryScope(accounts=(account.id,)), ObservationStore(), 'EUR')
    assert {(t.subject, t.currency) for t in tasks} == {(position.id, 'GBP'), (contract.id, 'GBP')}
