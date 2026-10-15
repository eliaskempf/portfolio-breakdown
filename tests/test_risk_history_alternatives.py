"""Invented listings and price paths; no working portfolio or network access."""
from dataclasses import replace

import pandas as pd
import pytest

from portfolio_app.analytics_service import load_risk
from portfolio_app.risk_data import AdjustedHistory, RiskHistoryService, equity_risk_history
from test_risk import TODAY, held, history, market


@pytest.fixture
def listing_pair(monkeypatch):
    monkeypatch.setattr('portfolio_app.risk_data.EQUITY_HISTORY_ALTERNATIVES',
                        {'LOCAL': ('SYNTHETIC-IDENTITY', 'PRIMARY')})


class Provider:
    def __init__(self, *, local=None, fx_missing=False):
        self.prices = history(market())
        self.fx = pd.Series(0.8, index=self.prices.index)
        self.local = local if local is not None else self.prices.tail(8)
        self.fx_missing = fx_missing
        self.calls = []

    def fetch(self, ticker, years):
        self.calls.append(ticker)
        if ticker == 'USDEUR=X':
            if self.fx_missing:
                raise ValueError('Invented FX failure')
            return AdjustedHistory(self.fx, 'EUR')
        return AdjustedHistory(self.local if ticker == 'LOCAL' else self.prices,
                               'USD' if ticker == 'PRIMARY' else 'EUR')


def get(service, provider, **kwargs):
    return equity_risk_history(service, 'LOCAL', kwargs.pop('isin', 'SYNTHETIC-IDENTITY'),
                              'EUR', provider.prices, today=TODAY, **kwargs)


def test_equivalent_listing_uses_whole_adjusted_history_and_historical_fx(listing_pair, tmp_path):
    provider = Provider()
    provider.fx = pd.Series(range(100, 231), index=provider.prices.index) / 100
    service = RiskHistoryService(provider, tmp_path)
    memo = {}
    result = get(service, provider, memo=memo)
    pd.testing.assert_series_equal(result.prices, provider.prices * provider.fx)
    assert provider.calls == ['LOCAL', 'PRIMARY', 'USDEUR=X']
    assert 'LOCAL → PRIMARY' in result.note and 'historical FX' in result.note
    get(service, provider, memo=memo)
    assert len(provider.calls) == 3
    assert get(service, provider).status == 'cached'
    get(service, provider, refresh=True)
    assert len(provider.calls) == 6


@pytest.mark.parametrize('isin', ['', 'SYNTHETIC-IDENTITY'])
def test_loader_uses_alternative_and_discloses_source_without_changing_holding(listing_pair, tmp_path, isin):
    provider = Provider()
    positions = held(['invented'], [100.])
    positions['ticker'] = 'LOCAL'
    positions['isin'] = isin
    original = positions.copy(deep=True)
    result, status = load_risk(positions, tmp_path, False, 'BENCH', 3,
                              service=RiskHistoryService(provider), today=TODAY)
    assert result.status == 'complete' and result.beta == pytest.approx(1.)
    assert status.loc[status.Instrument.eq('LOCAL'), 'Note'].str.contains('LOCAL → PRIMARY').all()
    pd.testing.assert_frame_equal(positions, original)


def test_sufficient_local_history_never_fetches_alternative(listing_pair):
    provider = Provider(local=history(market()))
    result = get(RiskHistoryService(provider), provider)
    assert provider.calls == ['LOCAL']
    pd.testing.assert_series_equal(result.prices, provider.local)


def test_conflicting_identity_and_unreviewed_listing_never_substitute(listing_pair):
    provider = Provider()
    service = RiskHistoryService(provider)
    get(service, provider, isin='DIFFERENT-SHARE')
    equity_risk_history(service, 'UNREVIEWED', '', 'EUR', provider.prices, today=TODAY)
    assert provider.calls == ['LOCAL', 'UNREVIEWED']


def test_missing_fallback_fx_retains_local_history_with_explanation(listing_pair):
    provider = Provider(fx_missing=True)
    result = get(RiskHistoryService(provider), provider)
    pd.testing.assert_series_equal(result.prices, provider.local)
    assert 'PRIMARY history lacks sufficient comparable returns' in result.note


def test_stale_fallback_keeps_both_provenance_and_stale_status(listing_pair):
    provider = Provider()
    class Service(RiskHistoryService):
        def in_currency(self, ticker, *args, **kwargs):
            result = super().in_currency(ticker, *args, **kwargs)
            return replace(result, status='stale', note='Cached market or FX history is stale.') if ticker == 'PRIMARY' else result
    result = get(Service(provider), provider)
    assert result.status == 'stale'
    assert 'LOCAL → PRIMARY' in result.note and 'stale' in result.note


def test_fund_history_does_not_use_equity_alternative(listing_pair, tmp_path):
    provider = Provider()
    positions = held(['invented'], [100.], ['etf'])
    positions['ticker'] = 'LOCAL'
    result, _ = load_risk(positions, tmp_path, False, 'BENCH', 3,
                         service=RiskHistoryService(provider), today=TODAY)
    assert result.status == 'unavailable'
    assert 'PRIMARY' not in provider.calls


def test_alternative_must_cover_requested_window_and_benchmark(listing_pair):
    provider = Provider()
    service = RiskHistoryService(provider)
    late_benchmark = provider.prices.tail(20)
    result = equity_risk_history(service, 'LOCAL', '', 'EUR', late_benchmark, today=TODAY)
    pd.testing.assert_series_equal(result.prices, provider.local)
    assert 'lacks sufficient' in result.note
