"""Nonblocking market-data requests. Workers never access Streamlit state."""
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
from pathlib import Path
from threading import Condition, RLock

from portfolio_app.market.history import HistoryService
from portfolio_app.market.msci_history import MarketHistoryProvider
from portfolio_app.market.gold_prices import SpotGoldProvider
from portfolio_app.market.prices import PriceService, YahooProvider


class RequestCoordinator:
    """Bound outstanding requests, count a portfolio batch once, and share workers fairly."""
    def __init__(self, workers=4, capacity=256):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='market-data')
        self._lock = RLock()
        self._idle = Condition(self._lock)
        self._pending = set()
        self._queue = OrderedDict()
        self._batch_sizes = {}
        self._workers = workers
        self._running = 0
        self._closed = False
        self._revisions = {}
        self._channel_revisions = {}
        self._channel_sizes = {}
        self._snapshots = {}
        self._errors = {}
        self.capacity = capacity

    def request(self, workspace, key, work, *, channel=None, capture=None):
        identity = str(workspace), key
        with self._lock:
            if identity in self._pending:
                return False
            return self._enqueue(workspace, [(key, work)], identity, channel, capture)

    def request_group(self, workspace, requests, *, channel, capture):
        """Admit all newly discovered legs together, even if they outnumber free workers."""
        batch = str(workspace), channel, capture
        with self._lock:
            return self._enqueue(workspace, requests, batch, channel, capture)

    def _enqueue(self, workspace, requests, batch, channel, capture):
        workspace = str(workspace)
        missing = {key: work for key, work in requests if (workspace, key) not in self._pending}
        if not missing:
            return True
        if self._closed or (batch not in self._batch_sizes and len(self._batch_sizes) >= self.capacity):
            return False
        group = (workspace, channel) if channel is not None else None
        if group is not None:
            self._channel_sizes[group] = self._channel_sizes.get(group, 0) + len(missing)
            if capture is not None:
                snapshots = self._snapshots.setdefault(group, {})
                if capture not in snapshots:
                    snapshots[capture] = self._channel_revisions.get(group, 0), capture()
        queue = self._queue.setdefault(batch, deque())
        self._batch_sizes[batch] = self._batch_sizes.get(batch, 0) + len(missing)
        for key, work in missing.items():
            identity = workspace, key
            self._pending.add(identity)
            queue.append((copy_context(), identity, group, work))
        self._dispatch()
        return True

    def _dispatch(self):
        while self._queue and self._running < self._workers:
            batch, queue = self._queue.popitem(last=False)
            context, identity, group, work = queue.popleft()
            if queue:
                self._queue[batch] = queue
            self._running += 1
            self._pool.submit(context.run, self._run, identity, group, batch, work)

    def _run(self, identity, group, batch, work):
        try:
            changed = work()
            error = ''
        except Exception as exc:
            changed = False
            error = str(exc) or type(exc).__name__
        with self._lock:
            if group is not None:
                if changed is not False:
                    self._channel_revisions[group] = self._channel_revisions.get(group, 0) + 1
                self._channel_sizes[group] -= 1
                if not self._channel_sizes[group]:
                    del self._channel_sizes[group]
                    self._snapshots.pop(group, None)
            self._batch_sizes[batch] -= 1
            if not self._batch_sizes[batch]:
                del self._batch_sizes[batch]
            self._pending.remove(identity)
            self._errors[identity] = error
            self._revisions[identity[0]] = self._revisions.get(identity[0], 0) + 1
            self._running -= 1
            self._dispatch()
            if not self._pending:
                self._idle.notify_all()

    def pending(self, workspace, key=None):
        with self._lock:
            if key is not None:
                return (str(workspace), key) in self._pending
            return any(w == str(workspace) for w, _ in self._pending)

    def revision(self, workspace):
        with self._lock:
            return self._revisions.get(str(workspace), 0)

    def channel_state(self, workspace, channel):
        """Material revision and outstanding work for one consumer group."""
        key = str(workspace), channel
        with self._lock:
            return self._channel_revisions.get(key, 0), key in self._channel_sizes

    def channel_snapshot(self, workspace, channel, capture):
        """Read one complete revision, including when another window starts mid-refresh."""
        group = str(workspace), channel
        with self._lock:
            pinned = self._snapshots.get(group, {}).get(capture)
            if pinned is not None:
                revision, reader = pinned
                return revision, reader, True
            return self._channel_revisions.get(group, 0), capture(), group in self._channel_sizes

    def close(self):
        with self._idle:
            self._closed = True
            self._idle.wait_for(lambda: not self._pending)
        self._pool.shutdown(wait=True)


class BackgroundPrices:
    """PriceService-compatible cached reader; fetches run on the coordinator."""
    def __init__(self, service, coordinator, workspace):
        self.service, self.coordinator, self.workspace = service, coordinator, str(workspace)
        self.now = service.now
        self.display_revision, self._cached, _ = coordinator.channel_snapshot(
            self.workspace, 'valuation', service.cached_reader)
        self._results = {}
        self._deferred = None
        self._waiting = []

    @property
    def cache_warning(self):
        return self.service.cache_warning

    def still_current(self):
        """Reuse mounted quotes while any due replacements are already being fetched."""
        revision, cached, pending = self.coordinator.channel_snapshot(
            self.workspace, 'valuation', self.service.cached_reader)
        if revision != self.display_revision:
            return False
        for key, previous in self._results.items():
            result, due = cached(key)
            if result != previous or (due and not pending and not self._waiting):
                return False
        return True

    @contextmanager
    def collect_requests(self):
        """Discover the price and currency legs for this valuation before submitting them."""
        requests = []
        self._deferred = requests
        try:
            yield
        finally:
            self._deferred = None
            self._waiting.extend(requests)
            self.submit_waiting()

    def submit_waiting(self):
        """Retry a whole valuation rejected while every request slot was occupied."""
        if self._waiting and self.coordinator.request_group(
                self.workspace, self._waiting, channel='valuation', capture=self.service.cached_reader):
            self._waiting.clear()
        return bool(self._waiting)

    def _read(self, kind, value, refresh):
        key = f'{kind}:{value}'
        if key in self._results:
            return self._results[key]
        result, due = self._cached(key)
        self._results[key] = result
        if due or refresh:
            current, live_due = self.service.cached(key)
            if not live_due and not refresh:
                return result
            def update():
                getattr(self.service, kind)(value, refresh=refresh)
                return self.service.cached(key)[0] != current
            if self._deferred is None:
                self.coordinator.request(self.workspace, key, update, channel='valuation',
                                         capture=self.service.cached_reader)
            else:
                self._deferred.append((key, update))
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
                HistoryService(MarketHistoryProvider(), cache / 'history'),
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
