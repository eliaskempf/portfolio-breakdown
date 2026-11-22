"""Synthetic cache-first fundamentals, controllable without a market connection."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from threading import Event, current_thread
from time import monotonic, sleep

import pandas as pd

from portfolio_app.analytics.analytics_cache import AnalyticsCache
from portfolio_app.analytics.fundamental_refresh import BackgroundFundamentals
from portfolio_app.analytics.fundamentals import FundamentalsService, normalize_info
from portfolio_app.market.market_data import RequestCoordinator


def wait_until(check):
    end = monotonic() + 5
    while not check():
        assert monotonic() < end
        sleep(.005)


def test_staggered_listing_refresh_keeps_one_snapshot_across_readers_and_applies_current_holdings(tmp_path):
    releases = {ticker: Event() for ticker in ('AAA', 'BBB')}
    calls = []
    class Provider:
        def fetch(self, ticker):
            calls.append(ticker)
            assert current_thread().name.startswith('market-data')
            assert releases[ticker].wait(5)
            return normalize_info(ticker, {'quoteType': 'EQUITY', 'trailingPE': 20.})

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    seed = FundamentalsService(None, tmp_path, now=lambda: now)
    for ticker in releases:
        seed.cache.get('fundamentals-v1:' + ticker, lambda: asdict(normalize_info(ticker, {'quoteType': 'EQUITY', 'trailingPE': 10.})))
    now += timedelta(days=2)
    service = FundamentalsService(Provider(), tmp_path, now=lambda: now)
    pool = RequestCoordinator(workers=2)
    manager = BackgroundFundamentals(tmp_path, service, pool)
    rows = pd.DataFrame([dict(id='a', ticker='AAA', instrument_type='equity'), dict(id='b', ticker='BBB', instrument_type='equity'),
                         dict(id='a2', ticker='AAA', instrument_type='equity')])
    try:
        initial, done = manager.read(rows)
        assert all(s.metrics['trailing_pe'].value == 10. and s.status == 'stale' for s in initial.values())
        wait_until(lambda: len(calls) == 2)
        releases['AAA'].set()
        wait_until(lambda: not pool.pending(manager.workspace, ('fundamentals', 'AAA')))
        assert done() is None
        revised_rows = rows.assign(instrument_type=['equity', 'etf', 'equity'])
        intermediate, joined = manager.read(revised_rows, refresh=True)
        assert intermediate['a'].metrics['trailing_pe'].value == 10.
        assert intermediate['b'].kind == 'etf' and 'trailing_pe' not in intermediate['b'].metrics
        assert joined() is None
        releases['BBB'].set()
        wait_until(lambda: done() is not None)
        assert done() and joined()
        final, done = manager.read(rows)
        assert done is None and all(s.metrics['trailing_pe'].value == 20. for s in final.values())
        assert sorted(calls) == ['AAA', 'BBB']
        assert FundamentalsService(None, tmp_path, now=lambda: now).cached('AAA')[0].metrics['trailing_pe'].value == 20.
    finally:
        for release in releases.values():
            release.set()
        pool.close()


def test_cache_only_failure_cooldown_survives_failed_write_and_observes_external_repair(tmp_path, monkeypatch):
    now = datetime(2026, 2, 1, tzinfo=timezone.utc)
    cache = AnalyticsCache(tmp_path, now=lambda: now)
    cache.get('test', lambda: {'value': 1})
    now += timedelta(days=2)
    stale, due = cache.cached('test')
    assert due and stale.status == 'stale' and stale.data == {'value': 1}
    def fail(*args):
        raise OSError('Synthetic outage')
    with monkeypatch.context() as m:
        m.setattr('portfolio_app.analytics.analytics_cache.atomic_json', fail)
        assert cache.get('test', fail).status == 'stale'
        retained, due = cache.cached('test')
        assert not due and retained.data == {'value': 1}
        assert retained.note == 'Analytics cache could not be saved.'
    now += timedelta(minutes=1)
    corrected = AnalyticsCache(tmp_path, now=lambda: now)
    corrected.get('test', lambda: {'value': 3}, refresh=True)
    observed, due = cache.cached('test')
    assert not due and observed.data == {'value': 3}


def test_full_queue_retries_without_running_provider_on_the_reader(tmp_path):
    pool = RequestCoordinator(workers=1, capacity=1)
    busy = Event()
    calls = []
    class Provider:
        def fetch(self, ticker):
            calls.append(ticker)
            return normalize_info(ticker, {'quoteType': 'EQUITY', 'trailingPE': 8.})
    manager = BackgroundFundamentals(tmp_path, FundamentalsService(Provider(), tmp_path), pool)
    rows = pd.DataFrame([dict(id='a', ticker='AAA', instrument_type='equity')])
    try:
        pool.request('elsewhere', 'busy', lambda: busy.wait(5))
        snapshots, done = manager.read(rows)
        assert not snapshots['a'].metrics and done() is None and not calls
        busy.set()
        wait_until(lambda: done() is not None)
        assert done() and calls == ['AAA']
        assert manager.read(rows)[0]['a'].metrics['trailing_pe'].value == 8.
    finally:
        busy.set()
        pool.close()


def test_queued_fundamentals_use_selection_captured_by_dialog_before_workspace_switch(tmp_path, monkeypatch):
    from portfolio_app.workspace.workspace_lock import bind_workspace, write_context, workspace_context
    from portfolio_app.workspace.workspace_selection import Selection, save_selection

    monkeypatch.setenv('PORTFOLIO_STATE_DIR', str(tmp_path / 'synthetic-state'))
    directory, other = tmp_path / 'first', tmp_path / 'second'
    directory.mkdir()
    other.mkdir()
    release, started = Event(), Event()
    class Provider:
        def fetch(self, ticker):
            started.set()
            assert release.wait(5)
            return normalize_info(ticker, {'quoteType': 'EQUITY', 'trailingPE': 8.})
    pool = RequestCoordinator(workers=1)
    token = write_context.set(None)
    try:
        bind_workspace(directory, Selection(directory))
        write_context.set(None)  # A later dialog fragment runs on a fresh script thread.
        service = FundamentalsService(Provider(), directory / '.cache' / 'fundamentals')
        owner = BackgroundFundamentals(directory, service, pool, workspace_context(directory))
        owner.read(pd.DataFrame([dict(id='a', ticker='AAA', instrument_type='equity')]))
        assert started.wait(5)
        save_selection(directory, Selection(other, 1))
    finally:
        release.set()
        pool.close()
        write_context.reset(token)
    assert not list(directory.glob('.cache/fundamentals/*.json'))
    assert not list(other.glob('.cache/fundamentals/*.json'))


def test_fundamental_limit_preserves_workers_for_valuations_across_workspaces():
    pool = RequestCoordinator(workers=2, channel_limits={'fundamentals': 1})
    entered, second, quote, release = Event(), Event(), Event(), Event()
    def slow():
        entered.set()
        assert release.wait(5)
    try:
        pool.request('first', 'listing-a', slow, channel='fundamentals')
        assert entered.wait(5)
        pool.request('second', 'listing-b', second.set, channel='fundamentals')
        pool.request('third', 'price', quote.set, channel='valuation')
        assert quote.wait(5) and not second.is_set()
        release.set()
        assert second.wait(5)
    finally:
        release.set()
        pool.close()


def test_deferred_forced_refresh_uses_another_completed_fetch(tmp_path):
    pool = RequestCoordinator(workers=1, capacity=1)
    release = Event()
    calls = []
    class Provider:
        def fetch(self, ticker):
            calls.append(ticker)
            return normalize_info(ticker, {'quoteType': 'EQUITY', 'trailingPE': len(calls)})
    service = FundamentalsService(Provider(), tmp_path)
    service.get('AAA')
    manager = BackgroundFundamentals(tmp_path, service, pool)
    rows = pd.DataFrame([dict(id='a', ticker='AAA', instrument_type='equity')])
    try:
        pool.request('other', 'busy', lambda: release.wait(5))
        snapshots, done = manager.read(rows, refresh=True)
        assert snapshots['a'].metrics['trailing_pe'].value == 1. and done() is None
        service.get('AAA', refresh=True)
        assert done() and calls == ['AAA', 'AAA']
        current, check = manager.read(rows)
        assert check is None and current['a'].metrics['trailing_pe'].value == 2.
    finally:
        release.set()
        pool.close()
