"""Nonblocking market-data requests. Workers never access Streamlit state."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path
from threading import RLock

from portfolio_app.history import HistoryService, YahooHistoryProvider
from portfolio_app.gold_prices import SpotGoldProvider
from portfolio_app.prices import PriceService, YahooProvider


class RequestCoordinator:
    """Bounded workers and queue, with one in-flight request per workspace/key."""
    def __init__(self, workers=4, capacity=256):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='market-data')
        self._lock = RLock()
        self._pending = {}
        self._revisions = {}
        self._errors = {}
        self.capacity = capacity

    def request(self, workspace, key, work):
        identity = (str(workspace), key)
        with self._lock:
            if identity in self._pending or len(self._pending) >= self.capacity:
                return False
            # Hold the lock until registration is complete, even for instant jobs.
            self._pending[identity] = self._pool.submit(copy_context().run, self._run, identity, work)
        return True

    def _run(self, identity, work):
        # Synchronize with submission before work can finish.
        with self._lock:
            pass
        try:
            work()
            error = ''
        except Exception as exc:
            error = str(exc) or type(exc).__name__
        with self._lock:
            self._pending.pop(identity, None)
            self._errors[identity] = error
            self._revisions[identity[0]] = self._revisions.get(identity[0], 0) + 1

    def pending(self, workspace, key=None):
        with self._lock:
            return any(w == str(workspace) and (key is None or k == key) for w, k in self._pending)

    def revision(self, workspace):
        with self._lock:
            return self._revisions.get(str(workspace), 0)

    def close(self):
        self._pool.shutdown(wait=True, cancel_futures=True)


class BackgroundPrices:
    """PriceService-compatible cached reader; fetches run on the coordinator."""
    def __init__(self, service, coordinator, workspace):
        self.service, self.coordinator, self.workspace = service, coordinator, str(workspace)
        self.now = service.now
        self._requested = set()
        self._results = {}

    @property
    def cache_warning(self):
        return self.service.cache_warning

    def _read(self, kind, value, refresh):
        key = f'{kind}:{value}'
        if key in self._results:
            return self._results[key]
        result, due = self.service.cached(key)
        self._results[key] = result
        if (due or refresh) and key not in self._requested:
            self._requested.add(key)
            self.coordinator.request(self.workspace, key,
                lambda: getattr(self.service, kind)(value, refresh=refresh))
        return result

    def price(self, ticker, *, refresh=False):
        return self._read('price', ticker, refresh)

    def fx(self, currency, *, refresh=False):
        if currency == 'EUR':
            return self.service.fx(currency)
        return self._read('fx', currency, refresh)


class BackgroundHistory:
    def __init__(self, service, coordinator, workspace):
        self.service, self.coordinator, self.workspace = service, coordinator, str(workspace)

    def get(self, ticker, period='1Y', *, manual=False, refresh=False):
        result, due = self.service.cached(ticker, period, manual=manual)
        key = ('history', ticker, period)
        if ticker and not manual and (due or refresh):
            self.coordinator.request(self.workspace, key,
                lambda: self.service.get(ticker, period, refresh=refresh))
        return result

    def pending(self, ticker, period):
        return self.coordinator.pending(self.workspace, ('history', ticker, period))


coordinator = RequestCoordinator()
_services = {}
_services_lock = RLock()


def services(data_dir):
    """One cache writer per workspace in this process; disk writes also lock."""
    workspace = str(Path(data_dir).resolve())
    with _services_lock:
        if workspace not in _services:
            cache = Path(workspace) / '.cache'
            _services[workspace] = (
                PriceService(SpotGoldProvider(YahooProvider(provider_cache())), cache / 'prices.json'),
                HistoryService(YahooHistoryProvider(), cache / 'history'),
            )
        return _services[workspace]


def prices_for(data_dir):
    return BackgroundPrices(services(data_dir)[0], coordinator, Path(data_dir).resolve())


def history_for(data_dir):
    return BackgroundHistory(services(data_dir)[1], coordinator, Path(data_dir).resolve())


def provider_cache():
    # yfinance owns SQLite transactions and a process-global cache location.
    # It is provider runtime state, not a portfolio document or snapshot input.
    import os
    from portfolio_app.settings import state_path
    return state_path() / 'provider-cache' / str(os.getpid())
