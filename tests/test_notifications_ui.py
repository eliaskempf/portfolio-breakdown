"""Read-only navigation of notifications in an invented actual-account workspace."""
from portfolio_app.account_journal import save_journal, load_journal
from test_accounts_ui import launch_actual
from test_futures_investments import grouped
from test_ui import activate, by_label


def test_data_notices_live_in_notifications_and_navigation_preserves_portfolio(tmp_path, monkeypatch):
    _, journal, _ = grouped(tmp_path)
    save_journal(tmp_path, journal, None)
    app = launch_actual(tmp_path)
    assert not app.exception
    assert by_label(app.get('button_group'), 'Allocation measure').value == 'Market exposure'
    assert not any('snapshot prices' in item.value or 'Performance coverage:' in item.value for item in app.caption)
    metric = by_label(app.metric, 'Portfolio value')
    assert not metric.proto.delta_description
    assert len(metric.proto.help) < 230
    assert 'Notifications' not in [tab.label for tab in app.tabs]
    assert app.session_state['notification_seen'][str(tmp_path.resolve())] == set()
    app.session_state['workspace_notifications'] = True
    app.run()
    assert not app.exception
    assert any('manual/snapshot prices' in item.value for item in app.markdown)
    seen = app.session_state['notification_seen'][str(tmp_path.resolve())].copy()
    assert seen
    assert app.session_state['main_tabs'] == 'Overview'
    app.session_state['workspace_notifications'] = False
    app.run()
    assert not any('manual/snapshot prices' in item.value for item in app.markdown)
    by_label(app.get('button_group'), 'Allocation measure').set_value('Capital allocation').run()
    activate(app, 'Positions')
    app.session_state['workspace_notifications'] = True
    app.run()
    assert app.session_state['main_tabs'] == 'Positions'
    assert any('manual/snapshot prices' in item.value for item in app.markdown)
    app.session_state['workspace_notifications'] = False
    activate(app, 'Overview')
    assert by_label(app.get('button_group'), 'Allocation measure').value == 'Capital allocation'
    assert not app.exception
    assert load_journal(tmp_path)[0] == journal
    assert app.session_state['notification_seen'][str(tmp_path.resolve())] == seen
    monkeypatch.setattr('portfolio_app.etf_refresh_ui.coordinator.error', lambda _: 'Invented refresh outage')
    app.run()
    assert any('min-width:15px' in item.proto.body for item in app.get('html'))
    app.session_state['workspace_notifications'] = True
    app.run()
    assert len(app.session_state['notification_seen'][str(tmp_path.resolve())]) > len(seen)
    # Provider and price notices share the same neutral card presentation.
    notices = [item for item in app.markdown if 'manual/snapshot prices' in item.value or 'ETF refresh unavailable' in item.value]
    assert len(notices) == 2
    assert not app.info
    assert not app.warning


def test_unread_lifecycle_resolution_and_new_notices():
    from portfolio_app.notifications_ui import unread_notifications
    assert unread_notifications(set(), set()) == (False, set())
    assert unread_notifications({'first'}, set()) == (True, set())
    assert unread_notifications({'first'}, set(), opened=True) == (False, {'first'})
    assert unread_notifications({'first'}, {'first'}) == (False, {'first'})
    assert unread_notifications({'first', 'second'}, {'first'}) == (True, {'first'})
    assert unread_notifications(set(), {'first'}) == (False, set())
    assert unread_notifications({'first'}, set()) == (True, set())


def test_refresh_notice_identity_ignores_check_time_but_tracks_changed_issue(tmp_path, monkeypatch):
    import json
    from portfolio_app.etf_refresh_ui import refresh_notices
    monkeypatch.setattr('portfolio_app.etf_refresh_ui.coordinator.error', lambda _: None)
    path = tmp_path/'.cache'/'etf-refresh'/'status.json'
    path.parent.mkdir(parents=True)
    def record(message, checked):
        path.write_text(json.dumps({'invented-fund': {'status': 'unavailable', 'error': message, 'checked_at': checked}}))
        return refresh_notices(tmp_path, [])
    before = record('Invented outage', '2026-01-01')
    assert before == record('Invented outage', '2026-01-02')
    assert before[1] != record('Different invented issue', '2026-01-02')[1]
    assert refresh_notices(tmp_path, [], demo=True) == ([], ())


def test_running_refresh_does_not_hide_notices_being_marked_seen(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    path = tmp_path/'.cache'/'etf-refresh'/'status.json'
    path.parent.mkdir(parents=True)
    path.write_text('{"invented": {"status": "unavailable", "error": "Invented outage"}}')
    monkeypatch.setattr('portfolio_app.etf_refresh_ui.coordinator.running', lambda _: True)
    app = AppTest.from_string(f'''
from pathlib import Path
from portfolio_app.etf_refresh_ui import render_refresh_status, refresh_revision
root = Path({str(tmp_path)!r})
render_refresh_status(root, [], refresh_revision(root))
''').run()
    assert not app.exception
    assert any('Discovering and updating' in item.value for item in app.caption)
    assert any('1 ETF breakdown(s) unavailable' in item.value for item in app.caption)
