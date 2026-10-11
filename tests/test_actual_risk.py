"""Synthetic currency paths and native variation; no market or working data."""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from portfolio_app.analytics_service import load_risk
from portfolio_app.leverage_settings import FundFactor, LeverageSettings, save_leverage_settings
from portfolio_app.risk import native_future_weekly_returns, risk_analytics, weekly_returns
from portfolio_app.risk_data import AdjustedHistory
from portfolio_app.study_market import QualifiedHistory
from test_risk import history, market, TODAY


def row(identity, kind, value, currency='USD', **kwargs):
    return dict(id=identity, position_id=identity, name='Invented ' + identity, shares=1., ticker='',
                current_value_reporting=value, quote_currency=currency, instrument_type=kind,
                reporting_currency='EUR', manual_price=float('nan'), **kwargs)


def future(value=0., notional=200., currency='USD', product='FMWO', **kwargs):
    return row('future:synthetic', 'future', value, currency, signed_notional_reporting=notional,
               current_fx_rate=1., product=product, contract_id=f'EUREX:{product}:2026-12-18',
               return_convention='net' if product.startswith('FMW') else 'price', **kwargs)


class SyntheticService:
    def __init__(self, benchmark, *, proxy=None, fx=None):
        self.benchmark, self.proxy, self.fx = benchmark, proxy, fx
        self.calls = []

    def in_currency(self, ticker, target, years, **kwargs):
        self.calls.append((ticker, target))
        return AdjustedHistory(self.benchmark if ticker == 'BENCH' else self.proxy, target)

    def cash(self, source, target, years, **kwargs):
        self.calls.append(('FX', source, target))
        return AdjustedHistory(self.fx if self.fx is not None else pd.Series(dtype=float), target)


def actual(rows, target='EUR'):
    frame = pd.DataFrame(rows)
    frame['reporting_currency'] = target
    frame.attrs.update(actual_accounts=True, reporting_currency=target)
    return frame


def qualified(prices, currency='USD', benchmark='MSCI World', convention='net'):
    return {'future:synthetic': QualifiedHistory(prices, benchmark, currency, convention, 'Invented proxy')}


def test_unchanged_native_future_has_zero_market_return_despite_fx_moves():
    fx = history(market())
    native = fx * 0 + 100
    returns = native_future_weekly_returns(native, fx=fx, current_fx=1.2, today=TODAY)
    assert returns.dropna().eq(0).all()
    assert weekly_returns(native * fx, today=TODAY).dropna().ne(0).any()


def test_native_variation_uses_historical_fx_without_adding_full_notional_fx_returns():
    native = history(market())
    fx = pd.Series(np.linspace(.8, 1.2, len(native)), index=native.index)
    result = native_future_weekly_returns(native, fx=fx, current_fx=1.1, today=TODAY)
    expected = weekly_returns(native, today=TODAY) * fx / 1.1
    pd.testing.assert_series_equal(result, expected)
    broken = fx.drop(fx.index[-2])
    assert pd.isna(native_future_weekly_returns(native, fx=broken, current_fx=1., today=TODAY).iloc[-2])


def test_cash_debt_and_unsettled_claim_fx_count_once_and_full_notional_has_none(tmp_path):
    fx = history(market())
    flat = fx * 0 + 100
    positions = actual([row('cash', 'cash', 100), row('loan', 'liability', -40), future(10)])
    service = SyntheticService(fx, fx=fx)
    result, _ = load_risk(positions, tmp_path, False, 'BENCH', 3, service=service, today=TODAY,
        portfolio_result=SimpleNamespace(net_equity=70.), qualified_histories=qualified(flat))
    assert result.status == 'complete'
    assert result.beta == pytest.approx(1.)
    assert result.holdings.loc['future:synthetic', 'Beta contribution'] == 0
    assert result.holdings.loc['loan', 'Weight'] == pytest.approx(-40/70)
    assert result.holdings.loc['risk-claim:future:synthetic', 'Weight'] == pytest.approx(10/70)
    assert result.holdings['Beta contribution'].sum() == pytest.approx(result.beta)
    assert 'risk-claim:future:synthetic' not in set(positions.id)


@pytest.mark.parametrize(('currency', 'product'), [('EUR', 'FMWN'), ('USD', 'FMWO'), ('GBP', 'FMWB')])
@pytest.mark.parametrize('notional', [200., -200.])
def test_all_reporting_currencies_signed_notional_and_same_currency_debt(currency, product, notional, tmp_path):
    prices = history(market())
    positions = actual([row('cash', 'cash', 120, currency), row('loan', 'liability', -20, currency),
                        future(notional=notional, currency=currency, product=product)], target=currency)
    result, _ = load_risk(positions, tmp_path, False, 'BENCH', 3, service=SyntheticService(prices), today=TODAY,
        portfolio_result=SimpleNamespace(net_equity=100.), qualified_histories=qualified(prices, currency))
    assert result.status == 'complete' and result.beta == pytest.approx(notional/100)
    assert result.holdings.loc['loan', 'Beta contribution'] == 0


def test_missing_qualified_history_or_fx_withholds_aggregate_without_renormalizing(tmp_path):
    prices = history(market())
    positions = actual([row('cash', 'cash', 100, 'EUR'), future()])
    for kwargs in ({}, {'qualified_histories': qualified(prices)}):
        result, _ = load_risk(positions, tmp_path, False, 'BENCH', 3, service=SyntheticService(prices), today=TODAY,
            portfolio_result=SimpleNamespace(net_equity=100.), **kwargs)
        assert result.status == 'partial' and result.beta is result.volatility is None
        assert result.holdings.loc['cash', 'Weight'] == 1.
        assert 'future:synthetic' in result.excluded


def test_multiple_contract_currencies_keep_separate_signed_weights(tmp_path):
    prices = history(market())
    sterling = dict(future(notional=-50., currency='GBP', product='FMWB'), id='future:gbp', position_id='future:gbp')
    positions = actual([row('cash', 'cash', 100, 'EUR'), future(), sterling])
    supplied = qualified(prices)
    supplied['future:gbp'] = QualifiedHistory(prices, 'MSCI World', 'GBP', 'net', 'Invented sterling proxy')
    result, _ = load_risk(positions, tmp_path, False, 'BENCH', 3,
        service=SyntheticService(prices, fx=prices * 0 + 1), today=TODAY,
        portfolio_result=SimpleNamespace(net_equity=100.), qualified_histories=supplied)
    assert result.status == 'complete' and result.beta == pytest.approx(1.5)
    assert result.holdings.loc['future:synthetic', 'Weight'] == 2
    assert result.holdings.loc['future:gbp', 'Weight'] == -.5


def test_linked_msci_proxy_stays_contract_native_and_leveraged_proxy_is_rejected(tmp_path):
    prices = history(market())
    (tmp_path / 'holdings.csv').write_text('id,name,shares,instrument_type,position_key,ticker\n'
        'proxy,Invented proxy,1,etf,proxy,SYNTH\n')
    positions = actual([row('cash', 'cash', 100, 'EUR'), future(proxy_position_id='proxy')])
    service = SyntheticService(prices, proxy=prices, fx=prices * 0 + 1)
    result, status = load_risk(positions, tmp_path, False, 'BENCH', 3, service=service, today=TODAY,
                              portfolio_result=SimpleNamespace(net_equity=100.))
    assert result.status == 'complete'
    assert ('SYNTH', 'USD') in service.calls and ('SYNTH', 'EUR') not in service.calls
    assert status.Note.str.contains('approximate MSCI').any()
    save_leverage_settings(tmp_path, LeverageSettings(fund_factors=(FundFactor('proxy', None),)), None)
    blocked, _ = load_risk(positions, tmp_path, False, 'BENCH', 3, service=service, today=TODAY,
                          portfolio_result=SimpleNamespace(net_equity=100.))
    assert blocked.status == 'partial' and blocked.beta is None


def test_price_index_does_not_inherit_msci_proxy_but_explicit_matching_history_works(tmp_path):
    prices = history(market())
    positions = actual([row('cash', 'cash', 100, 'EUR'), future(currency='EUR', product='FESX')])
    for convention, expected in [('net', 'partial'), ('price', 'complete')]:
        result, _ = load_risk(positions, tmp_path, False, 'BENCH', 3, service=SyntheticService(prices), today=TODAY,
            portfolio_result=SimpleNamespace(net_equity=100.),
            qualified_histories=qualified(prices, 'EUR', 'EURO STOXX 50', convention))
        assert result.status == expected


def test_embedded_etf_history_weights_remain_funded_values_in_actual_accounts(tmp_path):
    prices = history(market())
    leveraged = history(2 * market())
    positions = actual([dict(row('fund', 'etf', 100, 'GBP'), ticker='SYNTH'),
                        row('loan', 'liability', -20, 'GBP')], target='GBP')
    result, _ = load_risk(positions, tmp_path, False, 'BENCH', 3,
        service=SyntheticService(prices, proxy=leveraged), today=TODAY,
        portfolio_result=SimpleNamespace(net_equity=80.))
    assert result.status == 'complete' and result.beta == pytest.approx(2.5)
    assert result.holdings.loc['fund', 'Weight'] == 1.25


def test_explicit_weekly_risk_returns_do_not_require_fake_positive_price_series():
    prices = history(market())
    positions = pd.DataFrame([row('future', 'future', 0)])
    explicit = pd.Series(-2., index=prices.index)  # A futures loss may exceed allocated capital.
    result = risk_analytics(positions, {}, prices, today=TODAY, exposures={'future': 100},
                            net_equity=50, return_series={'future': explicit})
    assert result.status == 'complete'
    assert result.volatility == pytest.approx(0.)
