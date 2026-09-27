"""Hand-calculated invented portfolios; no working data or market access."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from portfolio_app.analytics import snapshot_analytics
from portfolio_app.analytics_cache import AnalyticsCache
from portfolio_app.fundamentals import (Fundamentals, FundamentalsService, FundFee, Metric, apply_fund_metadata,
    normalize_info, load_fee_overrides, save_fee_override)
from portfolio_app.holdings import DataError


def positions():
    return pd.DataFrame(dict(id=['a', 'a', 'b', 'fund'], name=['Invented A', 'Invented A', 'Invented B', 'Invented fund'],
        ticker=['AAA', 'AAA', 'BBB', 'FFF'], shares=[1., 1., 1., 1.],
        instrument_type=['equity', 'equity', 'equity', 'etf'], current_value_eur=[100., 100., 200., 100.]))


def snapshots():
    return {'a': Fundamentals('AAA', 'equity', {'trailing_pe': Metric(10.), 'forward_pe': Metric(8.), 'distribution_yield': Metric(.02)}),
            'b': Fundamentals('BBB', 'equity', {'trailing_pe': Metric(20.), 'forward_pe': Metric(-5.)}),
            'fund': Fundamentals('FFF', 'etf', {'fee': Metric(.005), 'distribution_yield': Metric(0.)})}


def test_harmonic_pe_costs_income_and_merged_concentration():
    original = positions()
    result = snapshot_analytics(original, snapshots())
    assert result.largest_weight == .4
    assert result.top_five_weight == 1.
    assert result.effective_holdings == pytest.approx(1 / (.4 ** 2 + .4 ** 2 + .2 ** 2))
    assert result.metrics['trailing_pe'].value == pytest.approx(400 / (200 / 10 + 200 / 20))
    assert result.metrics['forward_pe'].value == 8
    assert result.metrics['forward_pe'].covered_value == 200
    assert result.metrics['forward_pe'].excluded_count == 1
    assert result.metrics['fee_eur'].value == .5
    assert result.metrics['distribution_yield_eur'].value == 4.
    assert result.metrics['distribution_yield'].covered_value == 300
    assert result.metrics['distribution_yield'].value == pytest.approx(4 / 300)
    pd.testing.assert_frame_equal(original, positions())


def test_unknown_valuation_and_metric_are_not_zero():
    frame = positions()
    frame.loc[2, 'current_value_eur'] = float('nan')
    data = snapshots()
    data['fund'] = Fundamentals('FFF', 'etf')
    result = snapshot_analytics(frame, data)
    assert not result.valuation_complete
    assert result.missing_valuations == 1
    assert result.known_value == 300
    assert result.metrics['fee_eur'].value is None
    assert result.metrics['fee'].excluded_count == 1
    assert result.metrics['trailing_pe'].value == 10
    assert snapshot_analytics(frame.iloc[:0], {}).effective_holdings is None
    assert snapshot_analytics(frame.assign(shares=0), {}).known_value == 0
    absent = snapshot_analytics(positions(), {'a': snapshots()['a']})
    assert absent.metrics['trailing_pe'].eligible_value == 400
    assert absent.metrics['trailing_pe'].covered_value == 200
    assert absent.metrics['fee'].eligible_value == 100


def test_normalization_preserves_percent_units_missing_and_losses():
    result = normalize_info('AAA', dict(quoteType='EQUITY', currency='USD', trailingPE=-10,
        trailingAnnualDividendYield=.015, dividendYield=99, profitMargins=-.2, revenueGrowth=float('inf'), marketCap=1e6))
    assert result.metrics['distribution_yield'].value == .015
    assert result.metrics['profit_margin'].value == -.2
    assert result.metrics['revenue_growth'].value is None
    assert result.metrics['market_cap'].unit == 'USD'
    assert result.metrics['trailing_pe'].value == -10
    assert normalize_info('X', {'quoteType': 'CRYPTOCURRENCY', 'trailingPE': 30}).metrics == {}
    assert normalize_info('X', {'quoteType': 'EQUITY', 'trailingAnnualDividendYield': 0}).metrics['distribution_yield'].value == 0
    assert normalize_info('X', {'quoteType': 'EQUITY', 'dividendYield': 5}).metrics['distribution_yield'].value is None


def test_fund_statistics_do_not_become_stock_ratios():
    result = normalize_info('FFF', dict(quoteType='ETF', trailingPE=100, currency='EUR', totalAssets=1000),
                            {'fee': .002, 'fund_pe': 22, 'fund_pb': 3})
    assert 'trailing_pe' not in result.metrics
    assert result.metrics['fund_pe'].value == 22
    assert result.metrics['fee'].value == .002
    assert result.metrics['fund_assets'].value is None  # Listing currency is not fund-assets currency.
    result = normalize_info('FFF', dict(quoteType='ETF', currency='EUR', fundCurrency='USD', totalAssets=1000))
    assert result.metrics['fund_assets'].value == 1000
    assert result.metrics['fund_assets'].unit == 'USD'


def test_fee_identity_override_precedence_and_income_policy(tmp_path):
    snapshot = Fundamentals('FFF', 'etf', {'fee': Metric(.05), 'distribution_yield': Metric(.08)})
    # Invented identity: an unrelated ticker must not borrow a public fund's fee.
    row = dict(isin='XX0000000000', ticker='VVSM.DE', instrument_type='etf')
    assert apply_fund_metadata(snapshot, row, {}).metrics['fee'].value == .05
    key = 'isin:XX0000000000'
    fee = FundFee(.003, 'Invented verified fee source', '2020-01-01', accumulating=True)
    path = tmp_path / 'fund-fees.json'
    save_fee_override(path, key, fee)
    loaded = load_fee_overrides(path)
    result = apply_fund_metadata(snapshot, row, loaded)
    assert result.metrics['fee'].value == .003
    assert 'older than 90 days' in result.metrics['fee'].note
    assert result.metrics['distribution_yield'].value == 0
    save_fee_override(path, key, None)
    assert load_fee_overrides(path) == {}
    with pytest.raises(DataError):
        save_fee_override(path, key, replace(fee, rate=-1))
    with pytest.raises(DataError):
        save_fee_override(path, 'invalid:key', fee)
    path.write_text('{"isin:XX0000000000": {"rate": "bad"}}')
    with pytest.raises(DataError):
        load_fee_overrides(path)


def test_public_fee_requires_exact_isin():
    snapshot = Fundamentals('FFF', 'etf', {'fee': Metric(.1)})
    row = dict(isin='IE00BMC38736', ticker='NOT-THE-US-FUND', instrument_type='etf')
    result = apply_fund_metadata(snapshot, row, {})
    assert result.metrics['fee'].value == .0035
    assert result.metrics['fee'].source.startswith('https://www.vaneck.com/')
    override = FundFee(.004, 'Synthetic override', '2026-01-01')
    assert apply_fund_metadata(snapshot, row, {'isin:IE00BMC38736': override}).metrics['fee'].value == .004
    # A fee-only override must not undo a verified accumulating share class.
    assert apply_fund_metadata(snapshot, row, {'isin:IE00BMC38736': override}).metrics['distribution_yield'].value == 0


def test_blank_instrument_type_can_use_provider_but_explicit_cash_cannot():
    snapshot = normalize_info('AAA', dict(quoteType='EQUITY', trailingPE=12))
    row = dict(ticker='AAA', isin='', instrument_type='')
    assert apply_fund_metadata(snapshot, row, {}).metrics['trailing_pe'].value == 12
    row['instrument_type'] = 'cash'
    assert apply_fund_metadata(snapshot, row, {}).metrics == {}


def test_cache_expiry_failure_cooldown_and_atomic_files(tmp_path):
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    calls = []
    def fetch():
        calls.append(1)
        return {'value': 12}
    cache = AnalyticsCache(tmp_path, now=lambda: now)
    assert cache.get('a', fetch).status == 'fresh'
    assert cache.get('a', fetch).status == 'cached'
    assert len(calls) == 1
    now += timedelta(days=2)
    def fail():
        calls.append(1)
        raise ValueError('offline')
    result = cache.get('a', fail)
    assert result.status == 'stale' and result.data == {'value': 12}
    assert cache.get('a', fail).status == 'stale'
    assert len(calls) == 2
    now += timedelta(minutes=16)
    assert cache.get('a', fetch).status == 'fresh'
    assert cache.get('missing', fail).data is None
    assert len(list(tmp_path.glob('*.json'))) == 2
    assert not list(tmp_path.glob('tmp*'))


def test_fundamentals_service_round_trip_and_corrupt_cache(tmp_path):
    class Provider:
        calls = 0
        def fetch(self, ticker):
            self.calls += 1
            return normalize_info(ticker, dict(quoteType='EQUITY', trailingPE=15))
    provider = Provider()
    service = FundamentalsService(provider, tmp_path)
    assert service.get('aaa').metrics['trailing_pe'].value == 15
    assert service.get('AAA').status == 'cached'
    assert provider.calls == 1
    next(tmp_path.glob('*.json')).write_text('corrupted')
    assert service.get('AAA').status == 'fresh'
    assert provider.calls == 2
    assert service.get('').status == 'unavailable'


def test_yahoo_fund_adapter_isolates_partial_failures(monkeypatch):
    import yfinance as yf
    from portfolio_app.fundamentals import YahooFundamentalsProvider
    class FundData:
        fund_operations = pd.DataFrame({'FFF': [.002]}, index=['Annual Report Expense Ratio'])
        equity_holdings = pd.DataFrame({'FFF': [21., 3.]}, index=['Price/Earnings', 'Price/Book'])
    class Ticker:
        def get_info(self):
            return {'symbol': 'FFF', 'quoteType': 'ETF'}
        def get_funds_data(self):
            return FundData()
    monkeypatch.setattr(yf, 'Ticker', lambda symbol: Ticker())
    result = YahooFundamentalsProvider().fetch('FFF')
    assert result.metrics['fee'].value == .002
    assert result.metrics['fund_pe'].value == 21
    def fail(self):
        raise ValueError('Invented network failure')
    monkeypatch.setattr(Ticker, 'get_funds_data', fail)
    assert YahooFundamentalsProvider().fetch('FFF').metrics['fee'].value is None
    assert 'unavailable' in YahooFundamentalsProvider().fetch('FFF').note
    with pytest.raises(ValueError, match='different listing'):
        YahooFundamentalsProvider().fetch('OTHER')
