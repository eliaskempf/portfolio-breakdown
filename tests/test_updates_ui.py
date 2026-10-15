"""Update settings and dialog eligibility with synthetic release state."""
from streamlit.testing.v1 import AppTest

from portfolio_app.updates import Release, UpdateService
from test_ui import by_label


def service_for(tmp_path, monkeypatch):
    service = UpdateService(tmp_path / 'state', 'linux-x64', fetch=lambda _: Release('v0.2.0'))
    assert service.schedule()
    service._thread.join(3)
    monkeypatch.setattr('portfolio_app.update_ui.updates_for', lambda: service)
    return service


def test_dialog_optout_is_durable_and_manual_result_remains_available(tmp_path, monkeypatch):
    service = service_for(tmp_path, monkeypatch)
    script = '''
from portfolio_app.update_ui import render_update_settings, render_update_dialog
render_update_settings()
render_update_dialog()
'''
    app = AppTest.from_string(script).run()
    assert not app.exception
    assert any(item.value == '**Portfolio Breakdown 0.2.0**' for item in app.markdown)
    by_label(app.button, 'Never remind me again').click().run()
    assert not app.exception
    assert service.store.read()['enabled'] is False
    assert not any(item.label == 'Never remind me again' for item in app.button)
    reopened = AppTest.from_string(script).run()
    assert not reopened.exception
    assert not reopened.toggle[0].value
    assert any(item.proto.label == 'View release 0.2.0' for item in reopened.get('link_button'))
    assert not by_label(reopened.button, 'Check for updates').disabled


def test_postpone_and_offline_mode(tmp_path, monkeypatch):
    service = service_for(tmp_path, monkeypatch)
    script = '''
from portfolio_app.update_ui import render_update_dialog
render_update_dialog()
'''
    app = AppTest.from_string(script).run()
    by_label(app.button, 'Remind me in a week').click().run()
    assert not app.button
    assert service.store.read()['enabled'] is None
    assert not AppTest.from_string(script).run().button
    offline = AppTest.from_string('''
from portfolio_app.update_ui import render_update_settings, render_update_dialog
render_update_settings(offline=True)
render_update_dialog(disabled=True)
''').run()
    assert not offline.exception
    assert by_label(offline.button, 'Check for updates').disabled


def test_editors_tours_and_notifications_do_not_consume_reminder(tmp_path, monkeypatch):
    service = service_for(tmp_path, monkeypatch)
    for key, value in [('main_tabs', 'Positions'), ('position_edit_dialog', True),
                       ('tour_welcome', True), ('workspace_notifications', True),
                       ('workspace_settings', True), ('some_dialog_open', True)]:
        app = AppTest.from_string(f'''
import streamlit as st
from portfolio_app.update_ui import render_update_dialog
st.session_state[{key!r}] = {value!r}
render_update_dialog()
''').run()
        assert not app.exception
        assert not app.button
        assert service.store.read()['remind_after'] == 0


def test_bell_read_state_is_independent_of_weekly_popup(tmp_path, monkeypatch):
    service = service_for(tmp_path, monkeypatch)
    from portfolio_app.update_ui import update_unread
    assert update_unread()
    app = AppTest.from_string('''
from portfolio_app.update_ui import render_update_notice
render_update_notice()
''').run()
    assert not app.exception
    assert not update_unread()
    assert service.store.read()['remind_after'] == 0
    assert service.claim_reminder('0.1.0')


def test_completed_onboarding_allows_reminders(tmp_path, monkeypatch):
    service_for(tmp_path, monkeypatch)
    app = AppTest.from_string('''
import streamlit as st
from portfolio_app.update_ui import render_update_dialog
st.session_state['onboarding_step'] = 'done'
render_update_dialog()
''').run()
    assert not app.exception
    assert by_label(app.button, 'Remind me in a week')
