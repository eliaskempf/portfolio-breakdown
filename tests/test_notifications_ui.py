"""Read-only navigation of notifications in an invented actual-account workspace."""
from portfolio_app.account_journal import save_journal, load_journal
from test_accounts_ui import launch_actual
from test_futures_investments import grouped
from test_ui import activate, by_label


def test_data_notices_live_in_notifications_and_navigation_preserves_portfolio(tmp_path):
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
    app.session_state['workspace_notifications'] = True
    app.run()
    assert not app.exception
    assert any('manual/snapshot prices' in item.value for item in app.info)
    assert app.session_state['main_tabs'] == 'Overview'
    app.session_state['workspace_notifications'] = False
    app.run()
    assert not any('manual/snapshot prices' in item.value for item in app.info)
    by_label(app.get('button_group'), 'Allocation measure').set_value('Capital allocation').run()
    activate(app, 'Positions')
    app.session_state['workspace_notifications'] = True
    app.run()
    assert app.session_state['main_tabs'] == 'Positions'
    assert any('manual/snapshot prices' in item.value for item in app.info)
    app.session_state['workspace_notifications'] = False
    activate(app, 'Overview')
    assert by_label(app.get('button_group'), 'Allocation measure').value == 'Capital allocation'
    assert not app.exception
    assert load_journal(tmp_path)[0] == journal
