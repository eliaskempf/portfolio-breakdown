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
