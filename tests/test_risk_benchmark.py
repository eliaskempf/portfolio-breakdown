"""Synthetic direct-index histories, source identity and cache/fallback behavior."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from portfolio_app.risk import DEFAULT_BENCHMARK
from portfolio_app.risk_data import (
    AdjustedHistory, MarketRiskHistoryProvider, RiskHistoryService, benchmark_risk_history,
)


def payload(params):
    dates = pd.date_range(params['start_date'], params['end_date'], freq='B')
    return dict(msci_index_code=params['index_codes'], index_variant_type='NETR',
        ISO_currency_symbol=params['currency_symbol'], indexes={'INDEX_LEVELS': [
            dict(calc_date=int(day.strftime('%Y%m%d')), level_eod=100 + i / 100 + np.sin(i))
            for i, day in enumerate(dates)]})


@pytest.mark.parametrize('currency', ['EUR', 'USD', 'GBP'])
def test_direct_benchmark_requests_exact_identity_currency_and_window(currency):
    calls = []
    def fetch(params):
        calls.append(params)
        return payload(params)
    provider = MarketRiskHistoryProvider(fetch=fetch, today=lambda: date(2026, 10, 15),
        yahoo=SimpleNamespace(fetch=lambda *a: pytest.fail('Direct index must not use ETF or FX history')))
    history, symbol = benchmark_risk_history(RiskHistoryService(provider), DEFAULT_BENCHMARK,
        currency, 3, today=date(2026, 10, 15))
    assert symbol == f'MSCI:892400:NETR:{currency}' and history.currency == currency
    assert len(history.prices) > 750
    assert calls == [dict(index_codes='892400', index_variant='NETR', currency_symbol=currency,
        data_frequency='DAILY', start_date='20231001', end_date='20261015')]


@pytest.mark.parametrize('field,value', [('msci_index_code', '990100'), ('index_variant_type', 'STRD'),
                                         ('ISO_currency_symbol', 'USD')])
def test_risk_provider_rejects_wrong_index_currency_or_return_type(field, value):
    def fetch(params):
        raw = payload(params)
        raw[field] = value
        return raw
    provider = MarketRiskHistoryProvider(fetch=fetch, today=lambda: date(2026, 10, 15))
    with pytest.raises(ValueError, match='metadata'):
        provider.fetch('MSCI:892400:NETR:EUR', 3)


def test_cached_direct_index_precedes_etf_fallback(tmp_path):
    now = [datetime(2026, 10, 15, tzinfo=timezone.utc)]
    failed = [False]
    def fetch(params):
        if failed[0]:
            raise ConnectionError('Synthetic outage')
        return payload(params)
    service = RiskHistoryService(MarketRiskHistoryProvider(fetch=fetch, today=lambda: now[0].date(),
        yahoo=SimpleNamespace(fetch=lambda *a: pytest.fail('Usable cached index must win'))), tmp_path, now=lambda: now[0])
    first, symbol = benchmark_risk_history(service, DEFAULT_BENCHMARK, 'EUR', 3, today=now[0].date())
    assert first.status == 'fresh'
    failed[0] = True
    now[0] += timedelta(days=2)
    stale, source = benchmark_risk_history(service, DEFAULT_BENCHMARK, 'EUR', 3, today=now[0].date())
    assert stale.status == 'stale' and source == symbol
    pd.testing.assert_series_equal(first.prices, stale.prices)


@pytest.mark.parametrize('short_index', [False, True])
def test_missing_or_short_index_uses_one_disclosed_etf_series(short_index):
    calls = []
    dates = pd.date_range('2023-10-01', '2026-10-14', freq='B')
    proxy = pd.Series(np.arange(len(dates)) + 100., index=dates)
    def fetch(params):
        if not short_index:
            raise ConnectionError('Synthetic outage')
        raw = payload(params)
        raw['indexes']['INDEX_LEVELS'] = raw['indexes']['INDEX_LEVELS'][-10:]
        return raw
    def yahoo(ticker, years):
        calls.append(ticker)
        return AdjustedHistory(proxy, 'EUR')
    service = RiskHistoryService(MarketRiskHistoryProvider(fetch=fetch, today=lambda: date(2026, 10, 15),
        yahoo=SimpleNamespace(fetch=yahoo)))
    result, symbol = benchmark_risk_history(service, DEFAULT_BENCHMARK, 'EUR', 3, today=date(2026, 10, 15))
    assert symbol == 'IUSQ.DE' and 'ETF fallback' in result.note
    assert calls == ['IUSQ.DE']
    pd.testing.assert_series_equal(result.prices, proxy, check_freq=False)


def test_explicit_custom_benchmark_is_never_substituted():
    history = AdjustedHistory(pd.Series(dtype=float), 'EUR', 'unavailable')
    calls = []
    service = SimpleNamespace(in_currency=lambda *a, **kw: (calls.append(a) or history))
    result, symbol = benchmark_risk_history(service, 'SYNTH-CUSTOM', 'EUR', 3)
    assert result is history and symbol == 'SYNTH-CUSTOM'
    assert calls == [('SYNTH-CUSTOM', 'EUR', 3)]


def test_unavailable_index_and_proxy_keep_source_disclosure_without_crashing(tmp_path):
    from portfolio_app.analytics_service import load_risk
    held = pd.DataFrame(dict(id=['a'], name=['Invented equity'], ticker=['SYNTH-A'], shares=[1.],
        instrument_type=['equity'], current_value_reporting=[100.], quote_currency=['EUR']))
    def fail(*args, **kwargs):
        raise ConnectionError('Synthetic outage')
    service = RiskHistoryService(MarketRiskHistoryProvider(fetch=fail, yahoo=SimpleNamespace(fetch=fail)))
    risk, sources = load_risk(held, tmp_path, False, DEFAULT_BENCHMARK, 3, service=service)
    assert risk.status == 'unavailable'
    assert sources.attrs['benchmark_symbol'] == 'IUSQ.DE'
    assert sources.attrs['benchmark_fallback']
    assert 'ETF fallback' in sources.iloc[0].Note
