"""Deterministic return processes with analytically known risk characteristics."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from portfolio_app.risk import eur_prices, risk_analytics, weekly_returns
from portfolio_app.risk_data import AdjustedHistory, RiskHistoryService, YahooRiskHistoryProvider

TODAY = date(2026, 9, 27)


def history(returns):
    index = pd.date_range(end='2026-09-25', periods=len(returns) + 1, freq='W-FRI')
    return pd.Series(100 * np.cumprod(np.r_[1., 1 + returns]), index=index)


def held(ids, values, kinds=None):
    return pd.DataFrame(dict(id=ids, name=['Invented ' + key for key in ids], shares=1.,
        ticker=ids, current_value_eur=values, instrument_type=kinds or ['equity'] * len(ids), quote_currency='EUR'))


def market():
    return np.random.default_rng(123).normal(.001, .01, 130)


def test_beta_one_two_and_cash_dilution():
    returns = market()
    bench = history(returns)
    for multiplier in (1., 2.):
        result = risk_analytics(held(['a'], [100]), {'a': history(multiplier * returns)}, bench, today=TODAY)
        assert result.beta == pytest.approx(multiplier)
        assert result.correlation == pytest.approx(1.)
        assert result.volatility == pytest.approx(np.std(returns, ddof=1) * np.sqrt(52) * multiplier)
        assert result.observations == 130
    result = risk_analytics(held(['a', 'cash'], [50, 50], ['equity', 'cash']), {'a': bench}, bench, today=TODAY)
    assert result.beta == pytest.approx(.5)
    assert result.holdings.loc['cash', 'Volatility contribution'] == 0
    assert pd.isna(result.correlations.loc['cash', 'a'])


def test_common_covariance_diversification_and_negative_contributions():
    returns = market()
    bench = history(returns)
    result = risk_analytics(held(['a', 'hedge'], [90, 10]),
                           {'a': bench, 'hedge': history(-returns)}, bench, today=TODAY)
    assert result.beta == pytest.approx(.8)
    assert result.holdings.loc['hedge', 'Volatility contribution'] < 0
    assert result.holdings['Volatility contribution'].sum() == pytest.approx(result.volatility)
    assert result.holdings['Risk share'].sum() == pytest.approx(1.)
    assert result.volatility < result.holdings.loc['a', 'Annual volatility']


def test_partial_coverage_missing_valuation_and_duplicate_accounts():
    bench = history(market())
    frame = held(['a', 'a', 'missing', 'unpriced'], [10, 30, 60, np.nan])
    result = risk_analytics(frame, {'a': bench}, bench, today=TODAY)
    assert result.status == 'partial'
    assert result.covered_value == 40
    assert result.known_value == 100
    assert not result.valuation_complete
    assert len(result.holdings) == 1
    assert result.beta == pytest.approx(1.)
    assert set(result.excluded) == {'missing', 'unpriced'}


def test_missing_weeks_never_bridge_returns_and_incomplete_week_removed():
    series = pd.Series([100., 110., 150., 165.], index=pd.to_datetime(['2026-08-07', '2026-08-14', '2026-08-28', '2026-09-02']))
    result = weekly_returns(series, today=date(2026, 9, 3))
    assert result.loc['2026-08-14'] == pytest.approx(.1)
    assert pd.isna(result.loc['2026-08-21'])
    assert pd.isna(result.loc['2026-08-28'])
    assert result.index.max() == pd.Timestamp('2026-08-28')


def test_short_history_and_zero_benchmark_variance():
    bench = history(market())
    result = risk_analytics(held(['a'], [100]), {'a': bench.tail(50)}, bench, today=TODAY)
    assert result.status == 'unavailable'
    assert result.beta is None
    constant = bench * 0 + 100
    result = risk_analytics(held(['a'], [100]), {'a': bench}, constant, today=TODAY)
    assert result.beta is None and result.correlation is None
    assert result.volatility > 0
    result = risk_analytics(held(['cash'], [100], ['cash']), {}, bench, today=TODAY)
    assert result.volatility == 0 and result.beta == 0
    assert result.correlation is None


def test_intersection_must_have_52_observations():
    bench = history(market())
    first, last = bench.iloc[:70], bench.iloc[-70:]
    result = risk_analytics(held(['a', 'b'], [50, 50]), {'a': first, 'b': last}, bench, today=TODAY)
    assert result.status == 'unavailable'
    assert result.observations < 52
    assert result.beta is None


def test_historical_fx_currency_subunits_and_missing_fx():
    prices = pd.Series([1000., 1200., 1400.], index=pd.date_range('2026-01-01', periods=3))
    fx = pd.Series([1.2, 1.3], index=prices.index[:2])
    result = eur_prices(prices, 'GBp', fx)
    assert result.tolist() == pytest.approx([12., 15.6])
    assert len(result) == 2
    with pytest.raises(ValueError):
        eur_prices(prices, 'USD')
    with pytest.raises(ValueError):
        eur_prices(prices, '')


def test_history_service_uses_adjusted_prices_and_fetches_fx_once(tmp_path):
    calls = []
    class Provider:
        def fetch(self, ticker, years):
            calls.append(ticker)
            return AdjustedHistory(history(market()), 'EUR' if ticker.endswith('EUR=X') else 'USD')
    service = RiskHistoryService(Provider(), tmp_path)
    memo = {}
    first = service.eur('AAA', memo=memo)
    service.eur('BBB', memo=memo)
    service.eur('AAA', memo=memo)
    assert calls == ['AAA', 'USDEUR=X', 'BBB']
    assert first.prices.iloc[0] == 10000
    assert service.eur('AAA').status == 'cached'


def test_yahoo_history_requests_adjustment(monkeypatch):
    import yfinance as yf
    class Ticker:
        history_metadata = {'currency': 'EUR'}
        def history(self, **kwargs):
            assert kwargs['auto_adjust'] is True
            assert kwargs['interval'] == '1d'
            return pd.DataFrame({'Close': [100., 102.]}, index=pd.date_range('2026-01-01', periods=2))
    monkeypatch.setattr(yf, 'Ticker', lambda ticker: Ticker())
    assert YahooRiskHistoryProvider().fetch('INVENTED', 3).prices.tolist() == [100., 102.]
