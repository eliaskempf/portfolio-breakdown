"""On-demand market-price history, separate from personal investment returns."""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Protocol

import pandas as pd

from portfolio_app.currencies import quote_unit
from portfolio_app.listing_quality import quote_issue

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


class HistoryService:
    def __init__(self, provider: HistoryProvider, cache_dir: Path | None = None, *, now=None):
        self.provider, self.cache_dir = provider, cache_dir
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._memory = {}

    def _path(self, ticker, period):
        return self.cache_dir / (sha256(f"{ticker}:{period}:close-v1".encode()).hexdigest() + ".json") if self.cache_dir else None

    def cached(self, ticker: str, period: str = "1Y", *, manual=False) -> tuple[HistoryResult, bool]:
        """Read saved history immediately, including a short failed-attempt cooldown."""
        if issue := quote_issue(ticker):
            return HistoryResult(note=issue), False
        if not ticker or manual:
            return HistoryResult(note="Market-price history is unavailable for this instrument."), False
        if period not in PERIODS:
            raise ValueError("Unsupported history period.")
        path = self._path(ticker, period)
        cached = self._memory.get((ticker, period))
        try:
            if cached is None and path and path.exists():
                raw = json.loads(path.read_text())
                raw['dates'] = tuple(raw['dates'])
                raw['prices'] = tuple(raw['prices'])
                cached = HistoryResult(**raw)
            if cached:
                if (len(cached.dates) != len(cached.prices)
                        or any(not math.isfinite(v) or v <= 0 for v in cached.prices)):
                    raise ValueError('Invalid history cache')
                if cached.attempted_at and cached.status in {'stale', 'unavailable'}:
                    age = self.now() - datetime.fromisoformat(cached.attempted_at)
                    if timedelta(0) <= age < timedelta(seconds=60):
                        return cached, False
                if cached.prices:
                    age = self.now() - datetime.fromisoformat(cached.fetched_at)
                    if timedelta(0) <= age < timedelta(hours=1):
                        return replace(cached, status='cached'), False
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            cached = None
        if cached and cached.prices:
            return replace(cached, status='stale', note='Showing saved market prices while refreshing.'), True
        return HistoryResult(note='Loading market prices…'), True

    def get(self, ticker: str, period: str = "1Y", *, manual=False, refresh=False) -> HistoryResult:
        cached, due = self.cached(ticker, period, manual=manual)
        if not ticker or manual or quote_issue(ticker) or (not due and not refresh):
            return cached
        path = self._path(ticker, period)
        try:
            result = replace(self.provider.history(ticker, PERIODS[period]), fetched_at=self.now().isoformat(), attempted_at=self.now().isoformat())
        except Exception:
            result = replace(cached, status='stale' if cached.prices else 'unavailable', attempted_at=self.now().isoformat(),
                             note='Refresh failed; showing cached market prices.' if cached.prices else 'Market-price history could not be loaded. Try again later.')
        self._memory[ticker, period] = result
        if path:
            temporary = None
            try:
                from portfolio_app.workspace_lock import workspace_lock, document_workspace
                with workspace_lock(document_workspace(path)):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with NamedTemporaryFile(mode="w", dir=path.parent, suffix='.tmp', delete=False) as handle:
                        temporary = Path(handle.name)
                        json.dump(vars(result), handle)
                    temporary.replace(path)
            except OSError:
                result = replace(result, note="History loaded, but its cache could not be saved.")
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
        self._memory[ticker, period] = result
        return result


def convert_price_history(result, target, legs):
    """Convert unadjusted closes on dates with available historical FX."""
    if result.currency == target:
        return result
    prices = pd.Series(result.prices, index=pd.DatetimeIndex(result.dates).tz_localize(None).normalize())
    statuses = [result.status]
    for currency, inverse in ((result.currency, False), (target, True)):
        if currency == 'EUR':
            continue
        fx = legs.get(currency)
        if fx is None or not fx.prices or fx.currency != 'EUR':
            return HistoryResult(currency=target, note='Historical FX unavailable or loading. Native-currency history remains available.')
        rates = pd.Series(fx.prices, index=pd.DatetimeIndex(fx.dates).tz_localize(None).normalize())
        prices = prices / rates.reindex(prices.index) if inverse else prices * rates.reindex(prices.index)
        statuses.append(fx.status)
    prices = prices.replace([float('inf'), -float('inf')], float('nan')).dropna()
    return replace(result, dates=tuple(stamp.isoformat() for stamp in prices.index), prices=tuple(prices), currency=target,
                   status='stale' if 'stale' in statuses else result.status,
                   note='Dates without historical FX are omitted.' if len(prices) < len(result.prices) else result.note)
