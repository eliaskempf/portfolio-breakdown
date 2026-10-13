"""Timestamped monitoring series, isolated from valuation and existing chart caches."""
from dataclasses import asdict, replace
from datetime import timedelta
import json
import math
from numbers import Real
from threading import RLock

import pandas as pd

from portfolio_app.analytics_cache import AnalyticsCache
from portfolio_app.currencies import quote_unit
from portfolio_app.listing_quality import quote_issue
from portfolio_app.monitoring import MarketSeries, utc_now


def _provider_timestamp(value):
    # yfinance may already normalize metadata epoch seconds to aware timestamps.
    return pd.to_datetime(value, unit='s' if isinstance(value, Real) else None, utc=True)


def yahoo_series(frame, metadata, basis, now):
    """Daily bars can still be developing. Never infer a close from fetch time."""
    unit, factor = quote_unit(metadata.get('currency', ''))
    zone = metadata.get('exchangeTimezoneName')
    if len(unit) != 3 or not zone or 'Close' not in frame:
        raise ValueError('Currency, exchange timezone or closing prices are missing.')
    local = pd.Timestamp(now).tz_convert(zone)
    frame = frame.sort_index()
    if frame.index.has_duplicates:
        raise ValueError('Duplicate trading dates')
    # Yahoo includes empty placeholders (holidays and absent bars) in max history.
    # They are not closing observations. Never fill prices or drop partial bars.
    empty = frame[['Open', 'High', 'Low', 'Close']].isna().all(axis=1) & frame['Volume'].fillna(0).eq(0) if all(
        c in frame for c in ('Open', 'High', 'Low', 'Close', 'Volume')) else pd.Series(False, index=frame.index)
    omitted = int(empty.sum())
    frame = frame.loc[~empty]
    raw = frame['Close']
    if basis == 'adjusted':
        adjusted = frame['Adj Close']
        anchor = pd.concat([raw, adjusted], axis=1).dropna().iloc[-1]
        if anchor.iloc[0] <= 0 or anchor.iloc[1] <= 0:
            raise ValueError('Invalid adjustment basis')
        # Rebase dividend-adjusted history to current quote units. Ratios remain
        # unchanged; current raw quotes and the SMA share the same scale.
        prices = adjusted * (anchor.iloc[0]/anchor.iloc[1])
    else:
        prices = raw
    regular = (metadata.get('currentTradingPeriod') or {}).get('regular') or {}
    end = _provider_timestamp(regular['end']) if regular.get('end') else None
    current_close_confirmed = end is not None and end.tz_convert(zone).date() == local.date() and pd.Timestamp(now) >= end+pd.Timedelta(minutes=15)
    dates, values, complete = [], [], []
    for stamp, value in prices.items():
        day = pd.Timestamp(stamp).date()
        if day > local.date():
            continue
        if pd.isna(value):
            raise ValueError('Missing closing observation in source history')
        if not math.isfinite(value) or value <= 0:
            raise ValueError('Invalid closing observation')
        dates.append(day.isoformat()); values.append(float(value)*factor)
        complete.append(day < local.date() or current_close_confirmed)
    quote = metadata.get('regularMarketPrice')
    quote_time = metadata.get('regularMarketTime')
    if quote is not None and quote_time:
        quote = float(quote)*factor
        if not math.isfinite(quote) or quote <= 0:
            quote = None
        stamp = _provider_timestamp(quote_time).isoformat()
    else:
        quote, stamp = None, ''
    if not dates:
        raise ValueError('No market history')
    note = 'Provider observations may be delayed. Regular-session quotes only.'
    if omitted:
        note += f' {omitted} empty source rows omitted; no prices filled. Averages use available closing observations.'
    return MarketSeries(tuple(dates), tuple(values), tuple(complete), unit, now.isoformat(), quote, stamp,
                        'valid', note, zone)


class MonitoringProvider:
    def fetch(self, spec, *, now=None):
        now = now or utc_now()
        if spec.symbol.startswith('MSCI:'):
            from portfolio_app.msci_history import MarketHistoryProvider
            result = MarketHistoryProvider().history(spec.symbol, 'max')
            return MarketSeries(result.dates, result.prices, (True,)*len(result.dates), result.currency,
                                now.isoformat(), quality='valid', note=result.note)
        if issue := quote_issue(spec.symbol):
            raise ValueError(issue)
        import yfinance as yf
        instrument = yf.Ticker(spec.symbol)
        frame = instrument.history(period='max', interval='1d', auto_adjust=False, actions=True, keepna=True, timeout=15)
        source = instrument.history_metadata
        # Do not enumerate this lazy mapping: that fetches unrelated intraday
        # tradingPeriods and can fail even when daily history is complete.
        metadata = {key: source.get(key) for key in ('currency', 'exchangeTimezoneName',
                    'currentTradingPeriod', 'regularMarketPrice', 'regularMarketTime')}
        # Only the quote endpoint provides an actual observation timestamp on
        # sources whose daily chart metadata omits regularMarketTime.
        if not metadata.get('regularMarketTime'):
            try:
                info = instrument.get_info()
            except Exception:
                info = {}  # Daily history remains usable without intraday metadata.
            for key in ('regularMarketTime', 'regularMarketPrice'):
                if key in info:
                    metadata[key] = info[key]
        return yahoo_series(frame, metadata, spec.basis, now)


class SyntheticMonitoringProvider:
    """Invented data only; no network or personal workspace reads."""
    def fetch(self, spec, *, now=None):
        now = now or utc_now()
        dates = pd.bdate_range(end=pd.Timestamp(now).date()-timedelta(days=1), periods=1600)
        values = tuple(100+i*.015+5*math.sin(i/25) for i in range(len(dates)))
        return MarketSeries(tuple(x.date().isoformat() for x in dates), values, (True,)*len(dates), spec.currency or 'EUR',
                            now.isoformat(), values[-1]*.995, now.replace(second=0, microsecond=0).isoformat(), 'valid', 'Synthetic invented market observations')


class MonitoringMarket:
    def __init__(self, directory, *, provider=None, now=utc_now):
        self.now = now
        self.lock = RLock()
        self.source_locks = {}
        self.provider = provider or MonitoringProvider()
        self.cache = AnalyticsCache(directory / '.cache' / 'monitoring', now=now)

    def get(self, spec, *, refresh=False):
        key = 'monitoring-v2:'+json.dumps(asdict(spec), sort_keys=True)
        with self.lock:
            source_lock = self.source_locks.setdefault(key, RLock())
        with source_lock:
            return self._get(spec, key, refresh)

    def _get(self, spec, key, refresh):
        result = self.cache.get(key, lambda: asdict(self.provider.fetch(spec, now=self.now())),
                                refresh=refresh, ttl=timedelta(minutes=5))
        if result.data is None:
            return MarketSeries(note=result.note or 'Market history unavailable.')
        try:
            raw = dict(result.data)
            for name in ('dates', 'closes', 'complete'):
                raw[name] = tuple(raw[name])
            series = MarketSeries(**raw)
            if result.status == 'stale':
                series = replace(series, quality='stale', note=result.note)
            return series
        except (TypeError, ValueError, KeyError):
            return MarketSeries(note='Invalid monitoring cache; refresh this source.')
