"""Keep background collection from interrupting an open editor or detail view."""
from functools import wraps

import streamlit as st
from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
from streamlit.runtime.scriptrunner_utils.script_run_context import ThreadState, get_script_run_ctx

from portfolio_app.interface.shared.fragment_handoff import install_fragment_handoff

install_fragment_handoff()


def refresh_allowed():
    state = st.session_state
    return not (state.get('position_edit_dialog') or state.get('position_edit_open_request')
                or any(value for key, value in state.items()
                       if key.endswith(('_dialog_open', '_dialog_requested'))))


def _set_poll_interval(seconds):
    """Use the same timer messages as Streamlit's run_every and fragment unmount."""
    ctx = get_script_run_ctx()
    fragment = ThreadState.get().fragment_id
    if ctx is None:
        return
    if fragment is None:
        raise RuntimeError('A status timer requires a mounted Streamlit fragment')
    message = ForwardMsg()
    if seconds is None:
        message.stop_auto_rerun.fragment_ids.append(fragment)
    else:
        message.auto_rerun.fragment_id = fragment
        message.auto_rerun.interval = seconds
    ctx.enqueue(message)


def status_fragment(*, run_every=None):
    """Poll a status fragment on demand without submitting a browser widget event."""
    def decorate(render):
        previous = object()  # A remounted fragment may inherit a client timer for its ID.

        @st.fragment()
        @wraps(render)
        def status():
            nonlocal previous
            interval = run_every

            def poll(seconds):
                nonlocal interval
                interval = seconds

            try:
                render(poll)
            finally:
                if interval != previous:
                    _set_poll_interval(interval)
                    previous = interval

        return status
    return decorate
