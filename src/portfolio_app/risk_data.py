"""Adjusted market histories, isolated from unadjusted chart price histories."""
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from typing import Protocol

import numpy as np
import pandas as pd

from portfolio_app.currencies import quote_unit

from portfolio_app.analytics_cache import AnalyticsCache, utc_now
from portfolio_app.risk import daily_prices, eur_prices


@dataclass(frozen=True)
class AdjustedHistory:
    prices: pd.Series
    currency: str
    status: str = 'fresh'
    fetched_at: str = ''
    note: str = ''


class RiskHistoryProvider(Protocol):
    def fetch(self, ticker: str, years: int) -> AdjustedHistory: ...


class YahooRiskHistoryProvider:
    def fetch(self, ticker, years):
        import yfinance as yf
        instrument = yf.Ticker(ticker)
        start = (pd.Timestamp(date.today()) - pd.DateOffset(years=years) - pd.Timedelta(days=14)).date()
        frame = instrument.history(start=start.isoformat(), interval='1d', auto_adjust=True,
                                   actions=False, timeout=10, raise_errors=True)
        currency = instrument.history_metadata.get('currency', '')
        if frame.empty or 'Close' not in frame:
            raise ValueError('No adjusted market history')
        return AdjustedHistory(daily_prices(frame.Close), currency)


class DemoRiskHistoryProvider:
    def fetch(self, ticker, years):
        end = pd.Timestamp(date.today())
        dates = pd.date_range(end - pd.DateOffset(years=years) - pd.Timedelta(days=14), end - pd.Timedelta(days=1), freq='B')
        # Deterministic invented correlated series, never live demo data.
        seed = int(sha256(ticker.encode()).hexdigest()[:8], 16)
        market = np.random.default_rng(41).normal(.0002, .009, len(dates))
        noise = np.random.default_rng(seed).normal(0, .004, len(dates))
        returns = market if ticker == 'IUSQ.DE' else market * (.5 + seed % 100 / 100) + noise
        return AdjustedHistory(pd.Series(100 * np.cumprod(1 + returns), index=dates), 'EUR', note='Synthetic adjusted history')


class RiskHistoryService:
    def __init__(self, provider: RiskHistoryProvider, directory=None, *, now=utc_now):
        self.provider, self.cache = provider, AnalyticsCache(directory, now=now)

    def get(self, ticker, years=3, *, refresh=False):
        if years not in (1, 3, 5) or not ticker:
            raise ValueError('A listing ticker and 1, 3 or 5 years are required')
        def fetch():
            result = self.provider.fetch(ticker, years)
            prices = daily_prices(result.prices)
            if prices.empty:
                raise ValueError('No valid adjusted prices')
            return dict(dates=[stamp.date().isoformat() for stamp in prices.index], prices=prices.tolist(),
                        currency=result.currency, note=result.note)
        result = self.cache.get(f'adjusted-daily-v1:{ticker}:{years}', fetch, refresh=refresh)
        if result.data is None:
            return AdjustedHistory(pd.Series(dtype=float), '', result.status, result.fetched_at, result.note)
        try:
            raw = result.data
            series = pd.Series(raw['prices'], index=pd.to_datetime(raw['dates']), dtype=float)
            if series.empty or (~np.isfinite(series) | series.le(0)).any():
                raise ValueError('Invalid history cache')
            return AdjustedHistory(daily_prices(series), raw['currency'], result.status, result.fetched_at,
                                   result.note or raw.get('note', ''))
        except (ValueError, TypeError, KeyError):
            return AdjustedHistory(pd.Series(dtype=float), '', 'unavailable', note='Invalid cached history; refresh to retry.')

    def eur(self, ticker, years=3, *, refresh=False, memo=None):
        memo = memo if memo is not None else {}
        def get(symbol):
            if symbol not in memo:
                memo[symbol] = self.get(symbol, years, refresh=refresh)
            return memo[symbol]
        result = get(ticker)
        if result.prices.empty:
            return result
        currency = quote_unit(result.currency)[0]
        if len(currency) != 3 or not currency.isalpha() or not currency.isupper():
            return AdjustedHistory(pd.Series(dtype=float), '', 'unavailable', note='History currency is unavailable')
        fx = get(f'{currency}EUR=X') if currency != 'EUR' else None
        if fx is not None and (fx.prices.empty or fx.currency != 'EUR'):
            return AdjustedHistory(pd.Series(dtype=float), 'EUR', 'unavailable', note='Historical EUR exchange rates unavailable')
        try:
            prices = eur_prices(result.prices, result.currency, fx.prices if fx else None)
        except ValueError as exc:
            return AdjustedHistory(pd.Series(dtype=float), 'EUR', 'unavailable', note=str(exc))
        stale = result.status == 'stale' or (fx is not None and fx.status == 'stale')
        stamps = [r.fetched_at for r in (result, fx) if r is not None and r.fetched_at]
        return AdjustedHistory(prices, 'EUR', 'stale' if stale else result.status, min(stamps, default=''),
                               'Cached market or FX history is stale.' if stale else result.note)
