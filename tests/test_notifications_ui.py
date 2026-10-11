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
    activate(app, 'Notifications')
    assert not app.exception
    assert any('manual/snapshot prices' in item.value for item in app.info)
    activate(app, 'Overview')
    assert not any('manual/snapshot prices' in item.value for item in app.info)
    by_label(app.get('button_group'), 'Allocation measure').set_value('Capital allocation').run()
    activate(app, 'Notifications')
    activate(app, 'Overview')
    assert by_label(app.get('button_group'), 'Allocation measure').value == 'Capital allocation'
    assert not app.exception
    assert load_journal(tmp_path)[0] == journal
