"""Coalesce provider completions into one refresh of the visible metrics."""
import streamlit as st

from portfolio_app.analytics.analytics_service import load_metrics
from portfolio_app.analytics.fundamental_refresh import workspace_fundamentals
from portfolio_app.interface.shared.background_ui import refresh_allowed, status_fragment
from portfolio_app.interface.shared.dialog_runtime import local_dialog_updates


def view_metrics(holdings, data_dir, *, demo=False, refresh=False, dialog=False, editing_key=None):
    if demo:
        return load_metrics(holdings, data_dir, demo=True, refresh=refresh)

    reader = workspace_fundamentals(data_dir)
    # Concurrent windows share active work. Keep this workspace's reader and its
    # write-failure cooldown alive for the lifetime of the current session.
    st.session_state['fundamentals_reader'] = reader
    snapshots, completed = reader.read(holdings, refresh=refresh)
    if completed is None:
        return snapshots

    mounted = False

    @status_fragment()
    @local_dialog_updates()
    def observe(poll):
        nonlocal mounted
        changed = completed() if mounted else None
        mounted = True
        in_position = dialog and st.session_state.get('position_edit_dialog')
        if changed and (in_position or refresh_allowed()) and not (editing_key and st.session_state.get(editing_key)):
            st.rerun()
        if changed is None:
            st.caption('Updating fundamentals…')
        if changed is not False:
            poll(.5)

    observe()
    return snapshots
