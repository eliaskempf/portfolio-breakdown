"""Focused history corrections use only invented holdings and static evidence."""
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from portfolio_app.account_journal import Journal
from portfolio_app.history_data_ui import (
    missing_inputs, readable_reasons, save_manual_observation, scope_names, scoped_observations,
)
from portfolio_app.history_observations import (
    HistoryScope, Observation, ObservationStore, active_observations, load_observations, save_observations,
)
from portfolio_app.holdings import DataError
from portfolio_app.security_history import SecurityBook, SecurityEvent


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
from portfolio_app.account_journal import Journal
from portfolio_app.history_observations import ObservationStore, HistoryScope
from portfolio_app.history_data_ui import render_history_data
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
from portfolio_app.account_journal import Journal
from portfolio_app.security_history import SecurityBook
from portfolio_app.history_observations import load_observations, HistoryScope
from portfolio_app.history_data_ui import _market_form, scope_names
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
    from portfolio_app.account_journal import Account, Event, save_journal
    journal = Journal(version=2, accounts=(Account('account-private-id', 'Invented account'),), events=(
        Event('open', '2024-01-01', 'cash_open', account_id='account-private-id', currency='EUR', amount=D(100)),
        Event('payment-private-id', '2024-01-02', 'cash_flow', account_id='account-private-id', currency='EUR', amount=D(20)),
        Event('adjustment-private-id', '2024-01-03', 'cash_adjust', account_id='account-private-id', currency='EUR', amount=D(5)),
    ))
    save_journal(tmp_path, journal, None)
    app = AppTest.from_string(f'''
from pathlib import Path
from types import SimpleNamespace
from portfolio_app.account_journal import load_journal
from portfolio_app.history_observations import HistoryScope, load_observations
from portfolio_app.history_data_ui import _advanced, scope_names
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
from portfolio_app.account_journal import Journal
from portfolio_app.security_history import SecurityBook
from portfolio_app.history_observations import load_observations, HistoryScope
from portfolio_app.history_data_ui import _market_form, scope_names
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
    from portfolio_app.personal_market import merge_download
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
    from portfolio_app.personal_market import merge_download
    (tmp_path/'holdings.csv').write_text('id,name,shares,ticker\ninvented,Invented asset,1,HELD\n')
    before = (tmp_path/'holdings.csv').read_bytes()
    app = AppTest.from_string(f'''
from pathlib import Path
from portfolio_app.account_journal import Journal
from portfolio_app.security_history import SecurityBook
from portfolio_app.history_observations import load_observations, HistoryScope
from portfolio_app.history_data_ui import _market_form, scope_names
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
