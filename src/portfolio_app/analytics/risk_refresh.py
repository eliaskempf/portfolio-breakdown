"""One background calculation publishes a complete risk result for its selection."""
from concurrent.futures import Future
from functools import partial
from threading import RLock
from time import monotonic
from weakref import WeakKeyDictionary, WeakValueDictionary, ref

from portfolio_app.analytics.analytics_cache import AnalyticsCache
from portfolio_app.market.market_data import coordinator
from portfolio_app.workspace.workspace_lock import write_context


class RiskRefresh:
    def __init__(self, workspace, selection, previous, calculate, force):
        self.workspace, self.selection = workspace, selection
        self.previous, self.force = previous, force
        self._calculate = calculate
        self.result = Future()
        self._prior = previous.histories.pending_inputs() if force else {}

    def submit(self):
        # Queue capacity can be temporarily occupied by another workspace.
        # Submission is idempotent while the view's timer is mounted.
        with _lock:
            if not self.result.done():
                target = ref(self)
                coordinator.request_group(self.workspace, [(('risk', target), partial(_run_pending, target))],
                                          channel='risk', capture=None)

    def _remember(self, key, previous, due):
        with _lock:
            entry = self._prior.setdefault(key, (previous, due))
            for workspace, pinned in _readers.values():
                if workspace == self.workspace:
                    pinned.setdefault(key, entry)

    def _run(self):
        try:
            context = self.workspace[1]
            if context:
                from portfolio_app.workspace.workspace_selection import selection
                launch, expected = context
                if selection(launch) != expected:
                    self.result.cancel()
                    return False
            token = write_context.set(context)
            try:
                snapshot = self._calculate(before_fetch=self._remember, previous=self.previous)
            finally:
                write_context.reset(token)
            _set_failed(self.workspace, self.selection, False)
            self.result.set_result(snapshot)
            return True
        except Exception as exc:
            _set_failed(self.workspace, self.selection, True)
            self.result.set_exception(exc)
            return False
        finally:
            with _lock:
                _active.pop(self, None)


def _run_pending(target):
    job = target()
    return job._run() if job is not None else False


_lock = RLock()
_active = WeakKeyDictionary()
_readers = WeakKeyDictionary()
_caches = WeakValueDictionary()
_failures = WeakKeyDictionary()


def _set_failed(workspace, selection, failed):
    with _lock:
        cache = _caches.get(workspace)
        if cache is not None:
            now = monotonic()
            others = [(s, end) for s, end in _failures.get(cache, ()) if end > now and not s.matches(selection)]
            if failed:
                others.append((selection, now + 15 * 60))
            _failures[cache] = others[-16:]


def retry_deferred(cache, selection):
    with _lock:
        return any(end > monotonic() and s.matches(selection) for s, end in _failures.get(cache, ()))


def workspace_history_cache(workspace):
    with _lock:
        cache = _caches.get(workspace)
        if cache is None:
            cache = _caches[workspace] = AnalyticsCache(workspace[0] / '.cache' / 'risk-history')
        return cache


def cached_history_reader(workspace):
    # A second selection can open after the worker has saved some of its legs.
    # Pin the pre-refresh evidence for those keys until this calculation finishes.
    pinned = {}

    def read(cache, key):
        with _lock:
            if key in pinned:
                return pinned[key]
        latest = cache.cached(key)
        with _lock:
            return pinned.get(key, latest)

    with _lock:
        for job in _active:
            if job.workspace == workspace:
                for key, entry in job._prior.items():
                    pinned.setdefault(key, entry)
        _readers[read] = workspace, pinned
    return read


def active_risk_refresh(workspace, selection, *, force):
    with _lock:
        return next((job for job in _active if job.workspace == workspace and job.selection.matches(selection)
                     and (job.force or not force)), None)


def start_risk_refresh(workspace, selection, previous, calculate, *, force):
    with _lock:
        existing = active_risk_refresh(workspace, selection, force=force)
        if existing is not None:
            return existing
        job = RiskRefresh(workspace, selection, previous, calculate, force)
        _active[job] = None
        if force:
            for owner, pinned in _readers.values():
                if owner == workspace:
                    for key, entry in job._prior.items():
                        pinned.setdefault(key, entry)
    job.submit()
    return job
