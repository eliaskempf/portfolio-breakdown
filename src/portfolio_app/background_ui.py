"""Keep background collection from interrupting an open editor or detail view."""
import streamlit as st


def refresh_allowed():
    state = st.session_state
    return not (state.get('position_edit_dialog') or state.get('position_edit_open_request')
                or any(value for key, value in state.items()
                       if key.endswith(('_dialog_open', '_dialog_requested'))))
