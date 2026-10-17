"""Background scheduling continues without replacing an open dialog."""
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

from portfolio_app import background_ui, settlement_archive_ui


def test_settlement_heartbeat_defers_page_refresh_but_keeps_collecting(monkeypatch, tmp_path):
    import streamlit as st
    from portfolio_app import futures_quotes, futures_clock
    state = {'position_edit_dialog': True}
    monkeypatch.setattr(st, 'session_state', state)
    ticks, refreshes, scheduled = [], [], []
    monkeypatch.setattr(st, 'fragment', lambda **kw: lambda fn: (ticks.append(fn) or fn))
    monkeypatch.setattr(st, 'rerun', lambda: refreshes.append(True))
    journal = SimpleNamespace(positions=('invented',))
    monkeypatch.setattr(settlement_archive_ui.ledger, 'load_journal', lambda _: (journal, None))
    monkeypatch.setattr(settlement_archive_ui, '_signature', lambda _: ())
    for service in (settlement_archive_ui.collector, futures_quotes.collector):
        monkeypatch.setattr(service, 'schedule', lambda *a, **kw: (scheduled.append(True) and False))
        monkeypatch.setattr(service, 'running', lambda _: False)
        monkeypatch.setattr(service, 'error', lambda _: '')
    clock = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    monkeypatch.setattr(futures_clock, 'utc_now', lambda: clock[0])
    settlement_archive_ui.watch_settlements(tmp_path, journal)
    clock[0] += timedelta(minutes=2)
    ticks[0]()
    assert not refreshes and len(scheduled) >= 6
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
