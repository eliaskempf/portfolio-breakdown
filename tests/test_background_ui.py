"""Background scheduling continues without replacing an open dialog."""
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest

from portfolio_app.interface.shared import background_ui; from portfolio_app.interface.views.derivatives import settlement_archive_ui


def test_settlement_polling_defers_changed_data_but_not_collection(monkeypatch, tmp_path):
    import streamlit as st
    from portfolio_app.derivatives import futures_quotes; from portfolio_app.derivatives import futures_valuation
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


@pytest.mark.parametrize('outcome,rerenders', [
    ('checked', False), ('already locked', False), ('new breakdown', True),
    ('unavailable', False), ('worker error', False), ('settings open', True), ('notifications open', True),
])
def test_etf_completion_redraws_only_affected_views(tmp_path, monkeypatch, outcome, rerenders):
    import streamlit as st
    from portfolio_app.interface.views.funds import etf_refresh_ui as ui
    from portfolio_app.funds.etf_refresh import write_json
    from portfolio_app.interface.shared.input_cache import fund_sources
    state = {}
    pending, error = [True], ['']
    callbacks, timers, renders, indicators = [], [], [], []
    def fragment(fn):
        callbacks.append(fn)
        return fn
    monkeypatch.setattr(st, 'session_state', state)
    monkeypatch.setattr(st, 'fragment', lambda **kwargs: fragment)
    monkeypatch.setattr(st, 'rerun', lambda: renders.append(True))
    monkeypatch.setattr(background_ui, '_set_poll_interval', timers.append)
    monkeypatch.setattr(ui.coordinator, 'running', lambda _: pending[0])
    monkeypatch.setattr(ui.coordinator, 'error', lambda _: error[0])
    (tmp_path / 'etfs').mkdir()
    state['parsed_inputs'] = {'funds': (fund_sources(tmp_path), [])}
    if outcome == 'settings open':
        state.update(main_tabs='Exposure', exposure_refresh_open=True)
    ui.render_refresh_status(tmp_path, [], ui.refresh_revision(tmp_path), show_messages=False,
                             render_indicator=indicators.append, panel_open=outcome == 'notifications open')
    assert timers == [2] and not renders
    if outcome != 'already locked':
        write_json(tmp_path / '.cache/etf-refresh/status.json',
                   {'invented': {'status': 'unavailable' if outcome == 'unavailable' else 'checked'}})
    if outcome == 'new breakdown':
        (tmp_path / 'etfs/new.yaml').write_text('holdings_file: invented.csv')
    if outcome == 'worker error':
        error[0] = 'Invented failure'
    pending[0] = False
    state['position_edit_dialog'] = True
    timers.clear()
    callbacks[0]()
    assert not renders and timers == ([] if rerenders else [None])
    state['position_edit_dialog'] = False
    callbacks[0]()
    assert bool(renders) == rerenders
    if outcome == 'unavailable':
        assert indicators[-1].failures == (('invented', 'unavailable', ''),)
    elif outcome == 'worker error':
        assert indicators[-1].error == 'Invented failure'


def test_status_timer_updates_only_when_cadence_changes_and_resets_on_remount(monkeypatch):
    import streamlit as st
    monkeypatch.setattr(st, 'fragment', lambda: lambda fn: fn)
    intervals = []
    monkeypatch.setattr(background_ui, '_set_poll_interval', intervals.append)
    pending = [False]

    def status(poll):
        if pending[0]:
            poll(.5)

    active = background_ui.status_fragment(run_every=60)(status)
    active()
    pending[0] = True
    active()
    active()
    pending[0] = False
    active()
    assert intervals == [60, .5, 60]

    idle = background_ui.status_fragment()(status)
    idle()
    idle()
    assert intervals == [60, .5, 60, None]


def test_status_timer_disarms_when_the_polling_view_exits(monkeypatch):
    import streamlit as st
    monkeypatch.setattr(st, 'fragment', lambda: lambda fn: fn)
    intervals = []
    monkeypatch.setattr(background_ui, '_set_poll_interval', intervals.append)
    @background_ui.status_fragment()
    def status(poll):
        if intervals:
            raise RuntimeError('Invented interrupted view')
        poll(.15)
    status()
    with pytest.raises(RuntimeError, match='Invented interrupted view'):
        status()
    assert intervals == [.15, None]
