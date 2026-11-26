"""Slow providers are controlled with events; no live data or network access."""
from datetime import timedelta
from pathlib import Path
from threading import Event
from time import monotonic

import pytest

from portfolio_app.market.history import HistoryResult, HistoryService
from portfolio_app.market.market_data import BackgroundHistory, BackgroundPrices, RequestCoordinator
from portfolio_app.market.prices import PriceService, Quote


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


def test_cold_portfolio_groups_price_and_held_acquisition_fx_in_one_publication(tmp_path, now, jobs, monkeypatch):
    from portfolio_app.application import valuation_requests
    from portfolio_app.portfolio.holdings import parse_holdings
    from portfolio_app.portfolio.portfolio import prepare_portfolio

    class Provider:
        def price(self, ticker):
            return Quote(50, 'USD', now)

        def fx(self, currency):
            return Quote({'USD': .8, 'CHF': 1.1}[currency], 'EUR', now)

    groups = []
    submit = jobs.request_group
    def capture(workspace, requests, **options):
        groups.append([key for key, _ in requests])
        return submit(workspace, requests, **options)
    monkeypatch.setattr(jobs, 'request_group', capture)
    holdings = parse_holdings(
        'id,name,ticker,shares,acquisition_price,acquisition_currency\n'
        'one,Invented USD,SYN-ONE,1,40,USD\n'
        'two,Invented CHF,SYN-TWO,1,30,CHF\n'
        'sold,Invented closed,SYN-SOLD,0,1,CAD\n'
        'three,Invented without cost,SYN-THREE,1,,AUD\n')
    prices = PriceService(Provider(), now=lambda: now)
    def value():
        reader = BackgroundPrices(prices, jobs, str(tmp_path))
        with valuation_requests(reader, None):
            return prepare_portfolio(holdings, reader).set_index('id')

    assert value().at['sold', 'current_value_reporting'] == 0
    finish(jobs, tmp_path)
    assert len(groups) == 1
    assert sorted(groups[0]) == ['fx:CHF', 'fx:USD', 'price:SYN-ONE', 'price:SYN-THREE', 'price:SYN-TWO']
    ready = value()
    assert ready.at['one', 'unrealized_gain'] == 10
    assert ready.at['two', 'unrealized_gain'] == pytest.approx(40 / 1.1 - 30)
    assert len(groups) == 1


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
        assert reader.still_current()
        for _ in range(5):
            assert BackgroundPrices(service, jobs, 'one').price('SYNTH').quote is None
        assert provider.calls == 1
        assert jobs.revision('one') == 0
    finally:
        release.set()
    finish(jobs, 'one')
    assert jobs.revision('one') == 1
    assert not reader.still_current()
    assert BackgroundPrices(service, jobs, 'one').price('SYNTH').quote.price == 42
    assert PriceService(provider, tmp_path / 'prices.json', now=lambda: now).price('SYNTH').quote.price == 42
    assert provider.calls == 1


def test_prices_publish_a_complete_refresh_across_windows(tmp_path, now, jobs):
    from unittest.mock import Mock
    events = {kind: Event() for kind in ('price', 'fx')}
    provider = Mock()
    service = PriceService(provider, now=lambda: now)
    provider.price.return_value = Quote(10, 'USD', now)
    provider.fx.return_value = Quote(1, 'EUR', now)
    service.price('SYNTH')
    service.fx('USD')

    def replacement(kind, value):
        assert events[kind].wait(3)
        return value

    provider.price.side_effect = lambda ticker: replacement('price', Quote(12, 'USD', now))
    # An unchanged last completion must still publish the earlier correction.
    provider.fx.side_effect = lambda currency: replacement('fx', Quote(1, 'EUR', now))
    original = BackgroundPrices(service, jobs, tmp_path)
    try:
        with original.collect_requests():
            assert original.price('SYNTH', refresh=True).quote.price == 10
            assert original.fx('USD', refresh=True).quote.price == 1
            assert not jobs.pending(tmp_path)
        events['price'].set()
        deadline = monotonic() + 3
        while jobs.pending(tmp_path, 'price:SYNTH'):
            assert monotonic() < deadline
            Event().wait(.005)
        assert service.cached('price:SYNTH')[0].quote.price == 12
        assert original.still_current()
        second = BackgroundPrices(service, jobs, tmp_path)
        assert second.display_revision == original.display_revision
        assert second.fx('USD').quote.price == 1
        assert second.price('SYNTH').quote.price == 10
        assert second.still_current()
        assert provider.price.call_count == 2
    finally:
        for event in events.values():
            event.set()
    finish(jobs, tmp_path)
    assert not original.still_current() and not second.still_current()
    latest = BackgroundPrices(service, jobs, tmp_path)
    assert latest.display_revision == original.display_revision + 1
    assert latest.price('SYNTH').quote.price == 12
    assert latest.fx('USD').quote.price == 1
    assert latest.still_current()


def test_entire_valuation_retries_on_saturation_and_can_exceed_queue_capacity(tmp_path, now):
    from unittest.mock import Mock
    service = PriceService(Mock(), now=lambda: now)
    service.provider.price.return_value = Quote(10, 'EUR', now)
    tickers = ('SYNTH-A', 'SYNTH-B', 'SYNTH-C')
    for ticker in tickers:
        service.price(ticker)
    unrelated, last, last_started = Event(), Event(), Event()
    jobs = RequestCoordinator(workers=1, capacity=1)
    def updated(ticker):
        if ticker == tickers[-1]:
            last_started.set()
            assert last.wait(3)
        return Quote(12, 'EUR', now)
    service.provider.price.side_effect = updated
    try:
        assert jobs.request('other workspace', 'history', lambda: unrelated.wait(3))
        reader = BackgroundPrices(service, jobs, tmp_path)
        with reader.collect_requests():
            for ticker in tickers:
                assert reader.price(ticker, refresh=True).quote.price == 10
        assert reader.submit_waiting()
        assert not jobs.pending(tmp_path) and reader.still_current()
        unrelated.set()
        finish(jobs, 'other workspace')
        assert not reader.submit_waiting()
        assert last_started.wait(1)
        assert service.cached('price:' + tickers[0])[0].quote.price == 12
        second = BackgroundPrices(service, jobs, tmp_path)
        assert [second.price(t).quote.price for t in tickers] == [10, 10, 10]
        assert reader.still_current() and second.still_current()
        last.set()
        finish(jobs, tmp_path)
        assert not reader.still_current()
        latest = BackgroundPrices(service, jobs, tmp_path)
        assert [latest.price(t).quote.price for t in tickers] == [12, 12, 12]
        assert service.provider.price.call_count == 2 * len(tickers)
    finally:
        unrelated.set()
        last.set()
        jobs.close()


def test_mounted_prices_invalidate_on_quote_change_and_expiry(tmp_path, now, jobs):
    from unittest.mock import Mock
    clock = [now]
    provider = Mock()
    provider.price.return_value = Quote(10, 'EUR', now)
    service = PriceService(provider, now=lambda: clock[0])
    service.price('SYNTH')
    reader = BackgroundPrices(service, jobs, tmp_path)
    reader.price('SYNTH')
    assert reader.still_current()
    clock[0] += service.ttl
    assert not reader.still_current()
    clock[0] = now
    provider.price.return_value = Quote(11, 'EUR', now)
    service.price('SYNTH', refresh=True)
    assert not reader.still_current()
    assert provider.price.call_count == 2


def test_deferred_navigation_retries_a_due_quote_rejected_by_the_queue(now):
    from unittest.mock import Mock
    release = Event()
    jobs = RequestCoordinator(workers=1, capacity=1)
    try:
        assert jobs.request('one', 'other-job', lambda: release.wait(3))
        reader = BackgroundPrices(PriceService(Mock(), now=lambda: now), jobs, 'one')
        assert reader.price('SYNTH').quote is None
        assert not reader.still_current()
    finally:
        release.set()
        jobs.close()


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


def test_restart_sees_one_complete_price_and_fx_publication(tmp_path, now, jobs, monkeypatch):
    from unittest.mock import Mock
    path = tmp_path / '.cache' / 'prices.json'
    provider = Mock()
    service = PriceService(provider, path, now=lambda: now)
    provider.price.return_value = Quote(20, 'USD', now)
    provider.fx.return_value = Quote(1, 'EUR', now)
    service.price('SYNTH')
    service.fx('USD')
    original = path.read_bytes()
    writes = []
    replace = Path.replace
    def counted(source, target):
        if target == path:
            writes.append(target)
        return replace(source, target)
    monkeypatch.setattr(Path, 'replace', counted)
    gate = Event()
    def fx(currency):
        assert gate.wait(3)
        raise ConnectionError('Synthetic FX outage')
    provider.fx.side_effect = fx
    provider.price.return_value = Quote(24, 'USD', now)
    try:
        reader = BackgroundPrices(service, jobs, tmp_path)
        with reader.collect_requests():
            reader.price('SYNTH', refresh=True)
            reader.fx('USD', refresh=True)
        deadline = monotonic() + 3
        while jobs.pending(tmp_path, 'price:SYNTH'):
            assert monotonic() < deadline
            Event().wait(.005)
        assert not writes and path.read_bytes() == original
        assert PriceService(provider, path, now=lambda: now).cached('price:SYNTH')[0].quote.price == 20
        assert service.cached('price:SYNTH')[0].quote.price == 24
        # An independent writer can publish an unrelated ticker before this batch finishes.
        PriceService(provider, path, now=lambda: now).price('SYNTH-OTHER')
        assert len(writes) == 1
        writes.clear()
    finally:
        gate.set()
    finish(jobs, tmp_path)
    assert len(writes) == 1
    reopened = PriceService(provider, path, now=lambda: now)
    assert reopened.cached('price:SYNTH')[0].quote.price == 24
    assert reopened.cached('price:SYNTH-OTHER')[0].quote.price == 24
    rate, due = reopened.cached('fx:USD')
    assert not due and rate.status == 'cached fallback'
    assert rate.quote.price == 1 and rate.error == 'Synthetic FX outage'


def test_new_requests_and_direct_corrections_during_cache_publication(tmp_path, now, jobs, monkeypatch):
    from unittest.mock import Mock
    path = tmp_path / 'prices.json'
    provider = Mock()
    service = PriceService(provider, path, now=lambda: now)
    started, release = Event(), Event()
    write = service._save
    first = True
    def pause_first(entries):
        nonlocal first
        if first:
            first = False
            started.set()
            assert release.wait(3)
        write(entries)
    monkeypatch.setattr(service, '_save', pause_first)
    provider.price.return_value = Quote(10, 'EUR', now)
    try:
        reader = BackgroundPrices(service, jobs, tmp_path)
        reader.price('SYNTH-A')
        assert started.wait(1)
        provider.price.return_value = Quote(12, 'EUR', now)
        service.price('SYNTH-A', refresh=True)
        other = BackgroundPrices(service, jobs, tmp_path)
        assert other.price('SYNTH-A').quote is None
        assert other.price('SYNTH-B').quote is None
        deadline = monotonic() + 3
        while jobs.pending(tmp_path, 'price:SYNTH-B'):
            assert monotonic() < deadline
            Event().wait(.005)
        assert other.still_current()
    finally:
        release.set()
    finish(jobs, tmp_path)
    reopened = PriceService(provider, path, now=lambda: now)
    assert [reopened.cached('price:' + key)[0].quote.price for key in ('SYNTH-A', 'SYNTH-B')] == [12, 12]


def test_failed_batch_write_preserves_previous_file_and_allows_recovery(tmp_path, now, jobs, monkeypatch):
    from unittest.mock import Mock
    path = tmp_path / 'prices.json'
    provider = Mock()
    service = PriceService(provider, path, now=lambda: now)
    provider.price.return_value = Quote(10, 'EUR', now)
    service.price('SYNTH')
    previous = path.read_bytes()
    def unwritable(*args):
        raise PermissionError('Synthetic unwritable cache')
    provider.price.return_value = Quote(12, 'EUR', now)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'replace', unwritable)
        BackgroundPrices(service, jobs, tmp_path).price('SYNTH', refresh=True)
        finish(jobs, tmp_path)
    assert path.read_bytes() == previous
    assert 'could not be saved' in service.cache_warning
    assert service.cached('price:SYNTH')[0].quote.price == 12
    BackgroundPrices(service, jobs, tmp_path).price('SYNTH', refresh=True)
    finish(jobs, tmp_path)
    assert PriceService(provider, path, now=lambda: now).cached('price:SYNTH')[0].quote.price == 12


def test_delayed_price_cache_write_honors_its_original_workspace_selection(tmp_path, now, monkeypatch):
    from unittest.mock import Mock
    from portfolio_app.workspace.workspace_lock import write_context
    from portfolio_app.workspace.workspace_selection import Selection, save_selection
    monkeypatch.setenv('PORTFOLIO_STATE_DIR', str(tmp_path / 'state'))
    original, other = tmp_path / 'original', tmp_path / 'other'
    original.mkdir()
    other.mkdir()
    provider = Mock()
    provider.price.return_value = Quote(10, 'EUR', now)
    path = original / '.cache' / 'prices.json'
    service = PriceService(provider, path, now=lambda: now)
    context = write_context.set((original, Selection(original)))
    try:
        service.price('SYNTH', defer_save=True)
        publish = service.take_cache_write()
    finally:
        write_context.reset(context)
    save_selection(original, Selection(other, 1))
    publish()
    assert not path.exists() and 'active portfolio changed' in service.cache_warning
    assert service.cached('price:SYNTH')[0].quote.price == 10


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


def test_valuation_revision_tracks_material_results_only(jobs):
    for key, valuation, changed in [('personal-history', False, True),
                                    ('price:unchanged', True, False),
                                    ('price:changed', True, True)]:
        release = Event()
        def work():
            assert release.wait(3)
            return changed
        assert jobs.request('workspace', key, work, channel='valuation' if valuation else None)
        assert jobs.channel_state('workspace', 'valuation') == (0, valuation)
        release.set()
        finish(jobs, 'workspace')
        assert jobs.channel_state('workspace', 'valuation') == (int(valuation and changed), False)
    assert jobs.revision('workspace') == 3


def test_purchase_rates_join_one_nonblocking_valuation_batch(tmp_path):
    from datetime import date
    import pandas as pd
    from portfolio_app.application import BackgroundFX, valuation_requests
    from portfolio_app.market.fx import HistoricalFX
    from portfolio_app.market.prices import UnavailableProvider
    from portfolio_app.performance.cost_basis import component, resolve_cost
    from portfolio_app.performance.history_observations import Observation, ObservationFX, ObservationStore

    class Provider:
        def __init__(self):
            self.days = []

        def fetch(self, currency, start, end):
            self.days.append(end)
            return pd.Series(.75, index=pd.date_range(start, end))

    queue = RequestCoordinator(workers=2, capacity=1)
    release = Event()
    try:
        assert queue.request('other', 'busy', lambda: release.wait(10))
        service = HistoricalFX(Provider(), tmp_path / 'invented-rates')
        dates = [(date(2024, 1, 1) + timedelta(days=30 * i)).isoformat() for i in range(25)]
        parts = [component(1, 100, 'USD', day) for day in dates]
        parts[1]['conversions'] = {'EUR': {'amount': '81', 'method': 'supplied_cost'}}
        parts += [component(1, 0, 'GBP', dates[0]), component(1, 10, 'EUR')]
        saved = ObservationStore((Observation('invented', 'fx', 'USD/EUR', dates[0], '.8', 'USD', 'EUR'),))
        history = ObservationFX(saved, BackgroundFX(service, lambda *_: pytest.fail('Ungrouped request')))
        reader = BackgroundPrices(PriceService(UnavailableProvider()), queue, str(tmp_path))
        with valuation_requests(reader, history) as rates:
            first = resolve_cost(parts, 'EUR', rates)
            assert first.reason_kind == 'fx' and first.amount is None
            assert resolve_cost(parts, 'EUR', rates) == first
            assert not service.provider.days
        assert reader.submit_waiting()
        assert queue.channel_state(tmp_path, 'valuation') == (0, False)
        release.set()
        finish(queue, 'other')
        assert not reader.submit_waiting()
        finish(queue, tmp_path)
        assert sorted(service.provider.days) == [(date.fromisoformat(day) + timedelta(days=1)).isoformat()
                                                 for day in dates[2:]]
        assert queue.channel_state(tmp_path, 'valuation') == (23, False)
        with valuation_requests(reader, history) as rates:
            assert resolve_cost(parts, 'EUR', rates).amount == 80 + 81 + 23 * 75 + 10
        assert not reader.submit_waiting()
    finally:
        release.set()
        queue.close()
