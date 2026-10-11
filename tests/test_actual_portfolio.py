"""Invented account integration examples; never read working portfolio data."""
from dataclasses import replace
from decimal import Decimal
import json

import pandas as pd
import pytest

from portfolio_app.account_journal import Account, CashLink, Event, Journal, PositionSpec, append_events, save_journal
from portfolio_app.derivative_types import ContractSpec, PriceObservation
from portfolio_app.actual_portfolio import journal_positions, freeze_position_keys
from portfolio_app.holdings import DataError, parse_holdings
from portfolio_app.portfolio import prepare_portfolio
from portfolio_app.prices import PriceService, StaticProvider
from portfolio_app.portfolio_exposure import calculate_portfolio_exposure
from portfolio_app.positions import read_snapshot, save_position


def invented(tmp_path):
    path = tmp_path / 'holdings.csv'
    path.write_text('id,name,ticker,shares,instrument_type,exposure_kind,manual_price,manual_price_currency,manual_price_date,account,quantity_unit\ncash,Invented cash,,20000,cash,non_equity,1,EUR,2026-01-02,Invented account,units\n')
    quotes = tmp_path / 'demo_prices.json'
    quotes.write_text(json.dumps({'prices':{},'fx':{'USD':{'price':.9,'currency':'EUR','observed_at':'2026-01-02T12:00:00+00:00'},'GBP':{'price':1.2,'currency':'EUR','observed_at':'2026-01-02T12:00:00+00:00'}}}))
    service = PriceService(StaticProvider(quotes))
    contract = ContractSpec('SYNTH','2027-03-19','EUR',10,1)
    journal = Journal(accounts=(Account('a','Invented account'),),contracts=(contract,),positions=(PositionSpec('p','a',contract.id),),
        cash_links=(CashLink('a','EUR','position-0'),))
    journal = append_events(journal, (Event('cash','2026-01-02','cash_open',account_id='a',currency='EUR',amount=20000),
        Event('open','2026-01-02','trade',account_id='a',position_id='p',quantity=2,price=1000),
        Event('fee','2026-01-02','fee',account_id='a',currency='EUR',position_id='p',amount=8)))
    return service,journal,contract


@pytest.mark.parametrize('currency,rate',[('EUR',1),('USD',1/.9),('GBP',1/1.2)])
def test_adoption_mark_settlement_conserve_equity(tmp_path,currency,rate):
    service,journal,contract = invented(tmp_path)
    valued = prepare_portfolio(read_snapshot(tmp_path/'holdings.csv').holdings,service,reporting_currency=currency)
    original=valued.copy(deep=True)
    marks={'p':PriceObservation(contract.id,'2026-01-03',Decimal('1010'))}
    projected=journal_positions(valued,journal,service,reporting_currency=currency,marks=marks)
    assert projected.current_value_reporting.sum()==pytest.approx(20192*rate)
    assert len(projected)==2
    exposed=calculate_portfolio_exposure(projected,reporting_currency=currency)
    assert exposed.net_equity==pytest.approx(20192*rate)
    assert exposed.net_exposure==pytest.approx(20200*rate)
    assert exposed.reporting_currency==currency
    if currency != 'EUR':
        assert 'exposure_eur' not in exposed.exposures
    settled=append_events(journal,[Event('settle','2026-01-03','settlement',account_id='a',position_id='p',price=1010)])
    after=journal_positions(valued,settled,service,reporting_currency=currency,marks=marks)
    assert after.current_value_reporting.sum()==pytest.approx(projected.current_value_reporting.sum())
    pd.testing.assert_frame_equal(valued,original)


def test_identity_adoption_blocks_old_cash_editor_and_survives_restart(tmp_path):
    service,journal,_=invented(tmp_path)
    before=read_snapshot(tmp_path/'holdings.csv')
    frozen=freeze_position_keys(tmp_path,before.revision)
    assert frozen.holdings.position_id.tolist()==before.holdings.position_id.tolist()
    assert frozen.holdings.shares.tolist()==before.holdings.shares.tolist()
    save_journal(tmp_path,journal,None)
    with pytest.raises(DataError,match='Accounts'):
        save_position(tmp_path/'holdings.csv',{'shares':'21000'},expected_revision=frozen.revision,position_id='position-0')
    from portfolio_app.workspace import validate_workspace
    validate_workspace(tmp_path)


def test_debt_and_pending_settlement_once(tmp_path):
    service,journal,_=invented(tmp_path)
    journal=append_events(journal,[Event('loan','2026-01-02','borrow',account_id='a',currency='EUR',amount=3000,reference='loan'),
        Event('settle','2026-01-03','settlement',account_id='a',position_id='p',price=1010,value_date='2026-01-04')])
    valued=prepare_portfolio(read_snapshot(tmp_path/'holdings.csv').holdings,service)
    projected=journal_positions(valued,journal,service)
    result=calculate_portfolio_exposure(projected,reporting_currency='EUR')
    assert result.net_equity==20192
    assert result.liabilities==3000
    assert result.owned_assets==23192


def test_short_overdraft_and_missing_fx(tmp_path):
    service,journal,_=invented(tmp_path)
    journal=append_events(journal,[Event('loss','2026-01-03','cash_adjust',account_id='a',currency='EUR',amount=-21000)])
    valued=prepare_portfolio(read_snapshot(tmp_path/'holdings.csv').holdings,service)
    result=calculate_portfolio_exposure(journal_positions(valued,journal,service),reporting_currency='EUR')
    assert result.net_equity==-1008 and result.liabilities==1008
    assert result.net_multiple is None
