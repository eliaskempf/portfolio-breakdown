"""Update status and fallback links in isolated, synthetic application state."""
from streamlit.testing.v1 import AppTest

from portfolio_app.interface.shared import update_ui
from portfolio_app.runtime.updates import RELEASES_URL, Release, UpdateService


def settings(offline=False):
    return AppTest.from_string(
        'from portfolio_app.interface.shared.update_ui import render_update_settings\n'
        f'render_update_settings(offline={offline!r})', default_timeout=20
    ).run()


def test_manual_check_reports_current_and_retries_failures_immediately(tmp_path, monkeypatch):
    def offline(_):
        raise OSError('Invented network outage')

    service = UpdateService(tmp_path, 'source', automatic=False, fetch=offline)
    monkeypatch.setattr(update_ui, 'updates_for', lambda: service)
    monkeypatch.setattr(update_ui, 'app_version', lambda: '0.2.1')
    app = settings()
    app.button(key='updates_check').click().run()
    service._thread.join(3)
    app.run()
    assert not app.exception
    assert any('Could not check' in item.value for item in app.caption)
    assert not any('No newer' in item.value for item in app.caption)
    assert app.get('link_button')[0].proto.url == RELEASES_URL

    service.fetch = lambda _: Release('v0.2.1')
    app.button(key='updates_check').click().run()
    service._thread.join(3)
    app.run()
    assert not app.exception
    assert any('No newer compatible stable release' in item.value for item in app.caption)
    assert not service.enabled(service.store.read())


def test_offline_or_unreadable_state_preserves_release_link(tmp_path, monkeypatch):
    service = UpdateService(tmp_path, 'source', automatic=False)
    monkeypatch.setattr(update_ui, 'updates_for', lambda: service)
    demo = settings(offline=True)
    assert demo.button(key='updates_check').disabled
    assert demo.get('link_button')[0].proto.url == RELEASES_URL
    service.store.path.write_text('invalid')
    damaged = settings()
    assert not damaged.exception
    assert not damaged.button
    assert damaged.get('link_button')[0].proto.url == RELEASES_URL


def test_update_popup_waits_for_existing_dialog(tmp_path, monkeypatch):
    service = UpdateService(tmp_path, 'source', fetch=lambda _: Release('v0.2.1'))
    assert service.schedule()
    service._thread.join(3)
    monkeypatch.setattr(update_ui, 'updates_for', lambda: service)
    monkeypatch.setattr(update_ui, 'app_version', lambda: '0.2.0')
    app = AppTest.from_string("""
import streamlit as st
from portfolio_app.interface.shared.update_ui import render_update_dialog
if not st.session_state.get('editor_closed'):
    @st.dialog('Example editor')
    def editor():
        if st.button('Finish editing'):
            st.session_state.editor_closed = True
            st.rerun()
    editor()
render_update_dialog()
""", default_timeout=20).run()
    assert not app.exception
    assert [button.label for button in app.button] == ['Finish editing']
    assert not service.store.read()['seen']
    app.button[0].click().run()
    assert not app.exception
    assert [button.label for button in app.button] == ['Got it']
