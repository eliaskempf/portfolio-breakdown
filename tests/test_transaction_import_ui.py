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
    income = table[table.Transaction == 'In-kind income'].iloc[0]
    assert income['Trade value'] == '—' and '25' in income['Monetary valuation']
    related = app.dataframe[0].value
    assert set(related.Transaction) == {'Swap received'}
    assert related.iloc[0]['Monetary valuation'] == '80.00 EUR'
    next(b for b in app.button if b.label == 'Review against portfolio').click().run()
    assert not app.exception
    assert any('import portfolio' in c.value.lower() for c in app.caption)


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
    assert 'estimated' in table.iloc[0]['Acquisition cost']
    assert 'Observation date unknown' in table.iloc[0]['Evidence']
    assert any('imported movements' in c.value for c in app.caption)
