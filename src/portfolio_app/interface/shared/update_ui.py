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


def _release_actions(release, *, target, launcher_directory, surface, link_label, dismiss=None):
    installer = None
    if launcher_directory is not None and target == 'windows-x64':
        from portfolio_app.runtime.windows_update import downloads_for, installed_directory
        if installed_directory() is not None:
            installer = downloads_for()
    if installer is None:
        st.link_button(link_label, release.url, type='primary' if surface == 'dialog' else 'secondary')
        if surface == 'dialog' and st.button('Got it', key='updates_close_' + release.tag):
            dismiss()
            st.rerun()
        return

    key = f'updates_{surface}_{release.tag}'
    status = installer.status()
    if surface != 'dialog':
        st.markdown(f'**Version {release.version} available**')
    if status.phase == 'downloading':
        # Poll just the progress bar. Rebuild its controls once when verification finishes.
        @st.fragment(run_every=1)
        def progress():
            current = installer.status()
            if current.phase != 'downloading':
                st.rerun()
            st.progress(current.progress, text=f'Downloading {current.tag.removeprefix("v")}…')
        progress()
    elif status.tag == release.tag and status.phase == 'ready':
        st.caption('This closes the app and opens Windows Setup. Save any edits first.')
        with st.container(horizontal=True):
            if st.button('Keep working', key=key+'_keep', on_click=dismiss):
                st.rerun()
            if st.button('Exit and update', key=key+'_install', type='primary'):
                from portfolio_app.runtime.launcher import request_instance
                with st.spinner('Preparing Windows Setup…'):
                    result = request_instance(launcher_directory, 'update', payload={'tag': release.tag})
                if result is None or result.get('error'):
                    error = (result or {}).get('error') or 'Could not reach the launcher. Reopen the app or use View release.'
                    installer.retry_after_error(release.tag, error)
                    st.rerun()
                st.info('Closing the app. Continue in Windows Setup.')
    else:
        if status.tag == release.tag and status.error:
            st.error(status.error)
        with st.container(horizontal=True):
            st.button('Download update', key=key+'_download', type='primary',
                      on_click=installer.start, args=(release.tag,))
            if surface == 'dialog' and st.button('Later', key=key+'_later'):
                dismiss()
                st.rerun()
    st.link_button('View release', release.url, type='tertiary')


def render_update_settings(*, offline, launcher_directory=None):
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
            _release_actions(release, target=service.target, surface='help', launcher_directory=launcher_directory,
                             link_label=f'View release {release.version}',
                             dismiss=lambda: st.session_state.update(app_menu=False))
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


def release_notification(*, opened=False, launcher_directory=None):
    service = updates_for()
    raw = _read(service)
    release = _notification(service, raw)
    if not release:
        return False
    unread = _unread(raw, release.tag)
    if opened:
        with st.container(border=True):
            _release_actions(release, target=service.target, surface='notification', launcher_directory=launcher_directory,
                             link_label=f'Version {release.version} available',
                             dismiss=lambda: st.session_state.update(workspace_notifications=False))
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


def render_update_dialog(*, launcher_directory=None):
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
        _release_actions(release, target=service.target, surface='dialog', launcher_directory=launcher_directory,
                         link_label='View release', dismiss=dismiss)
    dialog()
