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
    assert any(list(frame.value.get('Reference', [])) == ['a','b'] for frame in app.dataframe)
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
    assert app.dataframe[0].value.Reference.tolist() == ['x']
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
