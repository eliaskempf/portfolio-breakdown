"""Direct MSCI daily net-return levels with validated index and currency identity."""
from datetime import date, datetime
import math

import pandas as pd
import requests

from portfolio_app.history import HistoryResult, YahooHistoryProvider

ENDPOINT = 'https://app2.msci.com/products/service/index/indexmaster/getLevelDataForGraph'
INDEX_NAMES = {'990100': 'MSCI World', '891800': 'MSCI Emerging Markets', '892400': 'MSCI ACWI'}
SYMBOLS = {f'MSCI:{code}:NETR:{currency}': currency for code in INDEX_NAMES for currency in ('EUR', 'USD', 'GBP')}


def fetch_levels(params):
    response = requests.get(ENDPOINT, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def parse_levels(raw, currency, start, end, *, index_code='990100'):
    """Reject a different index, currency or return convention, including empty data."""
    if (raw.get('msci_index_code') != index_code or raw.get('index_variant_type') != 'NETR'
            or raw.get('ISO_currency_symbol') != currency):
        raise ValueError('MSCI history metadata does not match the requested index.')
    rows = raw['indexes']['INDEX_LEVELS']
    observations = {}
    for row in rows:
        stamp = datetime.strptime(str(row['calc_date']), '%Y%m%d').date()
        value = row['level_eod']
        if (isinstance(value, bool) or not math.isfinite(float(value)) or float(value) <= 0
                or stamp in observations or not start <= stamp <= end):
            raise ValueError('Invalid or duplicate MSCI index observation.')
        observations[stamp] = float(value)
    if not observations:
        raise ValueError('No MSCI daily index levels returned for this period.')
    ordered = sorted(observations)
    return HistoryResult(tuple(day.isoformat() for day in ordered),
                         tuple(observations[day] for day in ordered), currency, 'fresh',
                         note=f'MSCI daily net-return index levels · Available observations: {ordered[0]} to {ordered[-1]}')


class MarketHistoryProvider:
    """Route explicit MSCI references; keep ordinary Yahoo history unchanged."""
    def __init__(self, *, fetch=fetch_levels, today=date.today, yahoo=None):
        self.fetch, self.today = fetch, today
        self.yahoo = yahoo if yahoo is not None else YahooHistoryProvider()

    def history(self, ticker, period):
        if not ticker.startswith('MSCI:'):
            return self.yahoo.history(ticker, period)
        if ticker not in SYMBOLS:
            raise ValueError('Unsupported MSCI index reference.')
        end = self.today()
        offsets = {'1mo': pd.DateOffset(months=1), '6mo': pd.DateOffset(months=6),
                   '1y': pd.DateOffset(years=1), '5y': pd.DateOffset(years=5)}
        if period == 'max':
            # The public daily endpoint accepts this historical window; earlier
            # requests can return an empty series. State the supported window.
            start = date(2000, 1, 1)
        elif period in offsets:
            start = (pd.Timestamp(end) - offsets[period]).date()
        else:
            raise ValueError('Unsupported history period.')
        currency = SYMBOLS[ticker]
        index_code = ticker.split(':')[1]
        raw = self.fetch(dict(currency_symbol=currency, index_variant='NETR',
            start_date=start.strftime('%Y%m%d'), end_date=end.strftime('%Y%m%d'),
            data_frequency='DAILY', index_codes=index_code))
        result = parse_levels(raw, currency, start, end, index_code=index_code)
        if period == 'max':
            from dataclasses import replace
            result = replace(result, note=result.note + ' · Max requests daily history from 2000; earlier history is not included.')
        return result
