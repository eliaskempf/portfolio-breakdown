"""Synthetic history presentation keeps non-cash evidence visible."""
from streamlit.testing.v1 import AppTest
from test_transaction_import_commands import setup,save


def test_received_swap_income_fee_and_valuation_are_visible(tmp_path):
    snapshot=setup(tmp_path);save(tmp_path,snapshot)
    def script(directory):
        from pathlib import Path
        from portfolio_app.positions import read_snapshot
        from portfolio_app.history_ui import render_position_history
        root=Path(directory)
        row=read_snapshot(root/'holdings.csv').holdings.query("position_id == 'orb'").iloc[0]
        render_position_history(root,row)
    app=AppTest.from_function(script,args=(str(tmp_path),)).run()
    assert not app.exception
    from portfolio_app.account_journal import load_journal
    from portfolio_app.history_ui import _transaction_table
    journal, _ = load_journal(tmp_path)
    book = next(b for b in journal.securities if b.position_id == 'orb')
    table = _transaction_table([e for e in book.records if e.kind != 'opening'], book)
    income = table[table.Transaction == 'Income in units'].iloc[0]
    assert income['Trade value'] == '—' and '25' in income['Monetary valuation']
    owner = next(b for b in journal.securities if b.position_id == 'vault')
    exchange = _transaction_table([e for e in owner.records if e.kind == 'swap'], owner)
    assert exchange.iloc[0]['Transaction'] == 'Exchange'
    assert exchange.iloc[0]['Monetary valuation'] == '80.00 EUR'
    next(b for b in app.button if b.label == 'Review against portfolio').click().run()
    assert not app.exception
    assert any(b.label == 'Apply incomplete history' for b in app.button)


def test_checkpoint_history_shows_estimates_and_unknown_observation(tmp_path):
    from test_inventory_imports import row, estimate
    from portfolio_app.transaction_import_commands import prepare_positions, save_transaction_import
    snapshot=prepare_positions(tmp_path,[dict(position_key='desk',id='aur',name='Aurora',account='Desk')],expected_holdings_revision=None)
    r=row('balance',**estimate())
    save_transaction_import(tmp_path,[r],{('Desk','AUR'):'desk'},expected_journal_revision=None,
        expected_holdings_revision=snapshot.revision,selected_ids=[r.identity],accept_reconciliation=True,
        comparison_date='2024-04-01',accept_checkpoints=True)
    def script(directory):
        from pathlib import Path
        from portfolio_app.positions import read_snapshot
        from portfolio_app.history_ui import render_position_history
        root=Path(directory)
        render_position_history(root,read_snapshot(root/'holdings.csv').holdings.iloc[0])
    app=AppTest.from_function(script,args=(str(tmp_path),)).run()
    assert not app.exception
    from portfolio_app.account_journal import load_journal
    from portfolio_app.history_ui import _transaction_table
    book = load_journal(tmp_path)[0].securities[0]
    table = _transaction_table(list(book.records), book)
    assert table.iloc[0]['Transaction']=='Balance checkpoint'
    assert table.iloc[0]['Trade value']=='—'
    details = app.dataframe[0].value
    assert details.iloc[0]['Estimated']
    assert details.iloc[0]['Observed'] == 'Unknown'
    assert details.iloc[0]['Acquisition_cost'] == '80.00 EUR'
    assert not any('imported movements' in c.value for c in app.caption)


def test_legacy_timestamp_order_is_editable_without_rounding(tmp_path):
    from portfolio_app.account_journal import load_journal
    from portfolio_app.history_ui import _key
    snapshot=setup(tmp_path);save(tmp_path,snapshot)
    journal,_=load_journal(tmp_path)
    book=next(b for b in journal.securities if b.position_id=='vault')
    event=next(e for e in book.records if e.kind=='swap')
    assert event.sequence>2**53
    def script(directory,identity):
        from pathlib import Path
        import streamlit as st
        from portfolio_app.history_ui import render_position_history,_key
        from portfolio_app.positions import read_snapshot
        root=Path(directory);key=_key(root,'vault')
        st.session_state.setdefault(key+'screen','edit')
        st.session_state.setdefault(key+'edit_record',identity)
        render_position_history(root,read_snapshot(root/'holdings.csv').holdings.query("position_id == 'vault'").iloc[0])
    app=AppTest.from_function(script,args=(str(tmp_path),event.id)).run()
    assert not app.exception
    order=next(t for t in app.text_input if t.label=='Order within date')
    assert order.value==str(event.sequence)
    next(t for t in app.text_input if t.label=='Received quantity').set_value('4').run()
    next(b for b in app.button if b.label=='Save changes').click().run()
    assert not app.exception
    saved=next(b for b in load_journal(tmp_path)[0].securities if b.position_id=='vault')
    assert saved.records[-1].sequence==event.sequence
    assert saved.records[-1].replaces==event.id


def test_mixed_starting_balance_and_new_exchange_destination_can_be_reviewed(tmp_path):
    def script(directory):
        from pathlib import Path
        import streamlit as st
        from portfolio_app.csv_transaction_import_ui import render_transaction_import
        st.session_state['import_tx_content']=('invented.csv',
            b'reference,date,type,asset,quantity,amount,currency,received_asset,received_quantity\n'
            b'o,2024-01-01,opening,AUR,10,100,EUR,,\n'
            b'x,2024-01-02,exchange,AUR,2,25,EUR,ORB,4\n')
        render_transaction_import(Path(directory), demo=True)
    app=AppTest.from_function(script,args=(str(tmp_path),)).run()
    assert not app.exception
    assert not any(c.label == 'Holdings were zero before these transactions' for c in app.checkbox)
    assert not app.exception and not app.warning
    assert any(b.label=='Apply and confirm history' for b in app.button)
    assert not (tmp_path/'holdings.csv').exists()


def test_crypto_symbol_maps_to_existing_pair_without_creating_duplicate(tmp_path):
    from portfolio_app.transaction_import_commands import prepare_positions
    prepare_positions(tmp_path, [dict(position_key='invented-position', id='invented-bitcoin',
        name='Bitcoin', account='Invented wallet', instrument_type='crypto', ticker='BTC-EUR')],
        expected_holdings_revision=None)
    original = (tmp_path/'holdings.csv').read_bytes()
    def script(directory):
        from pathlib import Path
        import streamlit as st
        from portfolio_app.csv_transaction_import_ui import render_transaction_import
        st.session_state['import_tx_content'] = ('invented.csv',
            b'reference,date,type,asset,quantity,amount,currency\nb,2024-01-01,buy,BTC,1,100,EUR\n')
        render_transaction_import(Path(directory), demo=True)
    app = AppTest.from_function(script, args=(str(tmp_path),)).run()
    assert not app.exception
    assert next(c for c in app.selectbox if c.label == 'Holding · BTC').value == 'invented-position'
    assert next(c for c in app.selectbox if c.label == 'Price listing · BTC').value == 'BTC-EUR'
    assert (tmp_path/'holdings.csv').read_bytes() == original


def test_new_crypto_uses_verified_live_pair_in_reporting_currency(tmp_path, monkeypatch):
    from portfolio_app.instruments import Instrument
    from portfolio_app.transaction_import_commands import prepare_positions
    import portfolio_app.transaction_mapping_ui as mapping_ui
    prepare_positions(tmp_path, [dict(position_key='invented-position', id='invented-bitcoin',
        name='Bitcoin', account='Invented wallet', instrument_type='crypto', ticker='BTC-EUR')],
        expected_holdings_revision=None)
    before = (tmp_path/'holdings.csv').read_bytes()
    queries = []
    def fetch(query, cache_dir, demo):
        queries.append(query)
        ticker = 'XYZ-EUR' if query == 'XYZ-EUR' else 'XYZ-USD'
        return [Instrument(ticker, 'Invented token '+ticker[-3:], kind='CRYPTOCURRENCY')], ''
    monkeypatch.setattr(mapping_ui, '_fetch', fetch)
    def script(directory):
        from pathlib import Path
        import streamlit as st
        from portfolio_app.csv_transaction_import_ui import render_transaction_import
        st.session_state['import_tx_content'] = ('invented.csv',
            b'reference,date,type,asset,quantity,amount,currency\nb,2024-01-01,buy,XYZ,1,100,EUR\n')
        render_transaction_import(Path(directory))
    app = AppTest.from_function(script, args=(str(tmp_path),)).run()
    assert not app.exception
    assert set(queries) == {'XYZ', 'XYZ-EUR'}
    assert next(c for c in app.selectbox if c.label == 'Price listing · XYZ').value == 'XYZ-EUR'
    assert next(c for c in app.selectbox if c.label == 'Type · XYZ').value == 'crypto'
    assert next(c for c in app.text_input if c.label == 'Name · XYZ').value == 'Invented token'
    assert (tmp_path/'holdings.csv').read_bytes() == before
