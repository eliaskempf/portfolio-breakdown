"""Slow providers are controlled with events; no live data or network access."""
from datetime import timedelta
from pathlib import Path
from threading import Event
from time import monotonic

import pytest

from portfolio_app.history import HistoryResult, HistoryService
from portfolio_app.market_data import BackgroundHistory, BackgroundPrices, RequestCoordinator
from portfolio_app.prices import PriceService, Quote


@pytest.fixture
def jobs():
    coordinator = RequestCoordinator(workers=2, capacity=4)
    yield coordinator
    coordinator.close()


def finish(jobs, workspace):
    deadline = monotonic() + 3
    while jobs.pending(workspace):
        assert monotonic() < deadline
        Event().wait(.005)


def test_workspace_aliases_share_pending_requests_and_completion(tmp_path, jobs):
    directory = tmp_path / 'synthetic-workspace'
    directory.mkdir()
    alias = directory / '..' / directory.name
    release = Event()
    try:
        assert jobs.request(alias, 'monitoring-browse', lambda: release.wait(3))
        assert jobs.pending(directory, 'monitoring-browse')
        assert not jobs.request(directory, 'monitoring-browse', lambda: None)
        assert not jobs.pending(tmp_path / 'different-workspace')
    finally:
        release.set()
    finish(jobs, alias)
    assert jobs.revision(directory) == jobs.revision(alias) == 1


def test_slow_quotes_do_not_block_and_requests_are_deduplicated(tmp_path, now, jobs):
    started, release = Event(), Event()
    class Provider:
        calls = 0
        def price(self, ticker):
            self.calls += 1
            started.set()
            assert release.wait(3)
            return Quote(42, 'EUR', now)
    provider = Provider()
    service = PriceService(provider, tmp_path / 'prices.json', now=lambda: now)
    try:
        reader = BackgroundPrices(service, jobs, 'one')
        assert reader.price('SYNTH').quote is None
        assert started.wait(1)
        for _ in range(5):
            assert BackgroundPrices(service, jobs, 'one').price('SYNTH').quote is None
        assert provider.calls == 1
        assert jobs.revision('one') == 0
    finally:
        release.set()
    finish(jobs, 'one')
    assert jobs.revision('one') == 1
    assert BackgroundPrices(service, jobs, 'one').price('SYNTH').quote.price == 42
    assert PriceService(provider, tmp_path / 'prices.json', now=lambda: now).price('SYNTH').quote.price == 42
    assert provider.calls == 1


def test_stale_quotes_survive_failure_and_respect_cooldown(tmp_path, now, jobs):
    clock = [now]
    class Provider:
        fail = False
        calls = 0
        def price(self, ticker):
            self.calls += 1
            if self.fail:
                raise ConnectionError('Synthetic failure')
            return Quote(12, 'USD', now)
    provider = Provider()
    service = PriceService(provider, tmp_path / 'prices.json', now=lambda: clock[0])
    service.price('SYNTH')
    clock[0] += timedelta(minutes=16)
    provider.fail = True
    assert BackgroundPrices(service, jobs, 'one').price('SYNTH').quote.price == 12
    finish(jobs, 'one')
    result = BackgroundPrices(service, jobs, 'one').price('SYNTH')
    assert result.status == 'cached fallback' and result.quote.observed_at == now
    assert provider.calls == 2


def test_history_failure_cooldown_persists_and_retry_can_override(tmp_path, now):
    clock = [now]
    class Provider:
        calls = 0
        def history(self, ticker, period):
            self.calls += 1
            raise ConnectionError('Synthetic failure')
    provider = Provider()
    for _ in range(3):
        assert HistoryService(provider, tmp_path, now=lambda: clock[0]).get('SYNTH').status == 'unavailable'
    assert provider.calls == 1
    clock[0] += timedelta(seconds=61)
    service = HistoryService(provider, tmp_path, now=lambda: clock[0])
    service.get('SYNTH')
    service.get('SYNTH', refresh=True)
    assert provider.calls == 3


def test_history_period_and_workspace_requests_are_isolated(tmp_path, now, jobs):
    release, started = Event(), Event()
    class Provider:
        def history(self, ticker, period):
            started.set()
            assert release.wait(3)
            return HistoryResult(('2026-01-01',), (42.,), 'EUR', 'fresh')
    a = BackgroundHistory(HistoryService(Provider(), tmp_path / 'a', now=lambda: now), jobs, 'a')
    b = BackgroundHistory(HistoryService(Provider(), tmp_path / 'b', now=lambda: now), jobs, 'b')
    try:
        assert not a.get('SYNTH', '1Y').prices
        assert started.wait(1)
        assert not a.get('SYNTH', '1M').prices
        assert not b.get('SYNTH', '1Y').prices
        assert a.pending('SYNTH', '1Y') and a.pending('SYNTH', '1M')
        assert not a.pending('OTHER', '1Y')
    finally:
        release.set()
    finish(jobs, 'a'); finish(jobs, 'b')
    assert a.get('SYNTH').prices and b.get('SYNTH').prices


def test_queue_is_bounded(jobs):
    release = Event()
    try:
        for i in range(4):
            assert jobs.request('one', i, lambda: release.wait(3))
        assert not jobs.request('one', 5, lambda: None)
        assert not jobs.request('one', 0, lambda: None)
    finally:
        release.set()
    finish(jobs, 'one')


def test_concurrent_price_cache_writers_preserve_distinct_keys(tmp_path, now, jobs):
    class Provider:
        def price(self, ticker):
            return Quote(42, 'EUR', now)
    path = tmp_path / 'prices.json'
    for ticker in ('SYNTH-A', 'SYNTH-B'):
        service = PriceService(Provider(), path, now=lambda: now)
        jobs.request('one', ticker, lambda service=service, ticker=ticker: service.price(ticker))
    finish(jobs, 'one')
    restored = PriceService(Provider(), path, now=lambda: now)
    assert not restored.cached('price:SYNTH-A')[1]
    assert not restored.cached('price:SYNTH-B')[1]


def test_history_uses_memory_if_refresh_cannot_be_saved(tmp_path, now, monkeypatch):
    clock = [now]
    class Provider:
        calls = 0
        def history(self, ticker, period):
            self.calls += 1
            return HistoryResult(('2026-01-01',), (42.,), 'EUR', 'fresh')
    provider = Provider()
    service = HistoryService(provider, tmp_path, now=lambda: clock[0])
    service.get('SYNTH')
    clock[0] += timedelta(hours=2)
    def cannot_write(*args):
        raise PermissionError('Synthetic unwritable cache')
    monkeypatch.setattr('pathlib.Path.replace', cannot_write)
    assert 'could not be saved' in service.get('SYNTH').note
    assert service.get('SYNTH').status == 'cached'
    assert provider.calls == 2


def test_history_cooldown_starts_after_a_long_failed_request(now):
    clock = [now]
    class Provider:
        calls = 0
        def history(self, ticker, period):
            self.calls += 1
            clock[0] += timedelta(seconds=90)
            raise TimeoutError('Synthetic timeout')
    provider = Provider()
    service = HistoryService(provider, now=lambda: clock[0])
    service.get('SYNTH')
    service.get('SYNTH')
    assert provider.calls == 1


@pytest.mark.parametrize('kind', ['price', 'history'])
def test_fetched_data_survives_cache_publication_and_cleanup_failure(tmp_path, now, monkeypatch, kind):
    class Provider:
        calls = 0
        def price(self, ticker):
            self.calls += 1
            return Quote(42., 'EUR', now)
        def history(self, ticker, period):
            self.calls += 1
            return HistoryResult(('2026-01-01',), (42.,), 'EUR', 'fresh')
    provider = Provider()
    service = (PriceService(provider, tmp_path / 'prices.json', now=lambda: now) if kind == 'price'
               else HistoryService(provider, tmp_path, now=lambda: now))
    def cannot_replace(*args):
        raise PermissionError('Invented publication failure')
    unlink = Path.unlink
    def cannot_clean_stage(path, *args, **kwargs):
        if path.suffix == '.tmp':
            raise PermissionError('Invented cleanup failure')
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'replace', cannot_replace)
    monkeypatch.setattr(Path, 'unlink', cannot_clean_stage)
    if kind == 'price':
        assert service.price('SYNTH').quote.price == 42.
        assert 'could not be saved' in service.cache_warning
        assert service.price('SYNTH').quote.price == 42.
    else:
        result = service.get('SYNTH')
        assert result.prices == (42.,) and 'could not be saved' in result.note
        assert service.get('SYNTH').prices == (42.,)
    assert provider.calls == 1
