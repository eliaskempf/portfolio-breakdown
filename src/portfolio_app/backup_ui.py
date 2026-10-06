"""Session-only backup downloads and restore review, separate from holdings import."""
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import streamlit as st

from portfolio_app.backup import (create_backup, inspect_backup, commit_restore,
                                   restore_destination, MAX_ARCHIVE)
from portfolio_app.holdings import DataError


def clear_backup():
    draft = st.session_state.get('backup_draft')
    if draft is not None:
        draft.close()
    for key in list(st.session_state):
        if key.startswith('backup_'):
            del st.session_state[key]


def backup_controls(*, demo=False, recovery=False):
    st.markdown('**Backup & restore**')
    disabled = demo
    def open_action(action):
        clear_backup()
        st.session_state['backup_action'] = action
        st.session_state['workspace_settings'] = False
    st.button('Create backup', on_click=open_action, args=('create',), disabled=disabled)
    st.button('Restore backup', on_click=open_action, args=('restore',), disabled=disabled or recovery)
    if demo:
        st.caption('Select My portfolio to back up or restore persisted data.')
    elif recovery:
        st.caption('Exact-folder recovery mode: relaunch normally to restore and switch.')
    else:
        st.caption('Complete portfolio archives. CSV/Excel holdings import is in Positions.')


def render_backup_dialog(directory: Path, launch: Path, generation: int) -> bool:
    action = st.session_state.get('backup_action')
    if not action:
        return False

    @st.dialog('Create backup' if action == 'create' else 'Restore backup', width='large', on_dismiss=clear_backup)
    def dialog():
        if action == 'create':
            st.caption('Includes all saved portfolio data, settings, ETF snapshots and document backups. Unsaved forms are not included.')
            try:
                if 'backup_download' not in st.session_state:
                    with st.spinner('Capturing and verifying portfolio…'):
                        st.session_state['backup_download'] = create_backup(directory)
                    st.session_state['backup_filename'] = datetime.now(timezone.utc).strftime('portfolio-%Y%m%d-%H%M%S.portfolio-backup.zip')
                st.download_button('Download backup', st.session_state['backup_download'],
                                   file_name=st.session_state['backup_filename'], mime='application/zip', on_click='ignore')
                st.caption('Keep this private archive somewhere safe. It is not encrypted. Close external editors before creating a backup.')
            except (DataError, OSError) as exc:
                st.error(str(exc))
            if st.button('Close backup'):
                clear_backup()
                st.rerun()
            return

        draft = st.session_state.get('backup_draft')
        if draft is None:
            uploaded = st.file_uploader('Portfolio backup archive', type=['zip'], key='backup_upload', max_upload_size=MAX_ARCHIVE // 1024**2)
            st.caption('Choose a complete Portfolio Breakdown backup; holdings CSV/Excel files use the separate importer.')
            if st.button('Review backup', disabled=uploaded is None):
                try:
                    with st.spinner('Validating backup…'):
                        draft = inspect_backup(uploaded.getvalue())
                    st.session_state['backup_draft'] = draft
                    st.session_state['backup_destination'] = str(directory.parent / ('portfolio-restored-' + uuid4().hex[:10]))
                    st.rerun()
                except (DataError, OSError) as exc:
                    st.error(str(exc))
        else:
            st.write('Backup verified. Review before restoring and switching.')
            st.write(f"Created: {draft.manifest['created_at']} · App: {draft.manifest['app_version']} · Archive format: {draft.manifest['version']}")
            summary = draft.summary
            st.write(f"Currency: {summary['reporting_currency']} · Positions: {summary['positions']} · Categories: {summary['categories']} · Classified instruments: {summary['classifications']} · ETF snapshots: {summary['etf_snapshots']}")
            st.caption(f"{summary['files']} files · {summary['bytes']:,} uncompressed bytes. Includes saved purchase costs, targets, overrides, caches and document backups.")
            st.write('Current workspace will be preserved:')
            st.code(str(directory), language=None)
            restored = st.session_state.get('backup_restored')
            target = st.text_input('New workspace folder', key='backup_destination', disabled=restored is not None,
                                   help='A new folder on the computer running the app. Its parent must already exist.')
            valid = True
            if restored is None:
                try:
                    restore_destination(Path(target), directory)
                except (DataError, OSError) as exc:
                    st.error(str(exc))
                    valid = False
            else:
                st.info('The verified workspace has been restored. Switching did not complete; retry or keep it for later.')
            st.caption('Confirmation discards unsaved forms and switches this app. The same shortcut or launch path will reopen the restored workspace after restart.')
            if st.button('Retry switch' if restored else 'Restore and switch', type='primary', disabled=not valid):
                try:
                    from portfolio_app.launcher import request_instance
                    # Do not create a destination if the launcher cannot own it.
                    info = request_instance(launch)
                    if not info or 'workspace' not in info:
                        raise DataError('Launch with portfolio-app or the desktop shortcut to restore and switch.')
                    already_switched = (restored is not None and
                                        info['workspace'] == dict(directory=str(restored), generation=generation + 1))
                    if info['workspace']['generation'] != generation and not already_switched:
                        raise DataError('The active portfolio changed. Cancel and review again.')
                    if restored is None:
                        with st.spinner('Restoring verified workspace…'):
                            restored = commit_restore(draft, Path(target), directory)
                        st.session_state['backup_restored'] = str(restored)
                    result = request_instance(launch, 'activate', payload=dict(directory=str(restored), generation=generation, digests=draft.digests))
                    if result is None:
                        raise DataError('No activation response. The restored folder is preserved; retry switching.')
                    if result.get('error'):
                        raise DataError(result['error'])
                    clear_backup()
                    st.session_state['active_portfolio'] = 'My portfolio'
                    st.rerun()
                except (DataError, OSError) as exc:
                    st.error(str(exc))
        if st.button('Cancel restore'):
            clear_backup()
            st.rerun()
    dialog()
    return True
