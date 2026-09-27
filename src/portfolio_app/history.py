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

PERIODS = {"1M": "1mo", "6M": "6mo", "1Y": "1y", "5Y": "5y", "Max": "max"}
SUBUNITS = {"GBp": ("GBP", .01), "GBX": ("GBP", .01), "ZAc": ("ZAR", .01), "ILA": ("ILS", .01)}


@dataclass(frozen=True)
class HistoryResult:
    dates: tuple[str, ...] = ()
    prices: tuple[float, ...] = ()
    currency: str = ""
    status: str = "unavailable"
    fetched_at: str = ""
    note: str = ""


class HistoryProvider(Protocol):
    def history(self, ticker: str, period: str) -> HistoryResult: ...


def normalize_history(frame, currency: str) -> HistoryResult:
    currency, factor = SUBUNITS.get(currency, (currency, 1.))
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
        import yfinance as yf
        instrument = yf.Ticker(ticker)
        frame = instrument.history(period=period, interval="1d", auto_adjust=False,
                                   actions=False, timeout=10, raise_errors=True)
        return normalize_history(frame, instrument.history_metadata.get("currency", ""))


class DemoHistoryProvider:
    """Deterministic invented prices; never fetches or substitutes live data."""
    def history(self, ticker, period):
        count = {"1mo": 30, "6mo": 180, "1y": 365, "5y": 1825, "max": 2200}[period]
        dates = pd.date_range(end="2026-09-01", periods=count)
        frame = pd.DataFrame({"Close": [80 + i * .03 + 3 * math.sin(i / 12) for i in range(count)]}, index=dates)
        return replace(normalize_history(frame, "EUR"), note="Synthetic demo price history")


class HistoryService:
    def __init__(self, provider: HistoryProvider, cache_dir: Path | None = None, *, now=None):
        self.provider, self.cache_dir = provider, cache_dir
        self.now = now or (lambda: datetime.now(timezone.utc))

    def get(self, ticker: str, period: str = "1Y", *, manual=False) -> HistoryResult:
        if not ticker or manual:
            return HistoryResult(note="Market-price history is unavailable for this instrument.")
        if period not in PERIODS:
            raise ValueError("Unsupported history period.")
        path = self.cache_dir / (sha256(f"{ticker}:{period}:close-v1".encode()).hexdigest() + ".json") if self.cache_dir else None
        cached = None
        try:
            if path and path.exists():
                raw = json.loads(path.read_text())
                cached = HistoryResult(**raw)
                if (not cached.dates or len(cached.dates) != len(cached.prices)
                        or any(not math.isfinite(v) or v <= 0 for v in cached.prices)):
                    raise ValueError("Invalid history cache")
                if self.now() - datetime.fromisoformat(cached.fetched_at) < timedelta(hours=1):
                    return replace(cached, status="cached")
        except (OSError, ValueError, TypeError):
            cached = None
        try:
            result = replace(self.provider.history(ticker, PERIODS[period]), fetched_at=self.now().isoformat())
        except Exception:
            if cached:
                return replace(cached, status="stale", note="Refresh failed; showing cached market prices.")
            return HistoryResult(note="Market-price history could not be loaded. Try again later.")
        if path:
            temporary = None
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
                    temporary = Path(handle.name)
                    json.dump(vars(result), handle)
                temporary.replace(path)
            except OSError:
                result = replace(result, note="History loaded, but its cache could not be saved.")
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
        return result
