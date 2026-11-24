"""On-demand market-price history, separate from personal investment returns."""
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import os
import re
from pathlib import Path
from portfolio_app.workspace.atomic_file import staged_text
from threading import Lock
from typing import Protocol

import pandas as pd

from portfolio_app.market.currencies import quote_unit
from portfolio_app.market.listing_quality import quote_issue

PERIODS = {"1M": "1mo", "6M": "6mo", "1Y": "1y", "5Y": "5y", "Max": "max"}


@dataclass(frozen=True)
class HistoryResult:
    dates: tuple[str, ...] = ()
    prices: tuple[float, ...] = ()
    currency: str = ""
    status: str = "unavailable"
    fetched_at: str = ""
    note: str = ""
    attempted_at: str = ""


class HistoryProvider(Protocol):
    def history(self, ticker: str, period: str) -> HistoryResult: ...


def normalize_history(frame, currency: str) -> HistoryResult:
    currency, factor = quote_unit(currency)
    if len(currency) != 3 or not currency.isupper():
        raise ValueError("History quote currency is unavailable.")
    if "Close" not in frame:
        raise ValueError("No closing prices available.")
    closes = frame.Close.dropna().sort_index()
    closes = closes.loc[~closes.index.duplicated(keep="last")]
    pairs = [(stamp.isoformat(), float(value) * factor) for stamp, value in closes.items()
             if math.isfinite(value) and value > 0]
    if not pairs:
        raise ValueError("No closing prices available.")
    return HistoryResult(tuple(p[0] for p in pairs), tuple(p[1] for p in pairs), currency, "fresh")


class YahooHistoryProvider:
    def history(self, ticker, period):
        if issue := quote_issue(ticker):
            raise ValueError(issue)
        import yfinance as yf
        instrument = yf.Ticker(ticker)
        frame = instrument.history(period=period, interval="1d", auto_adjust=False,
                                   actions=False, timeout=10, raise_errors=True)
        return normalize_history(frame, instrument.history_metadata.get("currency", ""))


class DemoHistoryProvider:
    """Deterministic invented prices; never fetches or substitutes live data."""
    def history(self, ticker, period):
        if ticker.endswith('EUR=X'):
            rate = {'USD': .9, 'GBP': 1.2}.get(ticker[:-5])
            if rate is None:
                return HistoryResult(note='No synthetic FX history')
            dates = pd.date_range(end="2026-09-01", periods={"1mo": 30, "6mo": 180, "1y": 365, "5y": 1825, "max": 2200}[period])
            return replace(normalize_history(pd.DataFrame({'Close': rate}, index=dates), 'EUR'), note='Synthetic demo FX history')
        count = {"1mo": 30, "6mo": 180, "1y": 365, "5y": 1825, "max": 2200}[period]
        dates = pd.date_range(end="2026-09-01", periods=count)
        frame = pd.DataFrame({"Close": [80 + i * .03 + 3 * math.sin(i / 12) for i in range(count)]}, index=dates)
        return replace(normalize_history(frame, "EUR"), note="Synthetic demo price history")


def _validated_history(result):
    dates, prices = tuple(result.dates), tuple(result.prices)
    if len(dates) != len(prices) or any(not math.isfinite(v) or v <= 0 for v in prices):
        raise ValueError('Invalid market-price history')
    return replace(result, dates=dates, prices=prices)


def _file_version(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _history_version(path):
    try:
        return _file_version(path.stat())
    except OSError as exc:
        return exc.errno


class HistoryService:
    def __init__(self, provider: HistoryProvider, cache_dir: Path | None = None, *, now=None):
        self.provider, self.cache_dir = provider, cache_dir
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._memory = {}  # Only results without a durable copy.
        self._saved = OrderedDict()
        self._saved_lock = Lock()

    def _path(self, ticker, period):
        return self.cache_dir / (sha256(f"{ticker}:{period}:close-v1".encode()).hexdigest() + ".json") if self.cache_dir else None

    def _read_history(self, key, path, version):
        pending = self._memory.get(key)
        if pending is not None and pending[0] == version:
            return pending[1]
        if path is None or isinstance(version, int):
            return None
        with self._saved_lock:
            saved = self._saved.get(key)
            if saved is not None and saved[0] == version:
                self._saved.move_to_end(key)
                return saved[1]
        # Capture the opened document's identity. A concurrent atomic replacement
        # must not associate the parsed prices with a different file.
        with path.open('rb') as handle:
            opened = _file_version(os.fstat(handle.fileno()))
            raw = json.load(handle)
            if 'dates' not in raw or 'prices' not in raw:
                raise ValueError('Incomplete market-price history')
            result = _validated_history(HistoryResult(**raw))
            stable = opened == _file_version(os.fstat(handle.fileno()))
        if stable:
            with self._saved_lock:
                self._saved[key] = opened, result
                self._saved.move_to_end(key)
                if len(self._saved) > 32:
                    self._saved.popitem(last=False)
        return result

    def cached(self, ticker: str, period: str = "1Y", *, manual=False) -> tuple[HistoryResult, bool]:
        """Read saved history immediately, including a short failed-attempt cooldown."""
        return self._lookup(ticker, period, manual)[0]

    def _lookup(self, ticker, period, manual):
        if issue := quote_issue(ticker):
            return (HistoryResult(note=issue), False), None, None
        if not ticker or manual:
            return (HistoryResult(note="Market-price history is unavailable for this instrument."), False), None, None
        if period not in PERIODS:
            raise ValueError("Unsupported history period.")
        path = self._path(ticker, period)
        version = _history_version(path) if path else None
        try:
            reading = self._cached_value((ticker, period), path, version)
        except OSError:
            reading = HistoryResult(note='Loading market prices…'), True
        return reading, path, version

    def _cached_value(self, key, path, version):
        cached = None
        try:
            cached = self._read_history(key, path, version)
            if cached:
                if cached.attempted_at and cached.status in {'stale', 'unavailable'}:
                    age = self.now() - datetime.fromisoformat(cached.attempted_at)
                    if timedelta(0) <= age < timedelta(seconds=60):
                        return cached, False
                if cached.prices:
                    age = self.now() - datetime.fromisoformat(cached.fetched_at)
                    if timedelta(0) <= age < timedelta(hours=1):
                        return replace(cached, status='cached'), False
        except (ValueError, TypeError, AttributeError, KeyError):
            cached = None
        if cached and cached.prices:
            return replace(cached, status='stale', note='Showing saved market prices while refreshing.'), True
        return HistoryResult(note='Loading market prices…'), True

    def get(self, ticker: str, period: str = "1Y", *, manual=False, refresh=False) -> HistoryResult:
        (cached, due), path, version = self._lookup(ticker, period, manual)
        if not ticker or manual or quote_issue(ticker) or (not due and not refresh):
            return cached
        try:
            result = _validated_history(self.provider.history(ticker, PERIODS[period]))
            if not result.prices and cached.prices:
                raise ValueError('Refresh returned no market prices')
            stamp = self.now().isoformat()
            result = replace(result, fetched_at=stamp, attempted_at=stamp)
        except Exception:
            result = replace(cached, status='stale' if cached.prices else 'unavailable', attempted_at=self.now().isoformat(),
                             note='Refresh failed; showing cached market prices.' if cached.prices else 'Market-price history could not be loaded. Try again later.')
        if path:
            try:
                from portfolio_app.workspace.workspace_lock import workspace_lock, document_workspace
                with workspace_lock(document_workspace(path)):
                    if _history_version(path) != version:
                        latest, _ = self.cached(ticker, period)
                        # Honor replacements and deletions. A concurrent failed
                        # attempt with no prices can still accept a successful fetch.
                        if latest.prices or not latest.attempted_at or result.status != 'fresh' or not result.prices:
                            return latest
                    if not result.prices and isinstance(version, tuple):
                        # A transient read error on first access may have hidden
                        # valid saved prices. Never publish an empty result over them.
                        try:
                            saved, _ = self._cached_value((ticker, period), path, version)
                        except OSError:
                            return result
                        if saved.prices:
                            result = replace(saved, status='stale', attempted_at=result.attempted_at,
                                             note='Refresh failed; showing cached market prices.')
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with staged_text(path, lambda handle: json.dump(vars(result), handle)) as temporary:
                        temporary.replace(path)
                    self._memory.pop((ticker, period), None)
                    return result
            except OSError:
                result = replace(result, note="History loaded, but its cache could not be saved.")
        self._memory[ticker, period] = version, result
        return result


# Daily providers emit ISO calendar dates or timestamps with a local clock.
# Only strip a clock that is well formed. Older formats use the original parser.
_ISO_DAILY = re.compile(
    r'[0-9]{4}-[0-9]{2}-[0-9]{2}'
    r'(?:T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]{1,9})?'
    r'(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])?)?'
)


def _trading_day(stamp):
    # Taking the provider's local date preserves the trading day across DST.
    if isinstance(stamp, str) and _ISO_DAILY.fullmatch(stamp):
        try:
            return date.fromisoformat(stamp[:10])
        except ValueError:
            pass
    return pd.Timestamp(stamp).date()


def _trading_dates(dates):
    return pd.DatetimeIndex([_trading_day(stamp) for stamp in dates])


def convert_price_history(result, target, legs):
    """Convert unadjusted closes on dates with available historical FX."""
    if result.currency == target:
        return result
    prices = pd.Series(result.prices, index=_trading_dates(result.dates))
    statuses = [result.status]
    for currency, inverse in ((result.currency, False), (target, True)):
        if currency == 'EUR':
            continue
        fx = legs.get(currency)
        if fx is None or not fx.prices or fx.currency != 'EUR':
            return HistoryResult(currency=target, note='Historical FX unavailable or loading. Native-currency history remains available.')
        rates = pd.Series(fx.prices, index=_trading_dates(fx.dates))
        prices = prices / rates.reindex(prices.index) if inverse else prices * rates.reindex(prices.index)
        statuses.append(fx.status)
    prices = prices.replace([float('inf'), -float('inf')], float('nan')).dropna()
    dates = prices.index.to_numpy(dtype='datetime64[s]').astype(str).tolist()
    return replace(result, dates=tuple(dates), prices=tuple(prices), currency=target,
                   status='stale' if 'stale' in statuses else result.status,
                   note='Dates without historical FX are omitted.' if len(prices) < len(result.prices) else result.note)
