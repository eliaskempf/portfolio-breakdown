"""Adjusted market histories, isolated from unadjusted chart price histories."""
from dataclasses import dataclass, replace
from datetime import date
from hashlib import sha256
from typing import Protocol

import numpy as np
import pandas as pd

from portfolio_app.currencies import quote_unit

from portfolio_app.analytics_cache import AnalyticsCache, utc_now
from portfolio_app.risk import DEFAULT_BENCHMARK, MIN_OBSERVATIONS, daily_prices, eur_prices, weekly_returns


# Reviewed same-share listings, used only for equity risk history. Never replace
# valuation quotes, splice currencies, or infer equivalence from company names.
# https://live.deutsche-boerse.com/equity/vertiv-hol-cl-a-dl-0001
# https://investors.vertiv.com/overview/default.aspx
EQUITY_HISTORY_ALTERNATIVES = {'49V.DE': ('US92537N1081', 'VRT')}


def equity_risk_history(service, ticker, isin, target, benchmark, years=3, *, refresh=False, memo=None, today=None):
    """Prefer the held listing; use a reviewed same-share listing if coverage fails.

    Both histories are independently adjusted and converted at historical FX.
    Compare completed weekly returns in the requested benchmark/window; never
    extend a series by stitching together prices from different trading venues.
    """
    memo = memo if memo is not None else {}
    original = service.in_currency(ticker, target, years, refresh=refresh, memo=memo)
    alternative = EQUITY_HISTORY_ALTERNATIVES.get(ticker)
    if alternative is None or (isin and isin != alternative[0]):
        return original
    end = pd.Timestamp(today or date.today())
    bench = weekly_returns(benchmark, today=end)
    if bench.empty:
        return original
    bench = bench.loc[bench.index >= end - pd.DateOffset(years=years)]

    def observations(prices):
        returns = weekly_returns(prices, today=end).reindex(bench.index)
        return int((returns.notna() & bench.notna()).sum())

    if observations(original.prices) >= MIN_OBSERVATIONS:
        return original
    symbol = alternative[1]
    fallback = service.in_currency(symbol, target, years, refresh=refresh, memo=memo)
    if observations(fallback.prices) < MIN_OBSERVATIONS:
        return replace(original, note=(original.note + f' Alternative {symbol} history lacks sufficient comparable returns.').strip())
    return replace(fallback, note=(f'Risk history: {ticker} → {symbol}, reviewed same-share listing; '
        f'adjusted prices converted to {target} at historical FX. Held listing has insufficient comparable history. '
        + fallback.note).strip())


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


class MarketRiskHistoryProvider:
    """Direct ACWI net-return benchmark; held instruments keep adjusted prices."""
    def __init__(self, *, fetch=None, today=date.today, yahoo=None):
        from portfolio_app.msci_history import fetch_levels
        self.fetch_levels = fetch or fetch_levels
        self.today = today
        self.yahoo = yahoo if yahoo is not None else YahooRiskHistoryProvider()

    def fetch(self, ticker, years):
        if not ticker.startswith('MSCI:'):
            return self.yahoo.fetch(ticker, years)
        from portfolio_app.msci_history import parse_levels
        parts = ticker.split(':')
        if (len(parts) != 4 or ':'.join(parts[:3]) != DEFAULT_BENCHMARK
                or len(parts[3]) != 3 or not parts[3].isascii() or not parts[3].isalpha() or not parts[3].isupper()):
            raise ValueError('Unsupported MSCI risk benchmark.')
        currency = parts[3]
        end = self.today()
        start = (pd.Timestamp(end) - pd.DateOffset(years=years) - pd.Timedelta(days=14)).date()
        raw = self.fetch_levels(dict(currency_symbol=currency, index_variant='NETR',
            start_date=start.strftime('%Y%m%d'), end_date=end.strftime('%Y%m%d'),
            data_frequency='DAILY', index_codes='892400'))
        result = parse_levels(raw, currency, start, end, index_code='892400')
        return AdjustedHistory(pd.Series(result.prices, index=pd.to_datetime(result.dates)), currency,
            note='MSCI ACWI · Net return index · ' + currency + ' · ' + result.note)


def benchmark_risk_history(service, benchmark, target, years, *, refresh=False, memo=None, today=None):
    """Prefer cached or live direct index levels; disclose any whole-series proxy."""
    symbol = f'{benchmark}:{target}' if benchmark == DEFAULT_BENCHMARK else benchmark
    history = service.in_currency(symbol, target, years, refresh=refresh, memo=memo)
    if benchmark != DEFAULT_BENCHMARK:
        return history, symbol
    end = pd.Timestamp(today or date.today())
    returns = weekly_returns(history.prices, today=end)
    if not returns.empty and returns.loc[returns.index >= end - pd.DateOffset(years=years)].count() >= MIN_OBSERVATIONS:
        return history, symbol
    fallback = service.in_currency('IUSQ.DE', target, years, refresh=refresh, memo=memo)
    return replace(fallback, note=('MSCI ACWI ETF fallback · IUSQ.DE · Direct net-return index history '
        'unavailable or insufficient. Uses adjusted ETF history in ' + target + '. ' + fallback.note).strip()), 'IUSQ.DE'


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
        is_index = ticker.startswith(DEFAULT_BENCHMARK + ':')
        returns = market if ticker == 'IUSQ.DE' or is_index else market * (.5 + seed % 100 / 100) + noise
        return AdjustedHistory(pd.Series(100 * np.cumprod(1 + returns), index=dates),
                               ticker.split(':')[-1] if is_index else 'EUR', note='Synthetic adjusted history')


class RiskHistoryService:
    def __init__(self, provider: RiskHistoryProvider, directory=None, *, now=utc_now):
        self.provider, self.cache = provider, AnalyticsCache(directory, now=now)

    def get(self, ticker, years=3, *, refresh=False):
        if years not in (1, 3, 5, 10) or not ticker:
            raise ValueError('A listing ticker and 1, 3, 5 or 10 years are required')
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
