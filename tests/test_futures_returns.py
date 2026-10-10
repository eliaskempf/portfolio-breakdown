"""Invented EUR prices; no downloads or personal files."""
import json

import numpy as np
import pandas as pd
import pytest

from portfolio_app.futures_returns import RETURN_FILE, estimate_index_return, load_return_estimate, refresh_return_history
from portfolio_app.holdings import DataError
from portfolio_app.risk_data import AdjustedHistory


def invented_returns(as_of='2025-01-02'):
    end = pd.Timestamp(as_of)
    dates = pd.bdate_range(end-pd.DateOffset(years=10), end)
    increments = np.random.default_rng(31).normal(.00025, .008, len(dates))
    return pd.Series(100*np.exp(np.cumsum(increments)), index=dates)


def write_synthetic_return_history(directory, as_of='2025-01-02'):
    class Service:
        def get(self, ticker, years, refresh):
            assert ticker == 'EUNL.DE' and years == 10 and refresh
            return AdjustedHistory(invented_returns(as_of), 'EUR')
    return refresh_return_history(directory, as_of, Service())


def test_historical_drift_matches_gbm_parameter_and_cagr_not_futures_returns():
    prices = invented_returns()
    result = estimate_index_return(prices, prices.index[-1])
    years = (prices.index[-1]-prices.index[0]).days/365.25
    log_returns = np.log(prices).diff().dropna()
    annual_log = np.log(prices.iloc[-1]/prices.iloc[0])/years
    variance = log_returns.var(ddof=1)*len(log_returns)/years
    assert result['index_return'] == pytest.approx(annual_log+variance/2)
    assert result['cagr'] == pytest.approx((prices.iloc[-1]/prices.iloc[0])**(1/years)-1)
    assert result['index_return'] > 0
    assert result['observations'] == len(prices)-1
    assert result['ticker'] == 'EUNL.DE'


def test_historical_growth_uses_only_ten_year_window_and_no_future_prices():
    prices = invented_returns()
    cutoff = prices.index[-50]
    result = estimate_index_return(prices, cutoff)
    changed = prices.copy()
    changed.loc[changed.index > cutoff] *= 100
    assert estimate_index_return(changed, cutoff) == result
    extended = pd.concat([pd.Series([1e10], index=[prices.index[0]-pd.DateOffset(years=1)]), prices])
    assert estimate_index_return(extended, prices.index[-1]) == estimate_index_return(prices, prices.index[-1])


def test_missing_stale_gapped_duplicate_or_short_history_is_not_zero_growth(tmp_path):
    prices = invented_returns()
    assert load_return_estimate(tmp_path, prices.index[-1]) is None
    for invalid in [prices.iloc[-300:], prices.iloc[:-20], prices.drop(prices.index[600:630]),
                    pd.concat([prices, prices.iloc[-1:]])]:
        with pytest.raises(DataError):
            estimate_index_return(invalid, prices.index[-1])


def test_refresh_validates_currency_and_preserves_good_snapshot_on_failure(tmp_path):
    result = write_synthetic_return_history(tmp_path)
    before = (tmp_path/RETURN_FILE).read_bytes()
    assert load_return_estimate(tmp_path, '2025-01-02') == result
    class WrongCurrency:
        def get(self, *args, **kwargs):
            return AdjustedHistory(invented_returns(), 'USD')
    with pytest.raises(DataError):
        refresh_return_history(tmp_path, '2025-01-02', WrongCurrency())
    assert (tmp_path/RETURN_FILE).read_bytes() == before
    raw = json.loads(before)
    raw['currency'] = 'USD'
    (tmp_path/RETURN_FILE).write_text(json.dumps(raw))
    with pytest.raises(DataError):
        load_return_estimate(tmp_path, '2025-01-02')
