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


def test_rejected_swaps_reserve_destination_and_reopen_without_placeholders(tmp_path):
    first = b'reference,date,type,asset,quantity,amount,currency\na,2024-01-02,buy,BTC,10,100,EUR\n'
    app = launch(tmp_path,first)
    button(app,'Review against portfolio').click().run()
    button(app,'Apply history').click().run()
    original = (tmp_path/'holdings.csv').read_bytes()
    content = (b'reference,date,type,asset,quantity,amount,currency,received_asset,received_quantity\n'
        b'x,2024-01-03,exchange,BTC,1,10,EUR,ETH,2\ny,2024-01-03,exchange,BTC,1,10,EUR,ETH,2\n')
    app = launch(tmp_path,content)
    button(app,'Review against portfolio').click().run()
    assert not app.exception
    next(s for s in app.multiselect if s.label.startswith('Keep transactions')).set_value([]).run()
    button(app,'Save exclusions').click().run()
    assert not app.exception and (tmp_path/'holdings.csv').read_bytes() == original
    journal,revision = load_journal(tmp_path)
    assert len(journal.securities) == 2 and not journal.securities[1].active
    reopened = launch(tmp_path,content)
    assert not reopened.exception and not any('missing' in c.value for c in reopened.caption)
    button(reopened,'Review against portfolio').click().run()
    assert not reopened.exception
    assert load_journal(tmp_path) == (journal,revision)
    from portfolio_app.csv_transaction_imports import prepare_import
    from portfolio_app.positions import read_snapshot
    rows = read_csv(content)
    mapping = {(a,asset):p for r in journal.transaction_imports for a,asset,p in r.positions}
    again = prepare_import(journal,read_snapshot(tmp_path/'holdings.csv').holdings,rows,mapping,
        selected_ids=[r.identity for r in rows])
    assert again.journal == journal and again.applied == 0


def test_new_source_after_rejected_correction_shows_surviving_manual_edit(tmp_path):
    from dataclasses import replace
    from decimal import Decimal
    from portfolio_app.history_commands import correct_transaction, save_history
    from portfolio_app.positions import read_snapshot
    from portfolio_app.transaction_import_commands import save_transaction_import
    header = 'reference,date,type,asset,quantity,amount,currency\n'
    original = header+'a,2024-01-02,buy,BTC,10,100,EUR\n'
    app = launch(tmp_path,original.encode())
    button(app,'Review against portfolio').click().run()
    button(app,'Apply history').click().run()
    journal,revision = load_journal(tmp_path)
    source = journal.transaction_imports[0]
    candidate = correct_transaction(journal.securities[0],source.id,quantity=Decimal(11))
    edited = candidate.records[-1]
    candidate = replace(candidate,active_ids=tuple(i for i in candidate.active_ids if i!=source.id)+(edited.id,))
    save_history(tmp_path,candidate,expected_journal_revision=revision,
        expected_holdings_revision=read_snapshot(tmp_path/'holdings.csv').revision,activate=True,accept_balance_change=True)
    journal,revision = load_journal(tmp_path)
    pending = read_csv(header+'a,2024-01-02,buy,BTC,10,102,EUR\n')
    mapping = {(a,asset):p for a,asset,p in source.positions}
    save_transaction_import(tmp_path,pending,mapping,expected_journal_revision=revision,
        expected_holdings_revision=read_snapshot(tmp_path/'holdings.csv').revision,
        correction_ids=[pending[0].identity],excluded_ids=[pending[0].identity],
        accept_reconciliation=True,comparison_date='2024-01-03')
    app = launch(tmp_path,(header+'a,2024-01-02,buy,BTC,10,103,EUR\n').encode())
    assert not app.exception
    assert any('replaces your later edit' in item.value for item in app.warning)
    assert not any('restores a transaction' in item.value for item in app.warning)
    assert any('11' in table.value.to_string() for table in app.table)


def test_matching_holdings_preview_then_confirm_without_blanket_checkboxes(tmp_path):
    (tmp_path/'holdings.csv').write_text('id,name,ticker,instrument_type,shares,acquisition_price,acquisition_currency\n'
        'invented-btc,Bitcoin,BTC-EUR,crypto,2,50,EUR\n')
    original = (tmp_path/'holdings.csv').read_bytes()
    app = launch(tmp_path, b'reference,date,type,asset,quantity,amount,currency\nb,2024-01-02,buy,BTC,2,100,EUR\n')
    assert not app.exception
    assert not app.checkbox
    assert not any(b.label.startswith('Apply') for b in app.button)
    button(app, 'Review against portfolio').click().run()
    assert not app.exception
    assert app.table[0].value['From history'].tolist() == ['2', '100.00', '50.00']
    assert any(item.value == 'Units and cost match' for item in app.caption)
    assert not any(s.label.startswith('Holding ·') for s in app.selectbox)
    assert not button(app, 'Apply history').disabled
    assert (tmp_path/'holdings.csv').read_bytes() == original
    assert not load_journal(tmp_path)[0].securities
    button(app, 'Apply history').click().run()
    assert not app.exception
    journal = load_journal(tmp_path)[0]
    assert sum(s.quantity for s in replay_securities(journal.securities).positions.values()) == 2
    assert not journal.securities[0].reviews[-1].complete_from


def test_earlier_holdings_require_explicit_starting_inventory(tmp_path):
    app = launch(tmp_path, b'reference,date,type,asset,quantity,amount,currency\nb,2024-01-02,buy,BTC,2,100,EUR\n')
    next(s for s in app.selectbox if s.label == 'Before the first transaction').set_value('Earlier holdings').run()
    assert not app.exception
    assert not any(b.label.startswith('Apply') for b in app.button)
    assert any(i.value == 'Starting balance required' for i in app.info)
    assert not (tmp_path/'holdings.csv').exists()


@pytest.mark.parametrize('decision,quantity', [('keep',2), ('exclude',1)])
def test_similar_rows_default_to_keep_and_can_be_excluded(tmp_path, decision, quantity):
    content = (b'reference,date,type,asset,quantity,amount,currency\n'
        b'a,2024-01-02,buy,BTC,1,100,EUR\nb,2024-01-02,buy,BTC,1,100,EUR\n')
    app = launch(tmp_path, content)
    assert not app.exception
    button(app, 'Review against portfolio').click().run()
    assert not app.exception
    assert not button(app, 'Apply history').disabled
    assert any(list(frame.value.get('Entry', [])) == [1, 2] for frame in app.dataframe)
    choice = next(s for s in app.multiselect if s.label.startswith('Keep transactions'))
    assert len(choice.value) == 2
    if decision == 'exclude':
        choice.set_value([read_csv(content)[0].identity]).run()
    assert not app.exception
    assert not button(app, 'Apply history').disabled
    button(app, 'Apply history').click().run()
    assert not app.exception
    journal = load_journal(tmp_path)[0]
    assert len(journal.transaction_imports) == 2
    assert sum(s.quantity for s in replay_securities(journal.securities).positions.values()) == quantity
    from portfolio_app.history_observations import HistoryScope, Observation, ObservationStore
    from portfolio_app.personal_performance import calculate_history
    identity = journal.securities[0].position_id
    obs = ObservationStore((Observation('invented-close', 'price', identity, '2025-01-02', 110, 'EUR'),))
    result = calculate_history(journal, HistoryScope((identity,)), obs, '2024-01-01', '2025-01-02')
    assert result.total_gain == 10*quantity and result.time_weighted == pytest.approx(.1)


def test_all_excluded_new_asset_retains_decision_without_creating_a_holding(tmp_path):
    content = (b'reference,date,type,asset,quantity,amount,currency\n'
        b'a,2024-01-02,buy,BTC,1,100,EUR\nb,2024-01-02,buy,BTC,1,100,EUR\n')
    app = launch(tmp_path, content)
    button(app, 'Review against portfolio').click().run()
    next(s for s in app.multiselect if s.label.startswith('Keep transactions')).set_value([]).run()
    assert not app.exception
    button(app, 'Save exclusions').click().run()
    assert not app.exception
    assert not (tmp_path/'holdings.csv').exists()
    journal, revision = load_journal(tmp_path)
    assert len(journal.transaction_imports) == 2 and not journal.securities[0].active
    assert len(journal.securities[0].active_ids) == 2
    reopened = launch(tmp_path, content)
    assert not reopened.exception
    button(reopened, 'Review against portfolio').click().run()
    assert not reopened.exception
    assert any(c.value == 'No new transactions selected' for c in reopened.caption)
    assert load_journal(tmp_path)[1] == revision


def test_asset_filter_includes_received_leg_without_excluding_other_transactions(tmp_path):
    app = launch(tmp_path, b'reference,date,type,asset,quantity,amount,currency,received_asset,received_quantity\n'
        b'b,2024-01-01,buy,AUR,10,100,EUR,,\nx,2024-01-02,exchange,AUR,2,25,EUR,ORB,4\n')
    next(s for s in app.selectbox if s.label == 'Show transactions for').set_value('ORB').run()
    assert not app.exception
    assert app.dataframe[0].value.Transaction.tolist() == ['Exchange']
    button(app, 'Review against portfolio').click().run()
    assert not app.exception
    assert button(app, 'Apply reviewed holdings').disabled
    first = next(s for s in app.selectbox if s.label == 'Holding to review').value
    button(app, 'Accept this holding').click().run()
    assert not app.exception
    assert button(app, 'Apply reviewed holdings').disabled
    assert not load_journal(tmp_path)[0].securities
    assert next(s for s in app.selectbox if s.label == 'Holding to review').value != first
    button(app, 'Accept this holding').click().run()
    assert not app.exception
    button(app, 'Apply reviewed holdings').click().run()
    assert not app.exception
    journal = load_journal(tmp_path)[0]
    assert all(b.reviews and not b.reviews[-1].complete_from for b in journal.securities)
    positions = replay_securities(load_journal(tmp_path)[0].securities).positions
    assert sorted(s.quantity for s in positions.values()) == [4,8]


def test_similar_entries_are_one_group_and_exclusions_recompute_balances(tmp_path):
    content = (b'reference,date,type,asset,quantity,amount,currency\n'
        b'a,2024-01-02,buy,BTC,1,100,EUR\nb,2024-01-02,buy,BTC,1,100,EUR\nc,2024-01-02,buy,BTC,1,100,EUR\n')
    app = launch(tmp_path, content)
    button(app, 'Review against portfolio').click().run()
    assert not app.exception
    choices = [s for s in app.multiselect if s.label.startswith('Keep transactions')]
    assert len(choices) == 1 and len(choices[0].value) == 3
    assert app.table[0].value['From history'].iloc[0] == '3'
    entries = read_csv(content)
    choices[0].set_value([entries[0].identity, entries[2].identity]).run()
    assert not app.exception
    assert app.table[0].value['From history'].iloc[0] == '2'
    assert not button(app, 'Apply history').disabled
    button(app, 'Apply history').click().run()
    assert not app.exception
    positions = replay_securities(load_journal(tmp_path)[0].securities).positions
    assert sum(s.quantity for s in positions.values()) == 2


def test_internal_references_never_appear_in_review_tables_or_choices(tmp_path):
    first, second = 'a1'*32, 'b2'*32
    content = ('reference,date,type,asset,quantity,amount,currency\n'
        f'{first},2024-01-02,buy,BTC,1,100,EUR\n{second},2024-01-02,buy,BTC,1,100,EUR\n').encode()
    app = launch(tmp_path, content)
    button(app, 'Review against portfolio').click().run()
    assert not app.exception
    visible = []
    for frame in app.dataframe:
        assert 'Reference' not in frame.value.columns
        assert 'position_id' not in frame.value.columns
        visible.append(frame.value.to_string())
    for field in [*app.selectbox, *app.multiselect]:
        visible.extend(field.options)
    assert not any(first in text or second in text for text in visible)
    assert any(field.label.startswith('Keep transactions') for field in app.multiselect)


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
    button(app, 'Review against portfolio').click().run()
    assert not app.exception
    assert any('checkpoint_units' in frame.value for frame in app.dataframe)
    for frame in app.dataframe:
        assert 'position_id' not in frame.value.columns and 'Reference' not in frame.value.columns
        assert 'csv-position-' not in frame.value.to_string()


@pytest.mark.parametrize('source', ['manual', 'consors_pdf:invented', 'transaction_csv_v3'])
def test_partial_fills_use_same_review_for_every_source(tmp_path, source):
    from dataclasses import replace
    from portfolio_app.account_journal import Journal, save_journal
    from portfolio_app.csv_transaction_imports import event_for
    from portfolio_app.history_commands import book_for
    from portfolio_app.positions import read_snapshot
    from test_history_ui import launch as history_app
    content = (b'reference,date,type,asset,quantity,amount,currency\n'
        b'buy,2024-01-01,buy,AUR,6,60,EUR\n'
        b's1,2024-01-02,sell,AUR,2,30,EUR\ns2,2024-01-02,sell,AUR,2,30,EUR\ns3,2024-01-02,sell,AUR,2,30,EUR\n')
    csv_dir, history_dir = tmp_path/'csv', tmp_path/'history'
    for directory in (csv_dir, history_dir):
        directory.mkdir()
        (directory/'holdings.csv').write_text('id,name,ticker,shares\naur,Aurora,AUR,0\n')
    imported = launch(csv_dir, content)
    button(imported, 'Review against portfolio').click().run()
    assert not imported.exception
    row = read_snapshot(history_dir/'holdings.csv').holdings.iloc[0]
    entries = read_csv(content)
    mapping = {pair:row.position_id for entry in entries for pair in entry.pairs}
    records = tuple(replace(event_for(entry, mapping), source=source) for entry in entries)
    book = replace(book_for(Journal(), row), records=records)
    from portfolio_app.transaction_csv import ImportReceipt
    receipts = tuple(ImportReceipt(entry.identity, entry.raw, entry.facts,
        positions=tuple((a, asset, row.position_id) for a,asset in entry.pairs)) for entry in entries) if source.startswith('transaction_csv_v') else ()
    save_journal(history_dir, Journal(version=5, securities=(book,), transaction_imports=receipts), None)
    ordinary = history_app(history_dir)
    button(ordinary, 'Review against portfolio').click().run()
    assert not ordinary.exception
    assert imported.table[0].value.equals(ordinary.table[0].value)
    for app in (imported, ordinary):
        assert not any(s.label.startswith('Resolve match') for s in app.selectbox)
        choices = [m for m in app.multiselect if m.label.startswith('Keep transactions')]
        assert len(choices) == 1 and len(choices[0].value) == 3
        assert any(c.value == 'All entries: units reconcile' for c in app.caption)
        assert not button(app, 'Apply history').disabled
        assert not app.radio
    button(ordinary, 'Apply history').click().run()
    assert not ordinary.exception
    assert next(iter(replay_securities(load_journal(history_dir)[0].securities).positions.values())).quantity == 0


def test_new_holding_zero_is_not_reconciliation_evidence(tmp_path):
    content = (b'reference,date,type,asset,quantity,amount,currency\n'
        b'buy,2024-01-01,buy,AUR,4,40,EUR\n'
        b's1,2024-01-02,sell,AUR,2,30,EUR\ns2,2024-01-02,sell,AUR,2,30,EUR\n')
    app = launch(tmp_path, content)
    button(app, 'Review against portfolio').click().run()
    assert not app.exception
    assert app.table[0].value['Current portfolio'].tolist() == ['—','—','—']
    assert not any(c.value == 'All entries: units reconcile' for c in app.caption)
    assert any(c.value == 'No reference balance' for c in app.caption)


def test_back_from_review_retains_mapping_and_does_not_save(tmp_path):
    app = launch(tmp_path, b'reference,date,type,asset,quantity,amount,currency\nb,2024-01-02,buy,BTC,2,100,EUR\n')
    next(t for t in app.text_input if t.label == 'Name · BTC').set_value('Invented name').run()
    button(app, 'Review against portfolio').click().run()
    button(app, 'Back to import').click().run()
    assert not app.exception
    assert next(t for t in app.text_input if t.label == 'Name · BTC').value == 'Invented name'
    assert not (tmp_path/'holdings.csv').exists()
    assert not load_journal(tmp_path)[0].transaction_imports


def test_review_rejects_concurrent_portfolio_change(tmp_path):
    app = launch(tmp_path, b'reference,date,type,asset,quantity,amount,currency\nb,2024-01-02,buy,BTC,2,100,EUR\n')
    button(app, 'Review against portfolio').click().run()
    (tmp_path/'holdings.csv').write_text('id,name,shares\ninvented,Invented,1\n')
    app.run()
    assert not app.exception
    assert not any(b.label.startswith('Apply') for b in app.button)
    assert button(app, 'Reload transaction review')
    assert not load_journal(tmp_path)[0].transaction_imports


def test_invalid_reconstruction_can_return_to_import_without_writes(tmp_path):
    app = launch(tmp_path, b'reference,date,type,asset,quantity,amount,currency\ns,2024-01-02,sell,BTC,2,100,EUR\n')
    button(app, 'Review against portfolio').click().run()
    assert not app.exception
    assert app.warning
    assert not any(b.label.startswith('Apply') for b in app.button)
    button(app, 'Back to import').click().run()
    assert not app.exception
    assert any(s.label == 'Holding · BTC' for s in app.selectbox)
    assert not (tmp_path/'holdings.csv').exists()
    assert not load_journal(tmp_path)[0].transaction_imports


def test_changing_reviewed_entries_requires_fresh_asset_decisions(tmp_path):
    app = launch(tmp_path, b'reference,date,type,asset,quantity,amount,currency\n'
        b'a,2024-01-01,buy,AUR,1,10,EUR\nb,2024-01-01,buy,AUR,1,10,EUR\nc,2024-01-01,buy,ORB,1,10,EUR\n')
    button(app, 'Review against portfolio').click().run()
    button(app, 'Accept this holding').click().run()
    button(app, 'Accept this holding').click().run()
    assert not app.exception
    assert not button(app, 'Apply reviewed holdings').disabled
    choice = next(s for s in app.multiselect if s.label.startswith('Keep transactions'))
    choice.set_value(choice.value[:1]).run()
    assert not app.exception
    assert button(app, 'Apply reviewed holdings').disabled
    assert any(c.value == 'Reviewed 0 / 2' for c in app.caption)
    assert not load_journal(tmp_path)[0].securities
