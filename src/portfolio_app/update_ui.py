"""Quiet application updates, independent of the selected portfolio."""
from datetime import datetime, timezone

import streamlit as st

from portfolio_app.settings import app_version
from portfolio_app.updates import UpdateStateError, updates_for, source_checkout, version_number
from portfolio_app.windows_update import downloads_for, installed_directory


def render_install_action(release, directory, *, key, disabled=False):
    if disabled or directory is None or installed_directory() is None:
        return
    downloads = downloads_for()
    initial = downloads.status()
    @st.fragment(run_every=1 if initial.phase == 'downloading' else None)
    def action():
        status = downloads.status()
        if initial.phase == 'downloading' and status.phase != 'downloading':
            st.rerun()  # Stop polling after completion; preserve popover scrolling.
        if status.phase == 'downloading':
            st.progress(status.progress, text='Downloading update…')
            return
        if status.tag == release.tag and status.phase == 'ready':
            st.caption('Update ready · Save your changes before exiting.')
            if st.button('Exit and update', key=f'updates_install_{key}', type='primary',
                         help='Close this app and open the Windows installer. Close other app windows first. Saved portfolio data stays in place.'):
                from portfolio_app.launcher import request_instance
                with st.spinner('Preparing update…'):
                    result = request_instance(directory, 'update', payload={'tag': release.tag})
                if result is None or result.get('error'):
                    message = (result or {}).get('error') or 'Could not contact the launcher. Restart the app or use View release.'
                    downloads.retry_after_error(release.tag, message)
                    st.rerun()
                else:
                    st.info('Closing the app. Continue in the Windows installer.')
        else:
            if status.tag == release.tag and status.error:
                st.error(status.error)
            if st.button('Download update', key=f'updates_download_{key}',
                         help='Download and verify the Windows installer. The app stays open until you choose Exit and update.'):
                downloads.start(release.tag)
                st.rerun()
    action()


def _read(service):
    try:
        return service.store.read()
    except (OSError, UpdateStateError):
        return None


def render_update_settings(*, offline=False, directory=None):
    service = updates_for()
    with st.expander('Updates'):
        raw = _read(service)
        if raw is None:
            st.caption('Update preferences unavailable. Automatic checks are paused.')
            return
        st.caption(f'Installed version: {app_version()}')
        if source_checkout():
            st.caption('Source checkout · Release versions only; Git changes are not compared.')
        def enable():
            try:
                service.store.enable(st.session_state['updates_enabled'])
            except (OSError, UpdateStateError):
                st.session_state['updates_save_error'] = True
        st.session_state['updates_enabled'] = service.enabled(raw)
        st.toggle('Check automatically', key='updates_enabled', on_change=enable,
                  help='Check GitHub daily and remind weekly when a newer compatible release is available. Applies to all portfolios on this computer.')
        if st.session_state.pop('updates_save_error', False):
            st.caption('Could not save update preferences. Try again.')
        def check_now():
            if not service.schedule(force=True):
                st.session_state['updates_check_deferred'] = True
        # Start in the callback so the full app's heartbeat sees the pending
        # request even when automatic checks are disabled.
        st.button('Check for updates', key='updates_check', on_click=check_now,
                  disabled=offline or service.pending() or service.target == 'unsupported')
        if st.session_state.pop('updates_check_deferred', False):
            st.caption('Please retry in a minute. Checks may be running in another window.')
        if offline:
            st.caption('Update checks are paused in the offline demo.')
        elif service.target == 'unsupported':
            st.caption('No packaged update channel for this platform.')
        elif service.pending():
            st.caption('Checking for updates…')
        check = raw['checks'].get(service.target, {})
        release = service.available(raw, app_version())
        if release:
            st.link_button(f'View release {release.version}', release.url)
            render_install_action(release, directory, key='settings', disabled=offline)
        elif version_number(app_version()) is None:
            st.caption('This build cannot be compared with stable release versions.')
        elif check.get('checked') and not check.get('failed'):
            st.caption('No newer compatible stable release found.')
        if check.get('failed') and not service.pending():
            st.caption('Could not check for updates. Try again later.')
        if check.get('checked'):
            stamp = datetime.fromtimestamp(check['checked'], timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            st.caption(f'Last successful check: {stamp}')


def watch_updates(*, disabled=False):
    if disabled:
        return
    service = updates_for()
    raw = _read(service)
    if raw is None:
        return
    service.schedule()
    initial = _read(service)
    pending = service.pending()
    @st.fragment(run_every=1 if pending else 60 if service.enabled(raw) else None)
    def heartbeat():
        if _read(service) != initial or service.pending() != pending or service.schedule():
            st.rerun()
    heartbeat()


def update_unread(*, disabled=False):
    if disabled:
        return False
    service = updates_for()
    raw = _read(service)
    release = service.available(raw, app_version()) if raw and service.enabled(raw) else None
    return bool(release and raw['seen'] != release.tag)


def render_update_notice(*, disabled=False):
    if disabled:
        return
    service = updates_for()
    raw = _read(service)
    release = service.available(raw, app_version()) if raw and service.enabled(raw) else None
    if release:
        with st.container(border=True):
            st.markdown(f'**Version {release.version} available**')
            st.link_button('View release', release.url)
            if raw['checks'][service.target]['failed']:
                st.caption('Last known release · Latest check unavailable')
        try:
            service.store.mark_seen(release.tag)
        except (OSError, UpdateStateError):
            pass


def render_update_dialog(*, disabled=False, directory=None):
    # Only interrupt the main results view; editors, onboarding, tours and
    # higher-priority alerts own their existing dialog lifecycles.
    blocked = ('position_edit_dialog', 'tour_welcome', 'workspace_settings', 'workspace_notifications', 'help_menu')
    if (disabled or st.session_state.get('main_tabs', 'Overview') != 'Overview'
            or any(st.session_state.get(key) for key in blocked)
            or st.session_state.get('onboarding_step') in {'categories', 'position'}
            or any(value for key, value in st.session_state.items()
                   if key != 'updates_dialog_open' and key.endswith(('_dialog_open', '_dialog_requested')))):
        return
    service = updates_for()
    raw = _read(service)
    release = service.available(raw, app_version()) if raw and service.enabled(raw) else None
    if not release:
        st.session_state.pop('updates_dialog_open', None)
        return
    if st.session_state.get('updates_dialog_open') != release.tag:
        try:
            if not service.claim_reminder(app_version()):
                return
        except (OSError, UpdateStateError):
            return
        st.session_state['updates_dialog_open'] = release.tag

    def dismiss():
        st.session_state.pop('updates_dialog_open', None)

    @st.dialog('Update available', on_dismiss=dismiss)
    def dialog():
        st.markdown(f'**Portfolio Breakdown {release.version}**')
        st.caption(f'Installed: {app_version()}')
        st.link_button('View release', release.url, type='primary')
        render_install_action(release, directory, key='dialog')
        if st.button('Remind me in a week', key='updates_later'):
            dismiss()
            st.rerun()
        if st.button('Never remind me again', key='updates_never',
                     help='Disable automatic checks and reminders for all future releases. Re-enable them in Portfolio settings → Updates.'):
            try:
                service.store.enable(False)
            except (OSError, UpdateStateError):
                st.error('Could not save your preference. Please try again.')
                return
            dismiss()
            st.rerun()
    dialog()
