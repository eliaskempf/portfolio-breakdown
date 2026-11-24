from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from portfolio_app.performance.history_diagnostics import HistoryIssue
from portfolio_app.performance.history_observations import HistoryScope, Observation, ObservationStore
from portfolio_app.performance.history_view import history_view
from portfolio_app.performance.history_view_cache import load_or_compute
from test_history_download import setup


def test_cache_preserves_calculation_and_typed_missing_evidence(tmp_path):
    journal, scope = setup(tmp_path)
    today = date(2024, 1, 8)
    store = ObservationStore((Observation('p', 'price', 'position-0', today.isoformat(), Decimal('10.15'), 'EUR'),))
    fresh = history_view(journal, scope, store, today=today)
    issue = HistoryIssue('missing closing FX', 'fx', (HistoryIssue('no quote', 'endpoint'),))
    fresh = replace(fresh, missing=(('2024-01-08', (issue,)),))
    signature = ('journal', 'observations', scope, '1Y', 'EUR', today, True, True)
    assert load_or_compute(tmp_path, signature, lambda: fresh) == fresh
    loaded = load_or_compute(tmp_path, signature, lambda: pytest.fail('disk hit should skip calculation'))
    assert loaded == fresh
    reason = loaded.missing[0][1][0]
    assert reason.kind == 'fx' and reason.causes[0].kind == 'endpoint'
    for index, value in enumerate(('edited journal', 'corrected observation', HistoryScope(('other',)), '3M',
                                  'USD', today + timedelta(days=1), False, False)):
        changed = list(signature)
        changed[index] = value
        calls = []
        load_or_compute(tmp_path, changed, lambda: calls.append(1) or fresh)
        assert calls == [1]
        load_or_compute(tmp_path, signature, lambda: fresh)
    path = tmp_path / '.cache/history-views/chart.json'
    path.write_bytes(path.read_bytes()[:-1])
    calls = []
    assert load_or_compute(tmp_path, signature, lambda: calls.append(1) or fresh) == fresh
    assert calls == [1]


def test_background_view_uses_one_complete_revision_and_leaves_inputs_untouched(tmp_path, monkeypatch):
    from threading import Event
    from portfolio_app.performance import history_update
    from portfolio_app.performance.history_observations import save_observations, load_observations
    journal, scope = setup(tmp_path)
    today = date(2024, 1, 8)
    store = ObservationStore((Observation('one', 'price', 'position-0', '2024-01-08', Decimal(11), 'EUR'),))
    save_observations(tmp_path, store, None)
    started, release = Event(), Event()
    def hold(directory):
        snapshot = load_observations(directory)
        started.set()
        assert release.wait(5)
        return snapshot
    monkeypatch.setattr(history_update, 'load_observations', hold)
    future = history_update.start_update(tmp_path, journal, 'prior', scope, '1Y', 'EUR', today)
    try:
        assert started.wait(3) and not future.done()
        assert history_update.start_update(tmp_path, journal, 'prior', scope, '1Y', 'EUR', today) is None
        _, revision = load_observations(tmp_path)
        corrected = replace(store.observations[0], id='two', replaces='one', amount=Decimal(13))
        save_observations(tmp_path, replace(store, observations=(*store.observations, corrected)), revision)
    finally:
        release.set()
    update = future.result(5)
    assert update.revision == revision and update.revision != load_observations(tmp_path)[1]
    assert update.view == history_view(journal, scope, store, today=today, period_annualized=False)


@pytest.mark.parametrize('points', [False, True])
def test_recomputes_stored_view_from_before_valuation_precision_fix(tmp_path, monkeypatch, points):
    from portfolio_app.performance import history_view_cache

    journal, scope = setup(tmp_path)
    today = date(2024, 1, 8)
    view = history_view(journal, scope, ObservationStore(), today=today, include_points=points)
    signature = ('journal', 'observations', scope, '1Y', 'EUR', today, points, True)
    versions = history_view_cache._versions()
    with monkeypatch.context() as previous:
        previous.setattr(history_view_cache, '_versions', lambda: (1, *versions[1:]))
        load_or_compute(tmp_path, signature, lambda: replace(view, useful=not view.useful))
    assert load_or_compute(tmp_path, signature, lambda: view) == view
    assert load_or_compute(tmp_path, signature, lambda: pytest.fail('updated snapshot should be reusable')) == view
