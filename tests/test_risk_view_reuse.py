"""Risk presentation reuses only the current selection and the actual market evidence read."""
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from portfolio_app.analytics.risk_data import DEFAULT_BENCHMARK, DemoRiskHistoryProvider, MarketRiskHistoryProvider
from portfolio_app.interface.views.analytics import risk_updates


@pytest.fixture
def settled_read(monkeypatch):
    # Drive the same completion that the view timer observes in the running app.
    monkeypatch.setattr(risk_updates, '_observe', lambda job: None)

    def read(*args, **kwargs):
        outcome = risk_updates.view_risk(*args, **kwargs)
        session = risk_updates.st.session_state
        if job := session.get('analytics_risk_refresh'):
            job.result.result(timeout=10)
            kwargs.pop('refresh', None)
            outcome = risk_updates.view_risk(*args, **kwargs)
        return outcome

    return read


def test_risk_reuse_checks_revised_histories_weights_scope_and_retry_deadlines(tmp_path, monkeypatch, settled_read):
    session = {}
    monkeypatch.setattr(risk_updates.st, 'session_state', session)
    provider = DemoRiskHistoryProvider()
    calls = []
    down = {'SYNTH-B'}
    def fetch(self, ticker, years):
        calls.append(ticker)
        if ticker in down:
            raise ValueError('Invented missing history')
        return provider.fetch(ticker, years)
    monkeypatch.setattr(MarketRiskHistoryProvider, 'fetch', fetch)
    holdings = pd.DataFrame(dict(id=['a', 'b'], name=['Invented A', 'Invented B'], ticker=['SYNTH-A', 'SYNTH-B'],
        shares=1., current_value_reporting=[100., 200.], instrument_type='equity', quote_currency='EUR'))
    def read(positions=holdings, **kwargs):
        return settled_read(positions, tmp_path, False, DEFAULT_BENCHMARK, 3, **kwargs)
    first, sources = read()
    assert first.status == 'partial' and set(first.excluded) == {'b'} and 'fresh' in set(sources.Status)
    warm, cached = read(holdings.assign(price_age_hours=1.25))
    assert warm is first and 'fresh' not in set(cached.Status)
    assert calls[0] == DEFAULT_BENCHMARK + ':EUR'
    assert sorted(calls[1:]) == ['SYNTH-A', 'SYNTH-B']
    assert cached.attrs == sources.attrs

    # Another instance corrects adjusted prices without changing the fetched time.
    cache = session['analytics_risk_snapshot'].histories.cache
    path = cache._path('adjusted-daily-v1:SYNTH-A:3')
    evidence = json.loads(path.read_text())
    dates = pd.DatetimeIndex(evidence['data']['dates'])
    closing = dates[(dates.dayofweek == 4) & (dates < pd.Timestamp.today().normalize())][-1]
    evidence['data']['prices'][dates.get_loc(closing)] *= 1.25
    path.write_text(json.dumps(evidence))
    revised, _ = read()
    assert revised.beta != first.beta and len(calls) == 3
    assert read()[0] is revised

    failure_path = cache._path('adjusted-daily-v1:SYNTH-B:3')
    evidence = json.loads(failure_path.read_text())
    evidence['attempted_at'] = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
    failure_path.write_text(json.dumps(evidence))
    down.clear()
    recovered, _ = read()
    assert recovered.status == 'complete' and calls[3:] == ['SYNTH-B']
    heavier = holdings.copy()
    heavier.loc[1, 'current_value_reporting'] *= 2
    reweighted, _ = read(heavier)
    assert reweighted.beta != recovered.beta
    assert read(heavier)[0] is reweighted
    subset, _ = read(heavier, scoped=True, portfolio_result=SimpleNamespace(net_equity=700.))
    assert subset.scoped and subset.portfolio_beta_contribution == subset.beta * 500 / 700
    changed_base, _ = read(heavier, scoped=True, portfolio_result=SimpleNamespace(net_equity=900.))
    assert changed_base.portfolio_beta_contribution == changed_base.beta * 500 / 900
    refreshed, _ = read(heavier, refresh=True)
    assert calls[4:] == [DEFAULT_BENCHMARK + ':EUR', 'SYNTH-A', 'SYNTH-B']
    assert refreshed.beta != reweighted.beta and read(heavier)[0] is refreshed
    next_day = risk_updates.date.today() + timedelta(days=1)
    monkeypatch.setattr(risk_updates, 'date', SimpleNamespace(today=lambda: next_day))
    tomorrow, _ = read(heavier)
    assert tomorrow is not refreshed and read(heavier)[0] is tomorrow
    assert len(calls) == 7


def test_actual_risk_reuse_observes_proxy_positions_outside_the_selection(tmp_path, monkeypatch, settled_read):
    from test_actual_risk import actual, future, row
    from portfolio_app.funds.leverage_settings import FundFactor, LeverageSettings, save_leverage_settings
    monkeypatch.setattr(risk_updates.st, 'session_state', {})
    monkeypatch.setattr(MarketRiskHistoryProvider, 'fetch', DemoRiskHistoryProvider.fetch)
    positions = actual([row('cash', 'cash', 100., 'EUR'), future(proxy_position_id='proxy')])
    path = tmp_path / 'holdings.csv'
    def save_proxy(ticker):
        path.write_text('id,name,shares,instrument_type,position_key,ticker\n'
                        f'proxy,Invented proxy,1,etf,proxy,{ticker}\n')
    def read():
        return settled_read(positions, tmp_path, False, DEFAULT_BENCHMARK, 3,
            portfolio_result=SimpleNamespace(net_equity=100.))[0]
    save_proxy('SYNTH-A')
    first = read()
    assert first.status == 'complete' and read() is first
    save_proxy('SYNTH-B')
    revised = read()
    assert revised.status == 'complete' and revised.beta != first.beta and read() is revised
    save_leverage_settings(tmp_path, LeverageSettings(fund_factors=(FundFactor('proxy', None),)), None)
    excluded = read()
    assert excluded.status == 'partial' and 'future:synthetic' in excluded.excluded


@pytest.mark.parametrize('force', [False, True], ids=['expired', 'explicitly-refreshed'])
def test_stale_risk_paints_once_across_windows_then_uses_the_complete_worker_result(tmp_path, monkeypatch, force):
    from threading import Event, current_thread
    from portfolio_app.analytics.risk_data import RiskHistoryService

    initial = datetime.now(timezone.utc) - timedelta(hours=1 if force else 48)
    provider = DemoRiskHistoryProvider()
    seed = RiskHistoryService(provider, tmp_path / '.cache' / 'risk-history', now=lambda: initial)
    tickers = [DEFAULT_BENCHMARK + ':EUR', 'SYNTH-A', 'SYNTH-B']
    for ticker in tickers:
        seed.get(ticker)
    began, release = Event(), Event()
    requests = []

    def fetch(self, ticker, years):
        assert current_thread().name.startswith('market-data')
        requests.append(ticker)
        if ticker == tickers[-1]:
            began.set()
            assert release.wait(8)
        history = provider.fetch(ticker, years)
        if ticker == 'SYNTH-A':
            last = history.prices.index[history.prices.index.dayofweek == 4][-1]
            history.prices.loc[last] *= 1.25
        return history

    monkeypatch.setattr(MarketRiskHistoryProvider, 'fetch', fetch)
    monkeypatch.setattr(risk_updates, '_observe', lambda job: None)
    portfolios = pd.DataFrame(dict(id=['a', 'b'], ticker=tickers[1:], name=['A', 'B'], shares=1.,
        current_value_reporting=[30., 70.], instrument_type='equity', quote_currency='EUR'))
    def read(session, positions=portfolios, *, refresh=False):
        monkeypatch.setattr(risk_updates.st, 'session_state', session)
        return risk_updates.view_risk(positions, tmp_path, False, DEFAULT_BENCHMARK, 3, refresh)

    first, second = {}, {}
    try:
        old, sources = read(first, refresh=force)
        assert sources.Status.eq('cached' if force else 'stale').all()
        assert began.wait(8)
        assert read(second)[0] is old
        job = first['analytics_risk_refresh']
        assert second['analytics_risk_refresh'] is job
        assert read(first)[0] is old and risk_updates.mounted_risk_current(old)
        assert requests == tickers
        third = {}
        scaled = portfolios.assign(current_value_reporting=portfolios.current_value_reporting * 2)
        during, scaled_sources = read(third, scaled)
        assert during.beta == old.beta and scaled_sources.Status.eq('cached' if force else 'stale').all()
    finally:
        release.set()
    job.result.result(timeout=8)
    third['analytics_risk_refresh'].result.result(timeout=8)
    updated, _ = read(second)
    assert updated.beta != old.beta and read(first)[0] is updated
    reweighted, _ = read(first, portfolios.assign(current_value_reporting=[80., 20.]))
    assert reweighted.beta != updated.beta and requests == tickers


def test_risk_provider_keeps_external_correction_written_during_fetch(tmp_path):
    from portfolio_app.analytics.risk_data import RiskHistoryService

    seed = RiskHistoryService(DemoRiskHistoryProvider(), tmp_path)
    seed.get('SYNTH-A')
    path = seed.cache._path('adjusted-daily-v1:SYNTH-A:3')
    def fetch(ticker, years):
        raw = json.loads(path.read_text())
        raw['data']['prices'][-1] = 314.
        path.write_text(json.dumps(raw))
        return DemoRiskHistoryProvider().fetch(ticker, years)
    service = RiskHistoryService(SimpleNamespace(fetch=fetch), tmp_path, track_inputs=True)
    assert service.get('SYNTH-A', refresh=True).prices.iloc[-1] == 314.
    assert service.inputs_current()


@pytest.mark.parametrize('force', [False, True])
def test_overlapping_history_acquisitions_share_only_the_same_listing(tmp_path, force):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from portfolio_app.analytics.analytics_cache import AnalyticsCache

    cache = AnalyticsCache(tmp_path)
    entered, waiting, release = Event(), Event(), Event()
    calls = []
    def fetch():
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return {'prices': [11.]}
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(cache.get, 'A', fetch, refresh=force, preserve_updates=True)
        try:
            assert entered.wait(3)
            second = pool.submit(cache.get, 'A', fetch, refresh=force, preserve_updates=True,
                                 before_fetch=lambda *args: waiting.set())
            assert waiting.wait(3)
            other = pool.submit(cache.get, 'B', lambda: {'prices': [22.]}, preserve_updates=True)
            assert other.result(timeout=3).data == {'prices': [22.]}
        finally:
            release.set()
        assert first.result().data == second.result().data == {'prices': [11.]}
    assert calls == [1]
    if force:
        cache.get('A', fetch, refresh=True, preserve_updates=True)
        assert calls == [1, 1]


def test_repeated_risk_validation_decodes_only_changed_evidence(tmp_path, monkeypatch):
    import os
    from portfolio_app.analytics.risk_data import RiskHistoryService

    now = datetime.now(timezone.utc)
    service = RiskHistoryService(DemoRiskHistoryProvider(), tmp_path, now=lambda: now, track_inputs=True)
    service.get('SYNTH-A', 1)
    service.get('SYNTH-B', 1)
    assert service.inputs_current()
    decode, calls = json.loads, []
    def counted(raw):
        calls.append(1)
        return decode(raw)
    monkeypatch.setattr(json, 'loads', counted)
    assert service.inputs_current()
    assert not calls
    now += timedelta(days=2)
    assert not service.inputs_current()
    assert not calls
    now -= timedelta(days=2)
    path = service.cache._path('adjusted-daily-v1:SYNTH-B:1')
    stamp = path.stat()
    raw = path.read_text()
    price = str(decode(raw)['data']['prices'][0])
    path.write_text(raw.replace(price, ('2' if price[0] == '1' else '1') + price[1:], 1))
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert not service.inputs_current()
    assert calls == [1]


def test_risk_refresh_worker_failure_keeps_dated_result_and_allows_manual_retry(tmp_path, monkeypatch):
    from threading import current_thread
    from portfolio_app.analytics.risk_data import RiskHistoryService

    initial = datetime.now(timezone.utc) - timedelta(days=2)
    seed = RiskHistoryService(DemoRiskHistoryProvider(), tmp_path / '.cache' / 'risk-history', now=lambda: initial)
    for ticker in (DEFAULT_BENCHMARK + ':EUR', 'SYNTH'):
        seed.get(ticker)
    holdings = pd.DataFrame(dict(id=['x'], name=['Example'], shares=1., ticker='SYNTH',
        instrument_type='equity', current_value_reporting=10., quote_currency='EUR'))
    session, warnings = {}, []
    monkeypatch.setattr(risk_updates.st, 'session_state', session)
    monkeypatch.setattr(risk_updates.st, 'warning', warnings.append)
    monkeypatch.setattr(risk_updates, '_observe', lambda job: None)
    monkeypatch.setattr(MarketRiskHistoryProvider, 'fetch', DemoRiskHistoryProvider.fetch)
    calculate = risk_updates.load_risk
    def fail(*args, **kwargs):
        if current_thread().name.startswith('market-data'):
            raise OSError('Invented refresh failure')
        return calculate(*args, **kwargs)
    monkeypatch.setattr(risk_updates, 'load_risk', fail)
    def read(refresh=False):
        return risk_updates.view_risk(holdings, tmp_path, False, DEFAULT_BENCHMARK, 3, refresh)
    old, _ = read()
    job = session['analytics_risk_refresh']
    assert isinstance(job.result.exception(timeout=5), OSError)
    for _ in range(2):
        retained, status = read()
        assert retained.beta == old.beta and status.Status.eq('stale').all()
        assert 'analytics_risk_refresh' not in session
    assert warnings
    # A new window can use the same dated evidence without retrying a failed batch.
    other = {}
    monkeypatch.setattr(risk_updates.st, 'session_state', other)
    assert read()[0].beta == old.beta and 'analytics_risk_refresh' not in other
    monkeypatch.setattr(risk_updates.st, 'session_state', session)
    monkeypatch.setattr(risk_updates, 'load_risk', calculate)
    read(refresh=True)
    session['analytics_risk_refresh'].result.result(timeout=5)
    assert set(read()[1].Status) == {'fresh'}


def test_failed_risk_cache_write_survives_selection_changes(tmp_path, monkeypatch):
    from portfolio_app.analytics import analytics_cache
    session, calls = {}, []
    monkeypatch.setattr(risk_updates.st, 'session_state', session)
    def fail(*args):
        raise OSError('Invented read-only cache')
    monkeypatch.setattr(analytics_cache, 'staged_text', fail)
    def fetch(self, ticker, years):
        calls.append(ticker)
        return DemoRiskHistoryProvider().fetch(ticker, years)
    monkeypatch.setattr(MarketRiskHistoryProvider, 'fetch', fetch)
    holdings = pd.DataFrame(dict(id=['x'], name=['Example'], shares=1., ticker='SYNTH',
        instrument_type='equity', current_value_reporting=10., quote_currency='EUR'))
    for capital in (10., 20., 30.):
        result, sources = risk_updates.view_risk(holdings.assign(current_value_reporting=capital), tmp_path,
            False, DEFAULT_BENCHMARK, 3)
        assert result.status == 'complete' and sources.Note.str.contains('could not be saved').all()
    assert calls == [DEFAULT_BENCHMARK + ':EUR', 'SYNTH']


def test_queue_does_not_retain_or_calculate_abandoned_risk_selections(monkeypatch):
    from threading import Event
    from weakref import ref
    from portfolio_app.analytics import risk_refresh
    from portfolio_app.market.market_data import RequestCoordinator

    pool = RequestCoordinator(workers=1)
    monkeypatch.setattr(risk_refresh, 'coordinator', pool)
    release, called = Event(), []
    selection = SimpleNamespace(matches=lambda other: False)
    try:
        pool.request('busy', 'block', lambda: release.wait(5))
        abandoned = risk_refresh.start_risk_refresh(('example', None), selection, None,
            lambda **kwargs: called.append('abandoned'), force=False)
        reference = ref(abandoned)
        del abandoned
        assert reference() is None
        active = risk_refresh.start_risk_refresh(('example', None), selection, None,
            lambda **kwargs: called.append('active'), force=False)
        release.set()
        active.result.result(timeout=5)
    finally:
        release.set()
        pool.close()
    assert called == ['active'] and pool._errors == {}


@pytest.mark.parametrize('current', [True, False])
def test_simultaneous_initial_risk_reads_reuse_only_current_evidence(current):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from portfolio_app.analytics import risk_refresh

    began, joined, release = Event(), Event(), Event()
    class Selection:
        def matches(self, other):
            joined.set()
            return self is other

        def proxies_current(self):
            return True

    selection = Selection()
    workspace = ('synthetic-cold-read', None)
    calls = []
    def calculate(*, cached_read):
        calls.append(cached_read)
        began.set()
        assert release.wait(5)
        histories = SimpleNamespace(due=False, inputs_current=lambda *, allow_due: current)
        return SimpleNamespace(histories=histories)

    def read():
        return risk_refresh.read_risk_snapshot(workspace, selection, calculate, force=False)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(read)
        try:
            assert began.wait(3)
            second = pool.submit(read)
            assert joined.wait(3)
        finally:
            release.set()
        assert (first.result() is second.result()) == current
    assert len(calls) == (1 if current else 2)
    # A later visit consults its own session and current inputs.
    read()
    assert len(calls) == (2 if current else 3)


def test_slow_initial_risk_read_leaves_other_requests_independent():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from portfolio_app.analytics.risk_refresh import read_risk_snapshot

    first, other = (risk_updates.RiskSelection((name,), (), pd.DataFrame()) for name in ('first', 'other'))
    workspace = ('synthetic-cold-read', None)
    began, release = Event(), Event()

    def slow(*, cached_read):
        began.set()
        assert release.wait(5)
        raise ValueError('synthetic calculation failed')

    def quick(*, cached_read):
        return 'separate result'

    with ThreadPoolExecutor(max_workers=2) as pool:
        busy = pool.submit(read_risk_snapshot, workspace, first, slow, force=False)
        try:
            assert began.wait(3)
            for owner, selection, force in [(workspace, other, False), (workspace, first, True),
                                             (('second-workspace', None), first, False)]:
                pending = pool.submit(read_risk_snapshot, owner, selection, quick, force=force)
                assert pending.result(timeout=3) == 'separate result'
        finally:
            release.set()
        with pytest.raises(ValueError, match='synthetic calculation failed'):
            busy.result()
    assert read_risk_snapshot(workspace, first, quick, force=False) == 'separate result'


def test_cold_risk_overlaps_only_required_missing_listings(tmp_path, monkeypatch, settled_read):
    from contextvars import ContextVar
    from threading import Barrier, Lock
    from portfolio_app.analytics.risk_data import RiskHistoryService
    from test_risk import held

    provider = DemoRiskHistoryProvider()
    seed = RiskHistoryService(provider, tmp_path / '.cache' / 'risk-history')
    benchmark = DEFAULT_BENCHMARK + ':EUR'
    seed.get(benchmark)
    seed.get('warm')
    context = ContextVar('synthetic workspace tag')
    context.set('portfolio')
    pair = Barrier(2)
    lock = Lock()
    calls = []
    def fetch(self, ticker, years):
        with lock:
            calls.append((ticker, context.get(None)))
        if ticker in ('cold-a', 'cold-b'):
            pair.wait(timeout=2)
        return provider.fetch(ticker, years)

    monkeypatch.setattr(risk_updates.st, 'session_state', {})
    monkeypatch.setattr(MarketRiskHistoryProvider, 'fetch', fetch)
    positions = held(['cold-a', 'cold-b', 'manual', 'warm', 'zero', 'cash'], [20.] * 6)
    positions.loc[positions.id.eq('manual'), 'manual_price'] = 10.
    positions.loc[positions.id.eq('zero'), 'shares'] = 0.
    positions.loc[positions.id.eq('cash'), 'instrument_type'] = 'cash'
    result, sources = settled_read(positions, tmp_path, False, DEFAULT_BENCHMARK, 3)
    assert set(result.holdings.index) == {'cold-a', 'cold-b', 'warm', 'cash'}
    assert set(result.excluded) == {'manual'}
    assert sorted(calls) == [('cold-a', 'portfolio'), ('cold-b', 'portfolio')]
    assert set(sources.loc[sources.Status.eq('fresh'), 'Instrument']) == {'cold-a', 'cold-b'}
    assert settled_read(positions, tmp_path, False, DEFAULT_BENCHMARK, 3)[0] is result
    assert len(calls) == 2


def test_cold_batch_preserves_stale_and_failed_evidence_until_refresh(tmp_path):
    from portfolio_app.analytics.risk_data import RiskHistoryService

    old = datetime.now(timezone.utc) - timedelta(days=2)
    demo = DemoRiskHistoryProvider()
    seed = RiskHistoryService(demo, tmp_path, now=lambda: old)
    seed.get('dated')
    fetched = []
    def fetch(ticker, years):
        fetched.append(ticker)
        if ticker == 'missing':
            raise ValueError('Synthetic missing history')
        return demo.fetch(ticker, years)

    service = RiskHistoryService(SimpleNamespace(fetch=fetch), tmp_path, track_inputs=True,
                                 cached_read=lambda cache, key: cache.cached(key))
    symbols = ['dated', 'new', 'missing', 'new']
    histories = service.get_many(symbols, refresh=True)
    assert list(histories) == ['dated', 'new', 'missing']
    assert [history.status for history in histories.values()] == ['stale', 'fresh', 'unavailable']
    assert service.due and service.inputs_current(allow_due=True)
    assert sorted(fetched) == ['missing', 'new']
    refreshed = RiskHistoryService(SimpleNamespace(fetch=fetch), tmp_path)
    refreshed.acquired.update(service.acquired)
    refreshed.get_many(symbols, refresh=True)
    assert fetched[2:] == ['dated']


def test_cold_batch_keeps_progress_when_helpers_are_busy_or_workspace_is_locked(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Event, current_thread
    from portfolio_app.analytics.risk_data import _acquire_missing
    from portfolio_app.workspace.workspace_lock import workspace_lock

    occupied, release = Barrier(4), Event()
    def slow(ticker):
        occupied.wait(timeout=4)
        assert release.wait(4)
        return ticker

    # Two helper threads and the first caller wait on independent providers.
    # Another caller must still be able to complete its own cold batch.
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(_acquire_missing, ['a', 'b', 'c'], slow)
        try:
            occupied.wait(timeout=4)
            caller = current_thread()
            def reader(ticker):
                assert current_thread() is caller
                return ticker
            assert _acquire_missing(['d', 'e'], reader) == {'d': 'd', 'e': 'e'}
        finally:
            release.set()
        assert first.result() == {'a': 'a', 'b': 'b', 'c': 'c'}
    with workspace_lock(tmp_path):
        assert _acquire_missing(['d', 'e'], reader) == {'d': 'd', 'e': 'e'}
