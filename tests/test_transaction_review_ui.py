"""Synthetic review decisions, with no live data or private portfolio access."""
import pytest
from streamlit.testing.v1 import AppTest

from portfolio_app.account_journal import load_journal
from portfolio_app.security_history import replay_securities
from portfolio_app.transaction_csv import read_csv


def launch(directory, content):
    def script(directory, content):
        from pathlib import Path
        import streamlit as st
        from portfolio_app.csv_transaction_import_ui import render_transaction_import
        st.session_state['import_tx_content'] = ('invented.csv', content)
        render_transaction_import(Path(directory), demo=True)
    return AppTest.from_function(script, args=(str(directory), content)).run()


def button(app, label):
    return next(item for item in app.button if item.label == label)


def test_matching_holdings_preview_then_confirm_without_blanket_checkboxes(tmp_path):
    (tmp_path/'holdings.csv').write_text('id,name,ticker,instrument_type,shares,acquisition_price,acquisition_currency\n'
        'invented-btc,Bitcoin,BTC-EUR,crypto,2,50,EUR\n')
    original = (tmp_path/'holdings.csv').read_bytes()
    app = launch(tmp_path, b'reference,date,type,asset,quantity,amount,currency\nb,2024-01-02,buy,BTC,2,100,EUR\n')
    assert not app.exception
    assert not app.checkbox
    assert app.dataframe[-1].value.Comparison.tolist() == ['Units and cost match']
    assert any('Applying confirms this starting point' in item.value for item in app.caption)
    assert not button(app, 'Apply and confirm history').disabled
    assert (tmp_path/'holdings.csv').read_bytes() == original
    assert not load_journal(tmp_path)[0].securities
    button(app, 'Apply and confirm history').click().run()
    assert not app.exception
    journal = load_journal(tmp_path)[0]
    assert sum(s.quantity for s in replay_securities(journal.securities).positions.values()) == 2
    assert journal.securities[0].reviews[-1].complete_from


def test_earlier_holdings_require_explicit_starting_inventory(tmp_path):
    app = launch(tmp_path, b'reference,date,type,asset,quantity,amount,currency\nb,2024-01-02,buy,BTC,2,100,EUR\n')
    next(s for s in app.selectbox if s.label == 'Before the first transaction').set_value('Earlier holdings').run()
    assert not app.exception
    assert not any(b.label.startswith('Apply') for b in app.button)
    assert any('Include a starting balance' in i.value for i in app.info)
    assert not (tmp_path/'holdings.csv').exists()


@pytest.mark.parametrize('decision,quantity', [('keep',2), ('exclude',1)])
def test_matching_rows_require_local_decision_and_can_be_excluded(tmp_path, decision, quantity):
    content = (b'reference,date,type,asset,quantity,amount,currency\n'
        b'a,2024-01-02,buy,BTC,1,100,EUR\nb,2024-01-02,buy,BTC,1,100,EUR\n')
    app = launch(tmp_path, content)
    assert not app.exception
    assert button(app, 'Apply and confirm history').disabled
    assert any(list(frame.value.get('Entry', [])) == ['First entry','Second entry'] for frame in app.dataframe)
    choice = next(s for s in app.selectbox if s.label.startswith('Resolve match'))
    choice.set_value('keep' if decision == 'keep' else read_csv(content)[1].identity).run()
    assert not app.exception
    assert not button(app, 'Apply and confirm history').disabled
    button(app, 'Apply and confirm history').click().run()
    assert not app.exception
    journal = load_journal(tmp_path)[0]
    assert len(journal.transaction_imports) == 2
    assert sum(s.quantity for s in replay_securities(journal.securities).positions.values()) == quantity


def test_asset_filter_includes_received_leg_without_excluding_other_transactions(tmp_path):
    app = launch(tmp_path, b'reference,date,type,asset,quantity,amount,currency,received_asset,received_quantity\n'
        b'b,2024-01-01,buy,AUR,10,100,EUR,,\nx,2024-01-02,exchange,AUR,2,25,EUR,ORB,4\n')
    next(s for s in app.selectbox if s.label == 'Show transactions for').set_value('ORB').run()
    assert not app.exception
    assert app.dataframe[0].value.Transaction.tolist() == ['Exchange']
    button(app, 'Apply incomplete history').click().run()
    assert not app.exception
    positions = replay_securities(load_journal(tmp_path)[0].securities).positions
    assert sorted(s.quantity for s in positions.values()) == [4,8]


def test_excluding_middle_duplicate_requires_review_of_remaining_pair(tmp_path):
    content = (b'reference,date,type,asset,quantity,amount,currency\n'
        b'a,2024-01-02,buy,BTC,1,100,EUR\nb,2024-01-02,buy,BTC,1,100,EUR\nc,2024-01-02,buy,BTC,1,100,EUR\n')
    app = launch(tmp_path, content)
    middle = read_csv(content)[1].identity
    matches = [s for s in app.selectbox if s.label.startswith('Resolve match')]
    matches[0].set_value(middle).run()
    assert not app.exception
    assert button(app, 'Apply and confirm history').disabled
    matches = [s for s in app.selectbox if s.label.startswith('Resolve match')]
    assert len(matches) == 3
    matches[-1].set_value('keep').run()
    assert not app.exception
    assert not button(app, 'Apply and confirm history').disabled
    button(app, 'Apply and confirm history').click().run()
    assert not app.exception
    positions = replay_securities(load_journal(tmp_path)[0].securities).positions
    assert sum(s.quantity for s in positions.values()) == 2


def test_internal_references_never_appear_in_review_tables_or_choices(tmp_path):
    first, second = 'a1'*32, 'b2'*32
    content = ('reference,date,type,asset,quantity,amount,currency\n'
        f'{first},2024-01-02,buy,BTC,1,100,EUR\n{second},2024-01-02,buy,BTC,1,100,EUR\n').encode()
    app = launch(tmp_path, content)
    assert not app.exception
    visible = []
    for frame in app.dataframe:
        assert 'Reference' not in frame.value.columns
        assert 'position_id' not in frame.value.columns
        visible.append(frame.value.to_string())
    for field in [*app.selectbox, *app.multiselect]:
        visible.extend(field.options)
    assert not any(first in text or second in text for text in visible)
    assert any('Exclude first entry' in field.options for field in app.selectbox)


def test_name_edit_survives_listing_change_and_destination_switch(tmp_path):
    (tmp_path/'holdings.csv').write_text('id,name,ticker,instrument_type,shares\ninvented-existing,Bitcoin,BTC-EUR,crypto,2\n')
    app = launch(tmp_path, b'reference,date,type,asset,quantity,amount,currency\nb,2024-01-02,buy,ETH,1,100,EUR\n')
    next(t for t in app.text_input if t.label == 'Name · ETH').set_value('Invented coin holding').run()
    next(s for s in app.selectbox if s.label == 'Price listing · ETH').set_value('ETH-USD').run()
    assert next(t for t in app.text_input if t.label == 'Name · ETH').value == 'Invented coin holding'
    holding = next(s for s in app.selectbox if s.label == 'Holding · ETH')
    assert holding.options[0] == 'New position'
    assert not any(s.label.startswith('Destination ·') for s in app.selectbox)
    from portfolio_app.positions import read_snapshot
    existing = read_snapshot(tmp_path/'holdings.csv').holdings.position_id.iloc[0]
    holding.set_value(existing).run()
    assert not app.exception
    assert not any(t.label == 'Name · ETH' for t in app.text_input)
    next(s for s in app.selectbox if s.label == 'Holding · ETH').set_value('').run()
    assert not app.exception
    assert next(t for t in app.text_input if t.label == 'Name · ETH').value == 'Invented coin holding'


def test_automatic_name_follows_listing_until_user_edits_it(tmp_path):
    app = launch(tmp_path, b'reference,date,type,asset,quantity,amount,currency\nb,2024-01-02,buy,BTC,1,100,EUR\n')
    next(s for s in app.selectbox if s.label == 'Price listing · BTC').set_value('').run()
    assert next(t for t in app.text_input if t.label == 'Name · BTC').value == 'BTC'
    next(s for s in app.selectbox if s.label == 'Price listing · BTC').set_value('BTC-EUR').run()
    assert next(t for t in app.text_input if t.label == 'Name · BTC').value == 'Bitcoin'


def test_pdf_provenance_is_a_readable_source_label():
    from portfolio_app.history_ui import _source_label
    assert _source_label('consors_pdf:'+'a1'*32) == 'PDF import'
    assert _source_label('legacy:'+'b2'*16) == 'Imported purchase history'
    assert _source_label('c3'*32) == 'Recorded history'


def test_saved_import_selector_hides_ledger_identifiers(tmp_path):
    from test_csv_transaction_imports import row
    from portfolio_app.transaction_csv import normalize
    from portfolio_app.transaction_import_commands import save_transaction_import
    secret = 'a1'*32
    original = row(1, gross_amount='10', currency='EUR')
    item = normalize({**original.raw, 'source_id':secret})
    save_transaction_import(tmp_path, [item], {}, expected_journal_revision=None, expected_holdings_revision=None)
    def script(directory):
        from pathlib import Path
        from portfolio_app.csv_transaction_import_ui import render_transaction_import
        render_transaction_import(Path(directory), demo=True)
    app = AppTest.from_function(script, args=(str(tmp_path),)).run()
    next(r for r in app.radio if r.label == 'Transactions').set_value('Saved imports').run()
    assert not app.exception
    selector = next(s for s in app.selectbox if s.label == 'Import')
    assert selector.options == ['Saved transactions 1 · 2024-04-01 – 2024-04-01']
    assert all(secret not in frame.value.to_string() for frame in app.dataframe)


def test_checkpoint_review_hides_internal_position_keys(tmp_path):
    import csv
    from io import StringIO
    from test_inventory_imports import row, estimate
    from portfolio_app.transaction_csv import FIELDS_V2
    stream = StringIO()
    writer = csv.DictWriter(stream, fieldnames=FIELDS_V2)
    writer.writeheader(); writer.writerow(row('balance', **estimate()).facts)
    app = launch(tmp_path, stream.getvalue().encode())
    assert not app.exception
    assert any('checkpoint_units' in frame.value for frame in app.dataframe)
    for frame in app.dataframe:
        assert 'position_id' not in frame.value.columns and 'Reference' not in frame.value.columns
        assert 'csv-position-' not in frame.value.to_string()
