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
    table=app.dataframe[0].value
    assert set(table.Transaction)=={'In-kind income','Asset fee','Swap received'}
    income=table[table.Transaction=='In-kind income'].iloc[0]
    assert income['Trade value']=='—'
    assert '25' in income['Monetary valuation']
    assert table.loc[table.Transaction=='Swap received','Monetary valuation'].iloc[0]=='80.00 EUR'
    assert table.loc[table.Transaction=='Asset fee','Monetary valuation'].iloc[0]=='—'
    edit=next(b for b in app.button if b.label=='Edit transaction')
    assert edit.disabled
    next(b for b in app.button if b.label=='Review against portfolio').click().run()
    assert not app.exception
    assert any(b.label=='Confirm complete history' for b in app.button)
