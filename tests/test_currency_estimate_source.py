"""Reviewed currency estimates and the first ordinary trade share the exact CSV opening."""
from decimal import Decimal as D
import json

import pytest
from streamlit.testing.v1 import AppTest

from portfolio_app.ledger.account_journal import load_journal
from portfolio_app.ledger.security_history import SecurityEvent
from portfolio_app.performance.cost_basis import FIELD, active_components, encode_components, estimate_missing
from portfolio_app.portfolio.balances import patch_holdings
from portfolio_app.portfolio.holdings import DataError
from portfolio_app.portfolio.portfolio import prepare_portfolio
from portfolio_app.portfolio.positions import read_snapshot
from test_currency import InventedPrices, NOW, PriceService
from test_trade_commands import save
from test_ui import by_label


@pytest.fixture
def path(tmp_path):
    path = tmp_path / 'holdings.csv'
    path.write_text('id,name,ticker,position_key,shares,acquisition_price,acquisition_currency\n'
                    'test,Invented,EXAMPLE,test,2.00000000000000001,10.00000000000000002,USD\n')
    return path


def previous_estimate(path):
    snapshot = read_snapshot(path)
    prices = PriceService(InventedPrices(), now=lambda: NOW)
    row = prepare_portfolio(snapshot.holdings, prices, reporting_currency='EUR').iloc[0]
    parts = estimate_missing(active_components(row), 'EUR', prices)
    return json.loads(encode_components(parts, row))


def first_sale(path):
    event = SecurityEvent('sale', 'sell', '2024-02-02', quantity=D(1), price=D(18),
                          currency='USD', charges_known=True)
    save(path.parent, event, position_id='test', opening_date='2024-02-01')
    opening, _ = load_journal(path.parent)[0].securities[0].records
    assert opening.quantity == D('2.00000000000000001') and not opening.lots
    part, = opening.costs
    assert D(part['shares']) == opening.quantity
    assert D(part['amount']) == D('20.0000000000000001400000000000000002')
    return part


def test_currency_review_writes_exact_units_and_cost_before_first_sale(path):
    app = AppTest.from_string(f"""from pathlib import Path
import streamlit as st
from test_currency import InventedPrices, NOW, PriceService
from portfolio_app.interface.views.workspace.currency_ui import render_currency_dialog
from portfolio_app.portfolio.portfolio_settings import load_settings
from portfolio_app.portfolio.positions import read_snapshot
path = Path({str(path)!r})
settings = load_settings(path.parent)
if settings.reporting_currency != 'GBP':
    st.session_state['currency_change_requested'] = 'GBP'
    render_currency_dialog(path.parent, read_snapshot(path), settings, PriceService(InventedPrices(), now=lambda: NOW), None)
""").run()
    app.multiselect(key='currency_estimate_selection').set_value(['test']).run()
    by_label(app.button, 'Review FX estimates').click().run()
    by_label(app.checkbox, 'Use these FX estimates for the selected positions').check().run()
    by_label(app.button, 'Apply currency change').click().run()
    assert not app.error and not app.exception
    saved = json.loads(read_snapshot(path).holdings.iloc[0][FIELD])
    part = first_sale(path)
    assert saved['source_exact'] and saved['components'] == [part]
    assert part['conversions']['GBP']['method'] == 'estimate'


def test_previous_exact_marked_estimate_recovers_opening_with_confirmed_conversion(path):
    document = previous_estimate(path)
    assert document['source_exact'] and D(document['components'][0]['shares']) == 2
    patch_holdings(path, {'test': {FIELD: json.dumps(document)}}, expected_revision=read_snapshot(path).revision)
    before = path.read_bytes()
    part = first_sale(path)
    assert part['conversions'] == document['components'][0]['conversions']
    assert path.read_bytes() == before


@pytest.mark.parametrize('alter', ['native_cost', 'target_amount', 'method', 'date', 'shares', 'rate', 'observed_on'])
def test_nonmatching_estimate_is_not_reinterpreted(path, alter):
    document = previous_estimate(path)
    part = document['components'][0]
    fx = part['conversions']['EUR']
    if alter == 'native_cost':
        part['amount'] = '21'
        fx['amount'] = str(D(21) * D(fx['rate']))
    elif alter == 'date':
        part['date'] = '2023-01-02'
    elif alter == 'shares':
        part['shares'] = '2.00000000000000002'
    else:
        fx.update({'target_amount': {'amount': '15'}, 'method': {'method': 'supplied_rate'},
                   'rate': {'rate': '0.7'}, 'observed_on': {'observed_on': ''}}[alter])
    patch_holdings(path, {'test': {FIELD: json.dumps(document)}}, expected_revision=read_snapshot(path).revision)
    assert active_components(read_snapshot(path).holdings.iloc[0]) == [part]
    with pytest.raises(DataError, match='cost quantities'):
        first_sale(path)
