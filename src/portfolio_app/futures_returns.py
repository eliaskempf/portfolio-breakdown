"""Historical EUR index-growth calibration, explicitly a proxy, not FMWN fills."""
from datetime import datetime, timezone
import json
import math

import numpy as np
import pandas as pd

from portfolio_app.futures_market import PROXY_TICKER, PROXY_URL
from portfolio_app.holdings import DataError
from portfolio_app.storage import revision, save_document

RETURN_FILE = 'futures-return-history.json'


def estimate_index_return(prices, as_of):
    """Up to ten years, at least three; calendar-time log growth, no look-ahead.

    The GBM parameter mu is annual log growth + half annual log variance.
    It is not CAGR: the simulator separately subtracts half variance. Report both
    and the fitted volatility. Simulation volatility remains independently editable.
    """
    series = pd.Series(prices).sort_index()
    if not isinstance(series.index, pd.DatetimeIndex) or series.index.has_duplicates:
        raise DataError('Return history requires unique daily dates.')
    if series.index.tz is not None:
        series.index = series.index.tz_localize(None)
    cutoff = pd.Timestamp(as_of)
    series = series.loc[(series.index <= cutoff) & (series.index >= cutoff-pd.DateOffset(years=10))]
    if len(series) < 751 or not np.isfinite(series).all() or series.le(0).any():
        raise DataError('At least three years of valid daily EUR proxy history are required.')
    years = (series.index[-1]-series.index[0]).days/365.25
    if years < 2.9 or (cutoff-series.index[-1]).days > 7:
        raise DataError('Return history must cover at least three years and end near the simulation start.')
    if series.index.to_series().diff().dt.days.dropna().gt(7).any():
        raise DataError('Return history has a gap longer than seven days.')
    returns = np.log(series).diff().dropna()
    log_growth = float(returns.sum()/years)
    variance = float(returns.var(ddof=1)*len(returns)/years)
    drift = log_growth + variance/2
    if not -.5 <= drift <= .5 or not 0 <= variance <= 1:
        raise DataError('Historical calibration is outside the supported model range.')
    return dict(ticker=PROXY_TICKER, source=PROXY_URL, log_growth=log_growth,
        index_return=drift, cagr=math.expm1(log_growth), volatility=math.sqrt(variance),
        observations=len(returns), years=years, start_date=series.index[0].date().isoformat(),
        end_date=series.index[-1].date().isoformat(), as_of=cutoff.date().isoformat())


def load_return_history(directory):
    """Read the saved EUR proxy only; opening a position never downloads data."""
    path = directory / RETURN_FILE
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
        if raw['version'] != 1 or raw['ticker'] != PROXY_TICKER or raw['currency'] != 'EUR':
            raise ValueError('Unsupported return-history snapshot')
        datetime.fromisoformat(raw['fetched_at'])
        prices = pd.Series(raw['prices'], index=pd.to_datetime(raw['dates']), dtype=float)
        if (prices.empty or prices.index.hasnans or prices.index.has_duplicates
                or not prices.index.is_monotonic_increasing or prices.index.tz is not None
                or not np.isfinite(prices).all() or prices.le(0).any()):
            raise ValueError('Invalid dated proxy prices')
        return prices, raw['fetched_at']
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise DataError(f'Invalid saved return history: {exc}') from exc


def load_return_estimate(directory, as_of):
    history = load_return_history(directory)
    if history is None:
        return None
    prices, fetched_at = history
    return dict(estimate_index_return(prices, as_of), fetched_at=fetched_at)


def refresh_return_history(directory, as_of, service=None):
    from portfolio_app.risk_data import RiskHistoryService, YahooRiskHistoryProvider
    service = service or RiskHistoryService(YahooRiskHistoryProvider(), directory / '.cache' / 'futures-risk')
    path = directory / RETURN_FILE
    expected = revision(path)
    observed = service.get(PROXY_TICKER, 10, refresh=True)
    if observed.currency != 'EUR' or observed.status not in ('fresh', 'cached'):
        raise DataError('Fresh EUR proxy history unavailable; previous return history retained.')
    estimate_index_return(observed.prices, as_of)
    prices = observed.prices.loc[observed.prices.index <= pd.Timestamp(as_of)]
    raw = dict(version=1, ticker=PROXY_TICKER, currency='EUR', dates=prices.index.strftime('%Y-%m-%d').tolist(),
        prices=prices.tolist(), fetched_at=datetime.now(timezone.utc).isoformat())
    save_document(path, json.dumps(raw)+'\n', expected)
    return load_return_estimate(directory, as_of)
