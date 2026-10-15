"""Synthetic histories only; chart references never alter account records."""
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from portfolio_app.derivative_market import CATALOG, make_contract
from portfolio_app.futures_chart_reference import reference_for
from portfolio_app.history import HistoryResult
from test_ui import by_label


@pytest.mark.parametrize('product', list(CATALOG))
def test_catalog_references_match_contract_currency(product):
    contract = make_contract(product, '2027-03-19')
    reference = reference_for(contract)
    assert reference.currency == contract.currency
    assert reference.index_points
    assert reference_for(replace(contract, exchange='CUSTOM')) is None
    assert reference_for(replace(contract, return_convention='unknown')) is None
    assert reference_for(replace(contract, currency='USD' if contract.currency != 'USD' else 'GBP')) is None


def start_chart(tmp_path, monkeypatch, *, product='FMWN', currency='EUR', status='fresh', supplied_currency=None):
    calls=[]
    def get(ticker, period, **kwargs):
        calls.append((ticker, period, kwargs))
        denomination = ('EUR' if ticker.endswith('EUR=X') else
                        supplied_currency or (ticker.rsplit(':',1)[-1] if ticker.startswith('MSCI:') else {'^FTSE':'GBP'}.get(ticker,'EUR')))
        values = (.9, .91) if ticker == 'USDEUR=X' else (1.2, 1.21) if ticker == 'GBPEUR=X' else (100., 110.)
        return HistoryResult(('2026-01-02','2026-01-03'), values, denomination, status,
                             '2026-01-04', 'Synthetic saved history' if status=='stale' else '')
    monkeypatch.setattr('portfolio_app.market_history_ui.history_for',
                        lambda _: SimpleNamespace(get=get, pending=lambda *args: False))
    app=AppTest.from_string(f'''
from pathlib import Path
from dataclasses import replace
import streamlit as st
from portfolio_app.derivative_market import make_contract
from portfolio_app.futures_chart_ui import render_futures_chart
st.session_state['reporting_currency']={currency!r}
st.session_state['position_edit_dialog']=True
contract=make_contract({product!r},'2027-03-19')
render_futures_chart(Path({str(tmp_path)!r}), [contract, replace(contract,expiry='2027-06-18')], key='chart_')
''').run()
    assert not app.exception
    return app,calls


def figure(app):
    return json.loads(app.get('plotly_chart')[0].proto.spec)


@pytest.mark.parametrize('product,currency', [('FMWN','EUR'),('FMWO','USD'),('FMWB','GBP')])
def test_msci_uses_direct_net_index_in_contract_currency(tmp_path, monkeypatch, product, currency):
    app,calls=start_chart(tmp_path,monkeypatch,product=product,currency='EUR')
    symbol=f'MSCI:990100:NETR:{currency}'
    assert figure(app)['layout']['yaxis']['title']['text']=='Index points'
    assert {c[0] for c in calls}=={symbol}
    assert any('Source: MSCI' in item.value and currency in item.value for item in app.caption)
    assert any('Net total return' in item.value for item in app.caption)
    assert not any('ETF' in item.value for item in app.caption)
    by_label(app.get('button_group'),'Period').set_value('5Y').run()
    assert calls[-1][:2]==(symbol,'5Y')
    assert not app.exception


def test_index_keeps_points_and_return_convention_without_fx_conversion(tmp_path, monkeypatch):
    app,calls=start_chart(tmp_path,monkeypatch,product='FDAX',currency='USD')
    assert figure(app)['layout']['yaxis']['title']['text']=='Index points'
    assert {c[0] for c in calls}=={'^GDAXI'}
    assert not any(control.label=='History currency' for control in app.get('button_group'))
    assert any('Gross total return' in item.value for item in app.caption)
    assert not app.selectbox # Rolled expiries use the same reference.


def test_stale_chart_and_retry_are_visible(tmp_path, monkeypatch):
    app,calls=start_chart(tmp_path,monkeypatch,status='stale')
    assert len(app.get('plotly_chart'))==1
    assert any('Synthetic saved history' in warning.value for warning in app.warning)
    by_label(app.button,'Retry history').click().run()
    assert any(kwargs.get('refresh') for _,_,kwargs in calls)


def test_wrong_source_currency_is_not_silently_relabelled(tmp_path, monkeypatch):
    app,_=start_chart(tmp_path,monkeypatch,supplied_currency='USD')
    assert not app.get('plotly_chart')
    assert any('unexpected currency' in item.value for item in app.info)


def test_unknown_contract_requires_explicit_reference_and_preserves_journal(tmp_path, monkeypatch):
    from portfolio_app.account_journal import load_journal
    from test_investment_dialog import start
    calls=[]
    monkeypatch.setattr('portfolio_app.market_history_ui.history_for', lambda _: SimpleNamespace(
        get=lambda ticker,period,**kwargs: (calls.append(ticker) or HistoryResult(
            ('2026-01-02','2026-01-03'),(100.,101.),'EUR','fresh','2026-01-04')),
        pending=lambda *args:False))
    app,journal=start(tmp_path)
    assert not calls
    assert by_label(app.selectbox,'Chart reference').value is None
    by_label(app.selectbox,'Chart reference').set_value('MSCI:990100:NETR:EUR').run()
    assert calls==['MSCI:990100:NETR:EUR']
    assert len(app.get('plotly_chart')) >= 1
    assert any('match to this contract' in caption.value for caption in app.caption)
    by_label(app.get('button_group'),'Futures position view').set_value('Settlements').run()
    by_label(app.get('button_group'),'Futures position view').set_value('Chart').run()
    assert by_label(app.selectbox,'Chart reference').value=='MSCI:990100:NETR:EUR'
    assert load_journal(tmp_path)[0]==journal
    assert not app.exception


def test_different_contract_markets_are_not_spliced(tmp_path, monkeypatch):
    monkeypatch.setattr('portfolio_app.market_history_ui.history_for',lambda _:SimpleNamespace(
        get=lambda *a,**kw:HistoryResult(note='Synthetic unavailable history'),pending=lambda *a:False))
    app=AppTest.from_string(f'''
from pathlib import Path
from portfolio_app.derivative_market import make_contract
from portfolio_app.futures_chart_ui import render_futures_chart
render_futures_chart(Path({str(tmp_path)!r}), [make_contract(code,'2027-03-19') for code in ['FDAX','FTUK']], key='mixed_')
''').run()
    assert not app.exception
    assert len(by_label(app.selectbox,'Contract market').options)==2
    assert not app.get('plotly_chart')
    assert any('unavailable' in item.value for item in app.info)
    assert by_label(app.button,'Retry history')
