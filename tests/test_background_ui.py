"""Background scheduling continues without replacing an open dialog."""
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

from portfolio_app import background_ui, settlement_archive_ui


def test_settlement_polling_defers_changed_data_but_not_collection(monkeypatch, tmp_path):
    import streamlit as st
    from portfolio_app import futures_quotes, futures_valuation
    state = {'position_edit_dialog': True}
    monkeypatch.setattr(st, 'session_state', state)
    ticks, refreshes, scheduled = [], [], []
    monkeypatch.setattr(st, 'fragment', lambda **kw: lambda fn: (ticks.append(fn) or fn))
    monkeypatch.setattr(st, 'rerun', lambda: refreshes.append(True))
    journal = SimpleNamespace(positions=('invented',))
    monkeypatch.setattr(settlement_archive_ui.ledger, 'load_journal', lambda _: (journal, None))
    material = [0]
    monkeypatch.setattr(settlement_archive_ui, '_signature', lambda _: material[0])
    monkeypatch.setattr(settlement_archive_ui, 'valuation_signature', lambda *a, **kw: material[0])
    for service in (settlement_archive_ui.collector, futures_quotes.collector):
        monkeypatch.setattr(service, 'schedule', lambda *a, **kw: (scheduled.append(True) and False))
        monkeypatch.setattr(service, 'running', lambda _: False)
        monkeypatch.setattr(service, 'error', lambda _: '')
    clock = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    monkeypatch.setattr(futures_valuation, 'utc_now', lambda: clock[0])
    settlement_archive_ui.watch_settlements(tmp_path, journal)
    clock[0] += timedelta(minutes=2)
    ticks[0]()
    assert not refreshes and len(scheduled) >= 6
    state['position_edit_dialog'] = False
    ticks[0]()
    assert not refreshes  # The elapsed minute alone never needs an app render.
    state['position_edit_dialog'] = True
    material[0] = 1
    ticks[0]()
    assert not refreshes
    state['position_edit_dialog'] = False
    ticks[0]()
    assert len(refreshes) == 1


def test_pending_open_and_other_editors_also_defer_refresh(monkeypatch):
    import streamlit as st
    for state in ({'position_edit_open_request': {'id':'invented'}}, {'backup_dialog_open':True}):
        monkeypatch.setattr(st,'session_state',state)
        assert not background_ui.refresh_allowed()
    monkeypatch.setattr(st,'session_state',{})
    assert background_ui.refresh_allowed()
