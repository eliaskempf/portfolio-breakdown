"""Current cross rates and dated cost conversions, independent of rendering."""
from dataclasses import dataclass
from datetime import date, timedelta
import math
import re

import pandas as pd

from portfolio_app.analytics_cache import AnalyticsCache
from portfolio_app.prices import PriceResult, Quote


def valid_currency(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Z]{3}', value) is not None


def current_rate(prices, source, target, *, refresh=False):
    if not valid_currency(source) or not valid_currency(target):
        return PriceResult(None, 'unavailable', 'Missing or invalid currency')
    if source == target:
        return PriceResult(Quote(1., target, prices.now()), 'identity')
    a, b = prices.fx(source, refresh=refresh), prices.fx(target, refresh=refresh)
    if a.quote is None or b.quote is None or a.quote.currency != 'EUR' or b.quote.currency != 'EUR':
        return PriceResult(None, 'unavailable', f'Missing {source}/{target} exchange rate')
    rate = a.quote.price / b.quote.price
    if not math.isfinite(rate) or rate <= 0:
        return PriceResult(None, 'unavailable', 'Invalid cross rate')
    stamp = min(a.quote.observed_at, b.quote.observed_at)
    status = 'stale' if any(r.status in {'stale', 'cached fallback'} for r in (a, b)) else 'fresh'
    return PriceResult(Quote(rate, target, stamp), status, '; '.join(r.error for r in (a, b) if r.error))


@dataclass(frozen=True)
class DatedRate:
    rate: float | None = None
    observed_on: str = ''
    source: str = 'Yahoo daily FX'
    note: str = ''


class YahooFXHistory:
    def fetch(self, currency, start, end):
        import yfinance as yf
        frame = yf.Ticker(f'{currency}EUR=X').history(start=start, end=end, interval='1d',
            auto_adjust=False, actions=False, timeout=10, raise_errors=True)
        result = pd.to_numeric(frame['Close'], errors='coerce')
        result.index = pd.DatetimeIndex(result.index).tz_localize(None).normalize()
        return result[~result.index.duplicated(keep='last')].sort_index()


class HistoricalFX:
    def __init__(self, provider=None, directory=None):
        self.provider = provider or YahooFXHistory()
        self.cache = AnalyticsCache(directory)

    def get(self, source, target, day, *, refresh=False):
        try:
            when = date.fromisoformat(day)
            if when > date.today() or not valid_currency(source) or not valid_currency(target):
                raise ValueError
        except (ValueError, TypeError):
            return DatedRate(note='Supply a valid purchase date and currency.')
        if source == target:
            return DatedRate(1., day, 'Same currency')
        def fetch():
            start, end = when - timedelta(days=7), when + timedelta(days=1)
            days = pd.date_range(start, when)
            legs = []
            for currency in (source, target):
                series = pd.Series(1., index=days) if currency == 'EUR' else self.provider.fetch(currency, start.isoformat(), end.isoformat())
                legs.append(series.reindex(days))
            rates = (legs[0] / legs[1]).replace([math.inf, -math.inf], float('nan')).dropna()
            rates = rates.loc[rates.gt(0)]
            if rates.empty:
                raise ValueError('No historical FX within seven days')
            return dict(rate=float(rates.iloc[-1]), observed_on=rates.index[-1].date().isoformat())
        result = self.cache.get(f'purchase-fx-v1:{source}:{target}:{day}', fetch, refresh=refresh, ttl=timedelta(days=3650))
        if result.data is None:
            return DatedRate(note='Historical FX unavailable; supply a rate or converted cost.')
        try:
            rate = float(result.data['rate'])
            observed = date.fromisoformat(result.data['observed_on'])
            if not math.isfinite(rate) or rate <= 0 or not when - timedelta(days=7) <= observed <= when:
                raise ValueError
            return DatedRate(rate, observed.isoformat(), note=result.note)
        except (ValueError, TypeError, KeyError):
            return DatedRate(note='Invalid historical FX cache; refresh or supply a conversion.')

    def cached(self, source, target, day):
        """Nonblocking validated cache read for the existing request coordinator."""
        import json
        from datetime import datetime, timezone
        from hashlib import sha256
        key = f'purchase-fx-v1:{source}:{target}:{day}'
        try:
            when = date.fromisoformat(day)
            if when > date.today() or not valid_currency(source) or not valid_currency(target):
                return DatedRate(note='Invalid purchase date or currency.'), False
            if source == target:
                return DatedRate(1., day, 'Same currency'), False
            entry = self.cache.memory.get(key)
            if entry is None and self.cache.directory:
                path = self.cache.directory / (sha256(key.encode()).hexdigest() + '.json')
                entry = json.loads(path.read_text())
            if entry and entry.get('data'):
                raw = entry['data']
                rate, observed = float(raw['rate']), date.fromisoformat(raw['observed_on'])
                if not math.isfinite(rate) or rate <= 0 or not when - timedelta(days=7) <= observed <= when:
                    raise ValueError
                return DatedRate(rate, observed.isoformat()), False
            if entry:
                age = datetime.now(timezone.utc) - datetime.fromisoformat(entry['attempted_at'])
                if timedelta(0) <= age < timedelta(minutes=15):
                    return DatedRate(note='Historical FX unavailable; supply a rate or converted cost.'), False
        except (OSError, ValueError, TypeError, KeyError):
            pass
        return DatedRate(note='Historical FX loading; gains remain unavailable until it is ready.'), True
