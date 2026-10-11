"""Cash performance semantics with invented balances and static rates only."""
from decimal import Decimal
import pandas as pd
import pytest
from portfolio_app.performance import position_performance, summarize_performance
from portfolio_app.application import PortfolioInputs, prepare_application
from portfolio_app.positions import read_snapshot
from portfolio_app.actual_portfolio import journal_positions
from portfolio_app.account_journal import Event, append_events
from test_actual_portfolio import invented
from test_accounts_ui import launch_actual
from test_ui import by_label, activate


def cash(currency='EUR', report='EUR', cost=float('nan'), cost_currency=''):
    return pd.DataFrame([dict(id='cash',position_id='cash',name='Invented cash',instrument_type='cash',
        shares=100.,quote_currency=currency,current_price=1.,current_value_reporting=100.,
        acquisition_price=cost,acquisition_currency=cost_currency,reporting_currency=report)])


@pytest.mark.parametrize('currency',['EUR','USD','GBP'])
def test_cash_without_basis_is_not_missing_investment_cost(currency):
    original=cash(currency,currency)
    valued=position_performance(original,reporting_currency=currency)
    summary=summarize_performance(valued)
    assert summary.cash_only and summary.cash_balance_count==1 and summary.eligible_count==0
    assert summary.gain_reporting is None and summary.return_pct is None
    assert 'not applicable' in valued.performance_note.iloc[0]
    assert valued.current_value_reporting.iloc[0]==100
    pd.testing.assert_frame_equal(original,cash(currency,currency))


def test_foreign_cash_does_not_invent_zero_fx_gain_or_discard_recorded_cost():
    valued=position_performance(cash('USD'))
    assert 'FX gain needs' in valued.performance_note.iloc[0]
    assert summarize_performance(valued).cash_only
    recorded=position_performance(cash('USD',cost=.8,cost_currency='EUR'))
    assert summarize_performance(recorded).gain_reporting==20
    missing_conversion=position_performance(cash('USD',cost=1,cost_currency='USD'))
    assert summarize_performance(missing_conversion).eligible_count==1
    assert summarize_performance(missing_conversion).covered_count==0


def test_cash_does_not_dilute_investment_return_or_reduce_eligible_coverage():
    rows=pd.concat([cash(),cash(cost=.8,cost_currency='EUR').assign(instrument_type='equity',id='stock',position_id='stock')],ignore_index=True)
    valued=position_performance(rows)
    summary=summarize_performance(valued)
    assert (summary.held_count,summary.eligible_count,summary.covered_count)==(2,1,1)
    assert summary.gain_reporting==20 and summary.return_pct==25
    assert valued.current_value_reporting.sum()==200


def test_normal_app_cash_has_no_buy_in_warning_and_no_invented_gain(tmp_path):
    service,journal,_=invented(tmp_path)
    snapshot=read_snapshot(tmp_path/'holdings.csv')
    prepared=prepare_application(PortfolioInputs(snapshot,None,{},[]),service)
    assert prepared.status.missing_costs==0 and prepared.status.problems.empty
    app=launch_actual(tmp_path)
    assert not app.exception
    assert by_label(app.metric,'Current value').delta=='N/A · Cash balance'
    assert not any(w.label=='Complete buy-ins' for w in app.button)
    activate(app,'Overview')
    by_label(app.get('button_group'),'Overview view').set_value('Performance').run()
    assert not app.exception
    assert any('N/A' in w.value and 'cash balance' in w.value for w in app.info)
    assert not any('Add convertible buy-ins' in w.value for w in app.info)
    settled=append_events(journal,[Event('profit','2026-01-03','settlement',account_id='a',position_id='p',price=1010)])
    projected=journal_positions(prepared.valued,settled,service)
    row=projected.loc[projected.instrument_type.eq('cash')]
    assert Decimal(str(row.current_value_reporting.iloc[0]))==Decimal('20192')
    assert summarize_performance(row).cash_only
    assert 'Cash balance' in row.performance_note.iloc[0]


def test_missing_cash_fx_remains_a_valuation_problem(tmp_path):
    from portfolio_app.prices import PriceService, UnavailableProvider
    invented(tmp_path)
    snapshot=read_snapshot(tmp_path/'holdings.csv')
    prepared=prepare_application(PortfolioInputs(snapshot,None,{},[]),PriceService(UnavailableProvider()),reporting_currency='USD')
    assert prepared.status.missing_costs==0
    assert prepared.status.missing_prices==1
    assert prepared.status.problems.name.tolist()==['Invented cash']
