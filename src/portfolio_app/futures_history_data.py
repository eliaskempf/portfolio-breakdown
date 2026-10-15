"""Private snapshots of observed EUR equity-proxy prices and historical €STR."""
from datetime import date, datetime, timezone
from io import StringIO
import json

import numpy as np
import pandas as pd

from portfolio_app.futures_market import PROXY_TICKER, download
from portfolio_app.holdings import DataError
from portfolio_app.storage import revision, save_document

HISTORY_FILE = 'futures-history.json'
ESTR_SOURCE = 'https://data.ecb.europa.eu/data/datasets/EST/EST.B.EU000A2X2A25.WT'


def parse_estr_history(content):
    try:
        frame = pd.read_csv(StringIO(content))
        if frame.empty or not frame['KEY'].eq('EST.B.EU000A2X2A25.WT').all():
            raise ValueError('Wrong or empty ECB series')
        series = pd.Series(pd.to_numeric(frame['OBS_VALUE']).to_numpy()/100,
                           index=pd.to_datetime(frame['TIME_PERIOD'])).sort_index()
        if series.index.has_duplicates or not np.isfinite(series).all() or not series.between(-.1, .2).all():
            raise ValueError('Invalid ECB observations')
        return series
    except (ValueError, KeyError, TypeError) as exc:
        raise DataError(f'Could not verify historical €STR: {exc}') from exc


def unpack_history(raw):
    try:
        if raw['version'] != 1 or raw['ticker'] != PROXY_TICKER:
            raise ValueError('Unsupported history snapshot')
        prices = pd.Series(raw['prices'], index=pd.to_datetime(raw['price_dates']), dtype=float)
        rates = pd.Series(raw['rates'], index=pd.to_datetime(raw['rate_dates']), dtype=float)
        for series in (prices, rates):
            if series.empty or series.index.has_duplicates or not series.index.is_monotonic_increasing or not np.isfinite(series).all():
                raise ValueError('Invalid historical observations')
        if prices.le(0).any() or not rates.between(-.1, .2).all():
            raise ValueError('Invalid price or rate')
        date.fromisoformat(raw['as_of'])
        datetime.fromisoformat(raw['fetched_at'])
        return prices, rates
    except (ValueError, KeyError, TypeError) as exc:
        raise DataError(f'Invalid futures history snapshot: {exc}') from exc


def load_history(directory):
    path = directory / HISTORY_FILE
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
        unpack_history(raw)
        return raw
    except (OSError, ValueError) as exc:
        raise DataError(f'Could not load futures history: {exc}') from exc


def refresh_history(directory, as_of, *, service=None, fetch=download):
    from portfolio_app.risk_data import RiskHistoryService, YahooRiskHistoryProvider
    cutoff = pd.Timestamp(as_of)
    if cutoff.date() > date.today():
        raise DataError('Historical observations cannot end in the future.')
    service = service or RiskHistoryService(YahooRiskHistoryProvider(), directory / '.cache' / 'futures-risk')
    path = directory / HISTORY_FILE
    expected = revision(path)
    observed = service.get(PROXY_TICKER, 5, refresh=True)
    if observed.currency != 'EUR' or observed.status not in ('fresh', 'cached'):
        raise DataError('Fresh EUR equity history unavailable; previous snapshot retained.')
    prices = observed.prices.loc[observed.prices.index <= cutoff]
    if len(prices) < 250 or (cutoff-prices.index[-1]).days > 7:
        raise DataError('Insufficient or stale equity history.')
    first = (prices.index[0]-pd.Timedelta(days=10)).date().isoformat()
    url = f'https://data-api.ecb.europa.eu/service/data/EST/B.EU000A2X2A25.WT?startPeriod={first}&endPeriod={as_of}&format=csvdata'
    rates = parse_estr_history(fetch(url)).loc[:cutoff]
    if rates.empty or rates.index[0] > prices.index[0] or (cutoff-rates.index[-1]).days > 7:
        raise DataError('Historical €STR does not cover the equity history; previous snapshot retained.')
    raw = dict(version=1, ticker=PROXY_TICKER, as_of=as_of,
               price_dates=prices.index.strftime('%Y-%m-%d').tolist(), prices=prices.tolist(),
               rate_dates=rates.index.strftime('%Y-%m-%d').tolist(), rates=rates.tolist(),
               rate_source=ESTR_SOURCE, fetched_at=datetime.now(timezone.utc).isoformat())
    unpack_history(raw)
    save_document(path, json.dumps(raw) + '\n', expected)
    return raw
