"""Injectable market data with a small persistent cache and stale fallback."""

from functools import cache, cached_property
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
from portfolio_app.workspace.atomic_file import staged_text
from threading import RLock
from typing import Callable, Protocol

from portfolio_app.market.currencies import quote_unit
from portfolio_app.market.listing_quality import quote_issue

UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(UTC)


def parse_time(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Market-data timestamps must include a timezone.")
    return result.astimezone(UTC)


@dataclass(frozen=True)
class Quote:
    price: float
    currency: str
    observed_at: datetime

    def __post_init__(self) -> None:
        if not math.isfinite(self.price) or self.price <= 0:
            raise ValueError("A quote must have a finite positive price.")
        if len(self.currency) != 3 or not self.currency.isalpha() or not self.currency.isupper():
            raise ValueError("A quote must have a three-letter currency.")
        if self.observed_at.tzinfo is None:
            raise ValueError("A quote timestamp must include a timezone.")


@dataclass(frozen=True)
class PriceResult:
    quote: Quote | None
    status: str
    error: str = ""


class QuoteSnapshot:
    """Read each requested quote or FX leg once during a valuation or monitoring pass."""
    def __init__(self, service):
        self._service = service

    def now(self):
        return self._service.now()

    @cached_property
    def price(self):
        return cache(self._service.price)

    @cached_property
    def fx(self):
        return cache(self._service.fx)


class MarketDataProvider(Protocol):
    def price(self, ticker: str) -> Quote: ...

    def fx(self, currency: str) -> Quote: ...


class UnavailableProvider:
    """Allow editing when an explicitly offline demo has no price fixture."""
    def price(self, ticker: str) -> Quote:
        raise ValueError('No offline price fixture is available.')

    def fx(self, currency: str) -> Quote:
        raise ValueError('No offline FX fixture is available.')


class YahooProvider:
    """Provider details stay here; FX quotes mean EUR per foreign currency unit."""

    def __init__(self, cache_dir: Path | None = None):
        if cache_dir is not None:
            import yfinance as yf

            yf.set_tz_cache_location(str(cache_dir))

    def _latest(self, ticker: str, currency: str | None = None) -> Quote:
        import yfinance as yf

        instrument = yf.Ticker(ticker)
        history = instrument.history(period="5d", auto_adjust=False, timeout=10, raise_errors=True)
        closes = history["Close"].dropna() if "Close" in history else []
        if len(closes) == 0:
            raise ValueError(f"No recent price returned for {ticker}.")
        units = currency or instrument.history_metadata.get("currency")
        if not units:
            raise ValueError(f"Quote currency unavailable for {ticker}.")
        # Yahoo uses case-sensitive currency codes for some exchange subunits.
        units, factor = quote_unit(units)
        observed = closes.index[-1].to_pydatetime()
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=UTC)
        return Quote(float(closes.iloc[-1]) * factor, units, observed.astimezone(UTC))

    def price(self, ticker: str) -> Quote:
        if issue := quote_issue(ticker):
            raise ValueError(issue)
        return self._latest(ticker)

    def fx(self, currency: str) -> Quote:
        return self._latest(f"{currency}EUR=X", currency="EUR")


class StaticProvider:
    """Explicit offline demo/test input; never a fallback for live prices."""

    def __init__(self, path: Path):
        self.data = json.loads(path.read_text(encoding="utf-8"))

    def _quote(self, section: str, key: str) -> Quote:
        record = self.data[section][key]
        return Quote(float(record["price"]), record["currency"], parse_time(record["observed_at"]))

    def price(self, ticker: str) -> Quote:
        return self._quote("prices", ticker)

    def fx(self, currency: str) -> Quote:
        return self._quote("fx", currency)


class PriceService:
    def __init__(
        self,
        provider: MarketDataProvider,
        cache_path: Path | None = None,
        *,
        now: Callable[[], datetime] = utc_now,
        ttl: timedelta = timedelta(minutes=15),
    ):
        self.provider = provider
        self.cache_path = cache_path
        self.now = now
        self.ttl = ttl
        self.cache_warning = ""
        self._lock = RLock()
        self._snapshot_reader = None
        self._pending_writes = {}
        self._sequence = 0
        self._saved_versions = {}
        self._entries: dict = {}
        if cache_path and cache_path.exists():
            try:
                loaded = json.loads(cache_path.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise ValueError("Expected an object")
                self._entries = loaded
            except (OSError, ValueError) as exc:
                self.cache_warning = f"Price cache could not be read; fetching fresh data: {exc}"

    def take_cache_write(self):
        """Detach complete background evidence without file I/O under the coordinator lock."""
        with self._lock:
            if not self._pending_writes:
                return None
            pending, self._pending_writes = self._pending_writes, {}

        def publish():
            from portfolio_app.workspace.workspace_lock import write_context
            for context, entries in pending.items():
                token = write_context.set(context)
                try:
                    self._save(entries)
                finally:
                    write_context.reset(token)
        return publish

    def _save(self, entries) -> None:
        from portfolio_app.portfolio.holdings import DataError
        try:
            from portfolio_app.workspace.locking import write_lock as _write_lock
            from portfolio_app.workspace.workspace_lock import workspace_lock, document_workspace
            with workspace_lock(document_workspace(self.cache_path)):
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                with _write_lock(self.cache_path.with_suffix('.lock')):
                    # A newer synchronous fetch can finish before a detached batch takes the lock.
                    changes = {key: (version, entry) for key, (version, entry) in entries.items()
                               if version > self._saved_versions.get(key, 0)}
                    if not changes:
                        return
                    try:
                        saved = json.loads(self.cache_path.read_text())
                        if not isinstance(saved, dict):
                            saved = {}
                    except (OSError, ValueError):
                        saved = {}
                    saved.update((key, entry) for key, (_, entry) in changes.items())
                    with staged_text(self.cache_path, lambda handle: json.dump(saved, handle)) as temporary:
                        temporary.replace(self.cache_path)
                    self._saved_versions.update((key, version) for key, (version, _) in changes.items())
        except (OSError, DataError) as exc:
            self.cache_warning = f"Price cache could not be saved: {exc}"

    def cached(self, key: str) -> tuple[PriceResult, bool]:
        """Read without network access; the boolean indicates a refresh is due."""
        with self._lock:
            entry = self._entries.get(key)
        return self._cached_entry(key, entry)

    def cached_reader(self):
        """Pin the complete in-memory evidence; quote age is still evaluated at read time."""
        with self._lock:
            if self._snapshot_reader is None:
                # Writers replace entire entries. Readers can safely share their contents.
                entries = self._entries.copy()
                def read(key):
                    return self._cached_entry(key, entries.get(key))
                self._snapshot_reader = read
            return self._snapshot_reader

    def _cached_entry(self, key, entry):
        if key.startswith('price:') and (issue := quote_issue(key.removeprefix('price:'))):
            return PriceResult(None, 'missing', issue), False
        if entry is None:
            return PriceResult(None, 'missing'), True
        cached = None
        attempted_at = None
        error = ""
        try:
            if entry.get("quote"):
                record = entry["quote"]
                cached = Quote(float(record["price"]), record["currency"], parse_time(record["observed_at"]))
            attempted_at = parse_time(entry["attempted_at"])
            error = str(entry.get("error", ""))
        except (KeyError, TypeError, ValueError, AttributeError):
            pass
        due = not (attempted_at and timedelta(0) <= self.now() - attempted_at < self.ttl)
        status = 'cached fallback' if cached and error else 'stale' if cached and due else 'cached' if cached else 'missing'
        return PriceResult(cached, status, error), bool(due)

    def _get(self, key: str, fetch: Callable[[], Quote], refresh: bool, defer_save: bool) -> PriceResult:
        previous, due = self.cached(key)
        if not refresh and not due:
            return previous
        cached = previous.quote
        try:
            quote = fetch()
            record = asdict(quote)
            record["observed_at"] = quote.observed_at.isoformat()
            entry = {"quote": record, "attempted_at": self.now().isoformat(), "error": ""}
            result = PriceResult(quote, "fresh")
        except Exception as exc:
            # Market providers raise several exception types. Failure is isolated per instrument.
            error = str(exc) or type(exc).__name__
            record = asdict(cached) if cached else None
            if record:
                record["observed_at"] = cached.observed_at.isoformat()
            entry = {"quote": record, "attempted_at": self.now().isoformat(), "error": error}
            result = PriceResult(cached, "cached fallback" if cached else "missing", error)
        with self._lock:
            self._entries[key] = entry
            self._snapshot_reader = None
            if self.cache_path is None:
                return result
            self._sequence += 1
            update = self._sequence, entry
            if defer_save:
                from portfolio_app.workspace.workspace_lock import write_context
                self._pending_writes.setdefault(write_context.get(), {})[key] = update
                return result
        # Persistence may wait on another process's writer; cached UI reads
        # must stay available while that happens.
        self._save({key: update})
        return result

    def price(self, ticker: str, *, refresh: bool = False, defer_save: bool = False) -> PriceResult:
        if issue := quote_issue(ticker):
            return PriceResult(None, 'missing', issue)
        return self._get(f"price:{ticker}", lambda: self.provider.price(ticker), refresh, defer_save)

    def fx(self, currency: str, *, refresh: bool = False, defer_save: bool = False) -> PriceResult:
        if currency == "EUR":
            return PriceResult(Quote(1.0, "EUR", self.now()), "identity")
        return self._get(f"fx:{currency}", lambda: self.provider.fx(currency), refresh, defer_save)
