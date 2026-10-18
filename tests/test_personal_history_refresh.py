"""Synthetic background jobs; injected downloads and clocks, no network."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from threading import Event

import pytest

from portfolio_app.account_journal import save_journal, load_journal
from portfolio_app.history_observations import Observation, ObservationStore, load_observations, save_observations
from portfolio_app.history_scope import can_start_history_chart
from portfolio_app.market_data import RequestCoordinator
from portfolio_app.personal_history_refresh import HistoryRefresher
from portfolio_app.personal_market import merge_download
from test_history_download import setup
from test_market_data import finish


@pytest.fixture
def jobs():
    instance = RequestCoordinator(workers=2)
    yield instance
    instance.close()


def observation(value=12, identity='download', source='Invented feed'):
    return Observation(identity, 'price', 'position-0', '2024-01-02', D(value), 'EUR', source=source)


def test_background_single_flight_reuses_prices_and_preserves_concurrent_manual_edits(tmp_path, jobs):
    journal, scope = setup(tmp_path)
    save_journal(tmp_path, journal, None)
    started, release = Event(), Event()
    calls = []
    def download(directory, journal, scope, store, start, end, target):
        calls.append((start, end))
        started.set()
        assert release.wait(3)
        return merge_download(store, (observation(),)), []
    refresh = HistoryRefresher(jobs, download=download)
    try:
        assert refresh.request(tmp_path, journal, scope, 'EUR').pending
        assert started.wait(1)
        for _ in range(3):
            assert refresh.request(tmp_path, journal, scope, 'EUR').pending
        manual = observation(15, 'manual', 'manual')
        save_observations(tmp_path, ObservationStore((manual,)), None)
    finally:
        release.set()
    finish(jobs, tmp_path)
    assert not refresh.request(tmp_path, journal, scope, 'EUR').pending
    assert calls == [('2024-01-01', date.today().isoformat())]
    assert load_observations(tmp_path)[0] == ObservationStore((manual,))
    assert load_journal(tmp_path)[0] == journal


def test_failure_keeps_observations_and_retry_bypasses_cooldown(tmp_path, jobs):
    journal, scope = setup(tmp_path)
    saved = ObservationStore((observation(),))
    save_observations(tmp_path, saved, None)
    clock = [datetime(2026, 1, 7, tzinfo=timezone.utc)]
    calls = []
    def fail(*args):
        calls.append(1)
        raise OSError('Invented outage')
    refresh = HistoryRefresher(jobs, download=fail, now=lambda: clock[0])
    assert refresh.request(tmp_path, journal, scope, 'EUR').pending
    finish(jobs, tmp_path)
    state = refresh.request(tmp_path, journal, scope, 'EUR')
    assert state.errors and not state.pending and len(calls) == 1
    assert refresh.request(tmp_path, journal, scope, 'EUR', force=True).pending
    finish(jobs, tmp_path)
    assert len(calls) == 2
    clock[0] += timedelta(minutes=6)
    assert refresh.request(tmp_path, journal, scope, 'EUR').pending
    finish(jobs, tmp_path)
    assert len(calls) == 3 and load_observations(tmp_path)[0] == saved


def test_changed_listing_cannot_receive_old_download_and_next_request_retries(tmp_path, jobs):
    journal, scope = setup(tmp_path)
    started, release = Event(), Event()
    def download(*args):
        started.set()
        assert release.wait(3)
        return ObservationStore((observation(),)), []
    refresh = HistoryRefresher(jobs, download=download)
    try:
        assert refresh.request(tmp_path, journal, scope, 'EUR').pending
        assert started.wait(1)
        path = tmp_path/'holdings.csv'
        path.write_text(path.read_text().replace('FAKE', 'OTHER'))
    finally:
        release.set()
    finish(jobs, tmp_path)
    assert not load_observations(tmp_path)[0].observations
    assert refresh.request(tmp_path, journal, scope, 'EUR').pending
    finish(jobs, tmp_path)
    assert load_observations(tmp_path)[0].observations == (observation(),)


def test_only_reconstructed_scopes_default_to_loading_chart(tmp_path, jobs):
    journal, scope = setup(tmp_path)
    assert can_start_history_chart(journal, scope)
    assert not can_start_history_chart(journal, replace(scope, positions=(*scope.positions, 'unknown')))
    assert not can_start_history_chart(journal, replace(scope, unresolved=('Unknown allocation',)))
    snapshot = replace(journal.securities[0], active_ids=(journal.securities[0].active_ids[0],))
    assert not can_start_history_chart(replace(journal, securities=(snapshot,)), scope)
    draft = replace(journal, securities=(replace(journal.securities[0], active_ids=()),))
    refresh = HistoryRefresher(jobs, download=lambda *a: pytest.fail('Inactive history must not fetch'))
    assert not refresh.request(tmp_path, draft, scope, 'EUR').pending
