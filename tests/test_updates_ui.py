"""Update status and fallback links in isolated, synthetic application state."""
import pytest
from streamlit.testing.v1 import AppTest

from portfolio_app.interface.shared import update_ui
from portfolio_app.runtime.updates import RELEASES_URL, Release, UpdateService


def settings(offline=False, *, launcher_directory=None):
    return AppTest.from_string(
        'from portfolio_app.interface.shared.update_ui import render_update_settings\n'
        f'render_update_settings(offline={offline!r}, launcher_directory={launcher_directory!r})', default_timeout=20
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


@pytest.mark.parametrize(('target', 'managed', 'installed', 'offline', 'offered'), [
    ('windows-x64', True, True, False, True),
    ('windows-x64', True, False, False, False),
    ('windows-x64', False, True, False, False),
    ('windows-x64', True, True, True, False),
    ('linux-x64', True, False, False, False),
])
def test_installer_offered_only_in_eligible_release_context(tmp_path, monkeypatch, target, managed, installed, offline, offered):
    from portfolio_app.runtime import windows_update
    service = UpdateService(tmp_path, target, fetch=lambda _: Release('v0.2.1'))
    assert service.schedule()
    service._thread.join(3)
    monkeypatch.setattr(update_ui, 'updates_for', lambda: service)
    monkeypatch.setattr(update_ui, 'app_version', lambda: '0.2.0')
    monkeypatch.setattr(windows_update, 'installed_directory', lambda: tmp_path if installed else None)
    monkeypatch.setattr(windows_update, 'downloads_for', lambda: windows_update.InstallerDownload(tmp_path))
    app = settings(offline=offline, launcher_directory=str(tmp_path) if managed else None)
    assert not app.exception
    assert any(button.label == 'Download update' for button in app.button) == offered
    assert not any(button.label == 'Exit and update' for button in app.button)
    if not offline:
        assert any(link.proto.url.endswith('/tag/v0.2.1') for link in app.get('link_button'))
