"""Read dated fundamentals immediately and refresh distinct listings in the shared worker pool."""
from functools import partial
from threading import Lock
from weakref import WeakValueDictionary

from portfolio_app.analytics.analytics_service import compose_metrics
from portfolio_app.analytics.fundamentals import FundamentalsService, YahooFundamentalsProvider
from portfolio_app.market.market_data import coordinator
from portfolio_app.workspace.workspace_lock import workspace_context, write_context


class BackgroundFundamentals:
    def __init__(self, directory, service, requests, context=None):
        self.directory, self.service, self.requests = directory, service, requests
        self.context = context
        self.workspace = (directory, context)
        self._lock = Lock()
        self._previous = {}
        self._revision = 0

    def _sync_batch(self):
        revision, pending = self.requests.channel_state(self.workspace, 'fundamentals')
        if not pending:
            self._previous.clear()
            self._revision = revision

    def _fetch(self, ticker, previous, force):
        if self.context:
            from portfolio_app.workspace.workspace_selection import selection
            launch, expected = self.context
            if selection(launch) != expected:
                return False
        token = write_context.set(self.context)
        try:
            self.service.get(ticker, refresh=force)
            return self.service.cached(ticker)[0] != previous
        finally:
            write_context.reset(token)

    def _submit(self, due, force):
        if not due:
            return
        new = {ticker: value for ticker, value in due.items() if ticker not in self._previous}
        work = [(('fundamentals', ticker), partial(self._fetch, ticker, value, force)) for ticker, value in new.items()]
        if self.requests.request_group(self.workspace, work, channel='fundamentals', capture=None):
            self._previous.update(new)
            due.clear()

    def read(self, holdings, *, refresh=False):
        with self._lock:
            self._sync_batch()
            revision = self._revision
            due = {}
            waiting_on_listing = False

            def listing(ticker):
                nonlocal waiting_on_listing
                ticker = ticker.strip().upper()
                if ticker in self._previous:
                    waiting_on_listing = True
                    return self._previous[ticker]
                snapshot, expired = self.service.cached(ticker)
                if ticker and (expired or refresh):
                    due[ticker] = snapshot
                return snapshot

            snapshots = compose_metrics(holdings, self.directory, listing)
            if not due and not waiting_on_listing:
                return snapshots, None
            self._submit(due, refresh)

        corrected = False

        def completed():
            """None while queued or fetching, then whether the mounted values need replacing."""
            nonlocal corrected
            with self._lock:
                if due:
                    self._sync_batch()
                    for ticker, previous in list(due.items()):
                        if ticker in self._previous:
                            del due[ticker]
                            continue
                        snapshot, expired = self.service.cached(ticker)
                        if not expired and (not refresh or snapshot != previous):
                            corrected |= snapshot != previous
                            del due[ticker]
                    self._submit(due, refresh)
                latest, pending = self.requests.channel_state(self.workspace, 'fundamentals')
                return None if due or pending else corrected or latest != revision

        return snapshots, completed


_readers = WeakValueDictionary()
_readers_lock = Lock()


def workspace_fundamentals(directory):
    directory = directory.resolve()
    context = workspace_context(directory)
    key = (directory, context)
    with _readers_lock:
        reader = _readers.get(key)
        if reader is None:
            service = FundamentalsService(YahooFundamentalsProvider(), directory / '.cache' / 'fundamentals')
            reader = _readers[key] = BackgroundFundamentals(directory, service, coordinator, context)
        return reader
