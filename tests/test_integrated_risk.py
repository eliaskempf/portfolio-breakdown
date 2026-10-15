"""Synthetic common-sample risk, with signed notionals and explicit wealth."""
import pandas as pd
import pytest
from test_risk import held, history, market, TODAY
from portfolio_app.risk import risk_analytics


def calculate(contracts, equity=120., missing=False):
    bench=history(market())
    positions=held(['fund','cash','future:overlay'],[80.,20.,20.],['equity','cash','future'])
    histories={'fund':bench}
    if not missing: histories['future:overlay']=bench
    return risk_analytics(positions,histories,bench,today=TODAY,
        exposures={'future:overlay':50.*contracts},net_equity=equity)


@pytest.mark.parametrize('direction', [1,-1,0])
def test_signed_notional_common_benchmark_and_cash_dilution(direction):
    result=calculate(direction)
    assert result.status=='complete'
    assert result.beta==pytest.approx((80+50*direction)/120)
    assert result.holdings.loc['cash','Beta']==0.
    assert result.holdings.loc['future:overlay','Beta contribution']==pytest.approx(50*direction/120)
    assert result.holdings['Beta contribution'].sum()==pytest.approx(result.beta)
    assert result.holdings['Volatility contribution'].sum()==pytest.approx(result.volatility)


def test_funding_and_liabilities_affect_denominator_not_market_weight():
    assert calculate(1,150.).beta==pytest.approx(130/150)
    assert calculate(1,90.).beta==pytest.approx(130/90)
    for equity in [0.,-1.,None,float('nan')]:
        result=calculate(1,equity)
        assert result.beta is result.volatility is None
        assert result.status=='unavailable'


def test_missing_proxy_shows_partial_estimate_without_renormalizing():
    result=calculate(1,missing=True)
    assert result.beta == pytest.approx(80/120)
    assert result.volatility > 0 and result.correlation == pytest.approx(1.)
    assert result.status=='partial' and 'future:overlay' in result.excluded
    assert 'covered holdings only' in result.note
    assert result.holdings.loc['fund','Weight']==pytest.approx(80/120)
    assert calculate(0,missing=True).beta==pytest.approx(80/120)


@pytest.mark.parametrize('notional', [50., -50.])
def test_short_stock_history_keeps_future_and_debt_weights_in_partial_estimate(notional):
    bench = history(market())
    positions = held(['stock', 'new', 'cash', 'debt', 'future'],
                     [80., 40., 20., -30., 0.], ['equity', 'equity', 'cash', 'liability', 'future'])
    result = risk_analytics(positions, {'stock': bench, 'new': bench.tail(8), 'future': bench},
                           bench, today=TODAY, exposures={'future': notional}, net_equity=110.)
    assert result.status == 'partial' and set(result.excluded) == {'new'}
    assert result.beta == pytest.approx((80 + notional) / 110)
    assert result.holdings.loc['debt', 'Weight'] == pytest.approx(-30/110)
    assert result.holdings.loc['future', 'Weight'] == pytest.approx(notional/110)
    assert result.holdings['Beta contribution'].sum() == pytest.approx(result.beta)
    assert result.holdings['Volatility contribution'].sum() == pytest.approx(result.volatility)


def test_filtered_category_retains_whole_portfolio_denominator_and_negative_capital():
    bench=history(market())
    positions=held(['future:overlay'],[-10.],['future'])
    result=risk_analytics(positions,{'future:overlay':bench},bench,today=TODAY,
                          exposures={'future:overlay':50.},net_equity=90.)
    assert result.beta==pytest.approx(50/90)
    assert result.valuation_complete


def test_proxy_must_share_sample_and_embedded_fund_leverage_is_not_multiplied_again():
    r=market();bench=history(r)
    positions=held(['levered','future:overlay'],[100.,20.],['etf','future'])
    result=risk_analytics(positions,{'levered':history(2*r),'future:overlay':bench},bench,today=TODAY,
                          exposures={'future:overlay':50.},net_equity=120.)
    assert result.beta==pytest.approx(250/120)
    missing=risk_analytics(positions,{'levered':bench.head(70),'future:overlay':bench.tail(70)},bench,today=TODAY,
                          exposures={'future:overlay':50.},net_equity=120.)
    assert missing.status=='unavailable' and missing.beta is None


def test_analytics_loader_includes_proxy_and_preserves_fixed_equity(tmp_path):
    from dataclasses import replace
    from portfolio_app.analytics_service import load_risk
    from portfolio_app.futures import FuturePosition
    from portfolio_app.portfolio_exposure import calculate_portfolio_exposure
    positions=held(['stock'],[100.])
    positions['position_id']='owned:stock'
    positions['reporting_currency']='EUR'
    positions['manual_price']=float('nan')
    future=FuturePosition(entry_price=1.,mark_price=1.,reference_capital_eur=20.)
    result=calculate_portfolio_exposure(positions,future=future)
    risk,status=load_risk(positions,tmp_path,True,'IUSQ.DE',3,future=future,portfolio_result=result)
    assert risk.status=='complete' and 'future:overlay' in risk.holdings.index
    assert risk.holdings.loc['future:overlay','Weight']==pytest.approx(100/120)
    assert status.Note.str.contains('index beta').any()
    short=replace(future,contracts=-1)
    short_risk,_=load_risk(positions,tmp_path,True,'IUSQ.DE',3,future=short,portfolio_result=result)
    assert short_risk.beta < risk.beta
