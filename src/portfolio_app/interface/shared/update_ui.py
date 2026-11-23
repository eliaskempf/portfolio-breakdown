"""Application release controls shared by Help and the notification bell."""
from datetime import datetime, timezone

import streamlit as st

from portfolio_app.runtime.updates import RELEASES_URL, UpdateStateError, updates_for, version_number
from portfolio_app.settings import app_version


def _read(service):
    try:
        return service.store.read()
    except (OSError, UpdateStateError):
        return None


def watch_updates():
    service = updates_for()
    raw = _read(service)
    if raw is None or service.target == 'unsupported':
        return
    enabled = service.enabled(raw)
    if enabled and service.schedule(snapshot=raw):
        raw = _read(service)
    pending = service.pending()
    if not enabled and not pending:
        return

    # While idle, check the daily deadline without rerunning the portfolio views.
    @st.fragment(run_every=1 if pending else 60)
    def poll():
        current = _read(service)
        if service.pending() != pending or current != raw:
            st.rerun()
        if current is not None and not pending and enabled and service.schedule(snapshot=current):
            st.rerun()
    poll()


def render_update_settings(*, offline):
    installed = app_version()
    st.markdown(f'**Portfolio Breakdown {installed}**')
    st.link_button('GitHub releases', RELEASES_URL)
    service = updates_for()
    raw = _read(service)
    if raw is None:
        st.caption('Update preferences unavailable. Automatic checks are paused.')
        return

    def enable():
        try:
            service.store.enable(st.session_state['updates_enabled'])
        except (OSError, UpdateStateError):
            st.session_state['updates_save_error'] = True

    st.session_state['updates_enabled'] = service.enabled(raw)
    st.toggle('Check automatically', key='updates_enabled', on_change=enable,
              help='Check GitHub daily for a compatible stable release while the app is open.')
    if st.session_state.pop('updates_save_error', False):
        st.caption('Could not save update preferences. Try again.')

    def check_now():
        if not service.schedule(force=True):
            st.session_state['updates_deferred'] = True

    check = raw['checks'].get(service.target, {})
    pending = service.pending()
    st.button('Check for updates', on_click=check_now, key='updates_check',
              disabled=offline or pending or service.target == 'unsupported')
    if st.session_state.pop('updates_deferred', False):
        st.caption('Check unavailable. Try again in a minute.')
    if offline:
        st.caption('Update checks are paused in the offline demo.')
    elif service.target == 'unsupported':
        st.caption('No packaged update channel for this platform.')
    elif pending:
        st.caption('Checking for updates…')
    else:
        if check.get('failed'):
            st.caption('Could not check for updates. Try again later.')
        if release := service.available(raw, installed):
            st.link_button(f'View release {release.version}', release.url)
        elif version_number(installed) is None:
            st.caption('This build cannot be compared with stable release versions.')
        elif check.get('checked') and not check.get('failed'):
            st.caption('No newer compatible stable release found.')
        if check.get('checked'):
            stamp = datetime.fromtimestamp(check['checked'], timezone.utc)
            st.caption(f'Last successful check: {stamp:%Y-%m-%d %H:%M UTC}')


def _notification(service, raw):
    tag = raw['checks'].get(service.target, {}).get('tag') if raw else None
    if tag and tag != raw.get('cleared'):
        return service.available(raw, app_version())
    return None


def _mark_read(service, tag):
    st.session_state['updates_read'] = tag
    try:
        service.store.acknowledge(service.target, tag)
    except (OSError, UpdateStateError):
        pass  # Keep the dismissal for this session if preferences are unavailable.


def _unread(raw, tag):
    return raw['seen'] != tag and st.session_state.get('updates_read') != tag


def release_notification(*, opened=False):
    service = updates_for()
    raw = _read(service)
    release = _notification(service, raw)
    if not release:
        return False
    unread = _unread(raw, release.tag)
    if opened:
        with st.container(border=True):
            st.link_button(f'Version {release.version} available', release.url)
            if raw['checks'][service.target]['failed']:
                st.caption('Last known release · Latest check unavailable')
            if st.button('Clear notification', key='updates_clear_' + release.tag):
                try:
                    service.store.acknowledge(service.target, release.tag, clear=True)
                except (OSError, UpdateStateError):
                    st.error('Could not clear the update notification. Try again.')
                else:
                    st.rerun()
        if unread:
            _mark_read(service, release.tag)
    return unread


def render_update_dialog():
    # Run after the views and their dialogs, including dialogs opened by fragments.
    # The editor, tour or an actionable monitoring alert gets the modal slot first.
    from streamlit.runtime.scriptrunner import get_script_run_ctx
    ctx = get_script_run_ctx()
    if ctx and ctx.has_dialog_opened:
        return
    service = updates_for()
    raw = _read(service)
    release = _notification(service, raw)
    if not release or not _unread(raw, release.tag):
        return
    check = raw['checks'][service.target]
    if check['failed'] or check.get('pending'):
        return

    def dismiss():
        _mark_read(service, release.tag)

    @st.dialog('Update available', on_dismiss=dismiss)
    def dialog():
        st.write(f'Portfolio Breakdown {release.version} is available.')
        st.caption(f'Installed version: {app_version()}')
        st.link_button('View release', release.url, type='primary')
        if st.button('Got it', key='updates_close_' + release.tag):
            dismiss()
            st.rerun()
    dialog()
