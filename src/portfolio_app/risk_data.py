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
        if ticker.endswith('EUR=X'):
            rate = {'USD': .9, 'GBP': 1.2}.get(ticker[:-5])
            return AdjustedHistory(pd.Series(rate, index=dates, dtype=float), 'EUR', note='Synthetic FX history')
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
        return self.in_currency(ticker, 'EUR', years, refresh=refresh, memo=memo)

    def in_currency(self, ticker, target, years=3, *, refresh=False, memo=None):
        memo = memo if memo is not None else {}
        def get(symbol):
            if symbol not in memo:
                memo[symbol] = self.get(symbol, years, refresh=refresh)
            return memo[symbol]
        result = get(ticker)
        if result.prices.empty:
            return result
        currency, factor = quote_unit(result.currency)
        if len(currency) != 3 or not currency.isalpha() or not currency.isupper():
            return AdjustedHistory(pd.Series(dtype=float), '', 'unavailable', note='History currency is unavailable')
        prices = result.prices * factor
        legs = [result]
        if currency != target:
            for units, inverse in ((currency, False), (target, True)):
                if units == 'EUR':
                    continue
                fx = get(f'{units}EUR=X')
                if fx.prices.empty or fx.currency != 'EUR':
                    return AdjustedHistory(pd.Series(dtype=float), target, 'unavailable', note=f'Historical {target} exchange rates unavailable')
                prices = prices / fx.prices.reindex(prices.index) if inverse else prices * fx.prices.reindex(prices.index)
                legs.append(fx)
        prices = daily_prices(prices)
        stale = any(leg.status == 'stale' for leg in legs)
        stamps = [leg.fetched_at for leg in legs if leg.fetched_at]
        return AdjustedHistory(prices, target, 'stale' if stale else result.status, min(stamps, default=''),
                               'Cached market or FX history is stale.' if stale else result.note)

    def cash(self, source, target, years=3, *, refresh=False, memo=None):
        # EUR cash starts with a constant EUR value on target FX observation dates.
        if source == 'EUR':
            fx = self.get(f'{target}EUR=X', years, refresh=refresh)
            if fx.currency != 'EUR':
                return AdjustedHistory(pd.Series(dtype=float), target, 'unavailable', note='Historical cash FX unavailable')
            return AdjustedHistory(daily_prices(1 / fx.prices), target, fx.status, fx.fetched_at, fx.note)
        return self.in_currency(f'{source}EUR=X', target, years, refresh=refresh, memo=memo)
