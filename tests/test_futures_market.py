"""Invented prices and history only; no network or working portfolio data."""
from dataclasses import asdict, replace
from datetime import date, timedelta
import json

import numpy as np
import pandas as pd
import pytest

from portfolio_app.futures import FuturePosition
from portfolio_app.futures_comparison import ComparisonSettings, comparison_dates, evaluate_paths, cash_growth
from portfolio_app.futures_market import (QUOTE_FILE, VOL_FILE, apply_quote, estimate_volatility,
    load_observation, parse_settlement, refresh_quote, refresh_volatility, statistics_request)
from portfolio_app.futures_workspace import SANDBOX_FILE, load_sandbox
from portfolio_app.holdings import DataError
from portfolio_app.risk_data import AdjustedHistory


def synthetic_statistics():
    return dict(meta=dict(productCode='FMWN', isin='DE000A11Q364'),
                header=dict(tradingDates=['02-01-2001 12:00']), dataRows=[
                    dict(date='20010103', contractType='D', dSettle=12),
                    dict(date='20001215', contractType='M', dSettle=99),
                    dict(date='20010615', contractType='M', dSettle=420),
                    dict(date='20010316', contractType='M', dSettle=410)])


def test_settlement_uses_business_day_and_nearest_quarter_not_daily_or_spot():
    html = '<script data-product="33934">{"productId":"33934","busDate":"20010102"}</script>'
    day, url = statistics_request(html)
    result = parse_settlement(synthetic_statistics(), day, url)
    assert result['price'] == 410
    assert result['contract_expiry'] == '2001-03-16'
    assert result['quote_date'] == '2001-01-02'
    assert 'busdate=20010102' in result['source']
    assert result['kind'] == 'Daily settlement'
    with pytest.raises(DataError, match='date'):
        parse_settlement(synthetic_statistics(), date(2001, 1, 3), url)
    raw = synthetic_statistics()
    raw['meta']['productCode'] = 'FMWO'
    with pytest.raises(DataError, match='instrument'):
        parse_settlement(raw, day, url)
    raw = synthetic_statistics()
    raw['dataRows'][-1]['dSettle'] = 0
    with pytest.raises(DataError, match='settlement'):
        parse_settlement(raw, day, url)


def test_failed_refresh_preserves_reference_and_new_quote_does_not_modify_trade(tmp_path):
    html = '<script data-product="33934">{"productId":"33934","busDate":"20010102"}</script>'
    refresh_quote(tmp_path, fetch=lambda url: json.dumps(synthetic_statistics()) if '/api/' in url else html)
    before = (tmp_path / QUOTE_FILE).read_bytes()
    assert not (tmp_path / SANDBOX_FILE).exists()
    with pytest.raises(DataError):
        refresh_quote(tmp_path, fetch=lambda url: 'unavailable')
    assert (tmp_path / QUOTE_FILE).read_bytes() == before


def test_new_entry_resets_pnl_with_backup_and_stale_revision_protection(tmp_path):
    initial = FuturePosition(entry_price=200, mark_price=300)
    (tmp_path / SANDBOX_FILE).write_text(json.dumps(dict(version=2, simulation=True, instrument='FMWN', position=asdict(initial))))
    today = date.today()
    raw = dict(instrument='FMWN', price=410., quote_date=today.isoformat(),
               contract_expiry=(today+timedelta(days=90)).isoformat(), source='https://www.eurex.com/synthetic',
               kind='Daily settlement', fetched_at=today.isoformat())
    (tmp_path / QUOTE_FILE).write_text(json.dumps(raw))
    _, revision = load_sandbox(tmp_path)
    apply_quote(tmp_path, revision)
    updated, _ = load_sandbox(tmp_path)
    assert updated.pnl == 0 and updated.notional == 41000
    assert updated.quote_date == today.isoformat() and updated.reference_price == 410
    assert len(list((tmp_path / '.backups').glob('*.json'))) == 1
    with pytest.raises(DataError, match='changed'):
        apply_quote(tmp_path, revision)


def test_calendar_expiry_and_cash_use_actual_day_count():
    position = FuturePosition(quote_date='2000-01-01', contract_expiry='2000-03-17', reference_price=500)
    settings = ComparisonSettings(start_date='2000-01-01')
    start, end = comparison_dates(settings, position)
    assert (end-start).days == 76  # Leap year.
    settings = replace(settings, horizon_days=(end-start).days)
    result = evaluate_paths(position, settings, np.ones((2, 1)), [10000]).iloc[0]
    assert result['Cash benchmark end value (EUR)'] == pytest.approx(10000*cash_growth(.03, 76/365.25))
    assert comparison_dates(replace(settings, until_expiry=False, months=12), position)[1] == date(2001, 1, 1)
    with pytest.raises(DataError, match='precede'):
        comparison_dates(replace(settings, start_date='2000-03-17'), position)


def invented_history():
    dates = pd.bdate_range('2000-01-03', periods=780)
    prices = 100*np.exp(np.cumsum(np.random.default_rng(2).normal(0, .01, len(dates))))
    return pd.Series(prices, index=dates)


def test_volatility_excludes_future_observations_and_reports_window():
    prices = invented_history()
    cutoff = prices.index[-5]
    result = estimate_volatility(prices, cutoff.date().isoformat())
    changed = prices.copy()
    changed.loc[changed.index > cutoff] *= 100
    assert estimate_volatility(changed, cutoff.date().isoformat())['volatility'] == result['volatility']
    expected = np.log(prices.loc[:cutoff]).diff().dropna().std(ddof=1)*np.sqrt(252)
    assert result['volatility'] == pytest.approx(expected)
    assert result['end_date'] == cutoff.date().isoformat()
    assert result['observations'] == 775
    with pytest.raises(DataError, match='201'):
        estimate_volatility(prices.iloc[:100], prices.index[99].date().isoformat())
    with pytest.raises(DataError, match='old'):
        estimate_volatility(prices, (prices.index[-1]+pd.Timedelta(days=20)).date().isoformat())


def test_proxy_refresh_requires_fresh_eur_history(tmp_path):
    class Service:
        currency = 'EUR'
        def get(self, ticker, years, refresh):
            assert ticker == 'EUNL.DE' and years == 3 and refresh
            return AdjustedHistory(invented_history(), self.currency)
    service = Service()
    as_of = invented_history().index[-1].date().isoformat()
    refresh_volatility(tmp_path, as_of, service)
    before = load_observation(tmp_path, VOL_FILE)
    service.currency = 'USD'
    with pytest.raises(DataError, match='EUR'):
        refresh_volatility(tmp_path, as_of, service)
    assert load_observation(tmp_path, VOL_FILE) == before


def test_historical_quote_selects_exact_maturity_not_front_contract_or_latest_date():
    from portfolio_app.futures_market import fetch_contract_settlement
    raw = synthetic_statistics()
    raw['header']['tradingDates'].insert(0, '03-01-2001 12:00')
    calls = []
    def fetch(url):
        calls.append(url)
        return json.dumps(raw)
    quote = fetch_contract_settlement(date(2001, 1, 2), date(2001, 6, 15), fetch)
    assert quote['price'] == 420  # Front March contract is 410, daily contract is 12.
    assert quote['quote_date'] == '2001-01-02'
    assert quote['contract_expiry'] == '2001-06-15'
    assert 'busdate=20010102' in calls[0]
    with pytest.raises(DataError, match='date'):
        fetch_contract_settlement(date(2001, 1, 4), date(2001, 6, 15), fetch)
    with pytest.raises(DataError, match='unavailable'):
        fetch_contract_settlement(date(2001, 1, 2), date(2001, 9, 21), fetch)
    with pytest.raises(DataError, match='date'):
        parse_settlement(raw, date(2001, 1, 2), calls[0].replace('20010102', '20010103'), contract_expiry=date(2001, 6, 15))


def test_observed_entry_and_mark_preserve_provenance_collateral_and_backup(tmp_path):
    from portfolio_app.futures_market import fetch_contract_settlement, position_from_settlements
    from portfolio_app.futures_workspace import save_sandbox
    quote = fetch_contract_settlement(date(2001, 1, 2), date(2001, 6, 15), lambda _: json.dumps(synthetic_statistics()))
    mark = {**quote, 'quote_date': '2001-01-03', 'price': 435., 'source': quote['source'].replace('20010102', '20010103')}
    original = FuturePosition(entry_price=200., mark_price=300., bucket_id='synthetic-core')
    (tmp_path / SANDBOX_FILE).write_text(json.dumps(dict(version=2, simulation=True, instrument='FMWN', position=asdict(original))))
    _, revision = load_sandbox(tmp_path)
    trade = position_from_settlements(original, quote, mark, collateral_budget=20000., entry_convention='Synthetic roll convention')
    save_sandbox(tmp_path, trade, revision)
    saved, _ = load_sandbox(tmp_path)
    assert saved == trade and saved.bucket_id == original.bucket_id
    assert saved.entry_date == '2001-01-02' and saved.quote_date == '2001-01-03'
    assert saved.entry_reference_price == 420. and saved.reference_price == 435.
    assert saved.pnl == 1500. and saved.notional == 43500.
    assert saved.collateral_budget == 20000.
    assert len(list((tmp_path / '.backups').glob('*.json'))) == 1
    with pytest.raises(DataError, match='same contract'):
        position_from_settlements(original, quote, {**mark, 'contract_expiry': '2001-09-21'})
    with pytest.raises(DataError, match='entry date'):
        position_from_settlements(original, quote, {**mark, 'quote_date': '2001-01-01'})
    with pytest.raises(DataError, match='reference price and source'):
        replace(saved, entry_reference_price=None)
    with pytest.raises(DataError, match='entry date'):
        replace(saved, entry_date='')


def test_distinct_observed_entry_is_not_reported_as_manual_price_override():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string('''
from portfolio_app.futures import FuturePosition
from portfolio_app.futures_market_ui import render_price_provenance
p = FuturePosition(entry_price=420., mark_price=435., reference_price=435., quote_date='2001-01-03',
    contract_expiry='2001-06-15', entry_date='2001-01-02', entry_reference_price=420.,
    entry_price_source='https://www.eurex.com/synthetic-entry', price_source='https://www.eurex.com/synthetic-mark')
render_price_provenance(p, p.entry_price, p.mark_price)
''').run()
    assert not app.exception
    assert any('Simulated entry: 2001-01-02' in x.value for x in app.caption)
    assert not any('override' in x.value.lower() for x in app.warning)
