"""Position-dialog lifetime and refresh boundary shared by all child workflows.

The narrow Streamlit control-flow adapter is kept here: an ordinary child
``st.rerun()`` refreshes an open dialog, not the application behind it. Explicit
navigation/dismissal still leaves this boundary. Browser tests cover native
dismissal, nested views and financial writes against the supported runtime.
"""
from contextlib import contextmanager
from dataclasses import replace

import streamlit as st
from streamlit.runtime.scriptrunner import RerunException
from streamlit.runtime.scriptrunner_utils.script_run_context import RunLocation, ThreadState

HOST = 'position_dialog_host'


def route_position_callback():
    """Replace an opener/dismiss callback's default whole-application rerun."""
    if st.session_state.get('position_dialog_host_ready') and ThreadState.get().run_location == RunLocation.CALLBACK:
        st.rerun(HOST)


@contextmanager
def local_dialog_updates():
    fragment = ThreadState.get().fragment_id
    try:
        yield
    except RerunException as exc:
        request = exc.rerun_data
        if fragment and st.session_state.get('position_edit_dialog') and not request.fragment_id_queue:
            # st.rerun's request has already been consumed at its yield point.
            # Preserve all other request fields, including pending widget values.
            raise RerunException(replace(request, fragment_id_queue=[fragment],
                                        is_fragment_scoped_rerun=True)) from None
        raise


def close_position_button():
    """Use native dismissal for both X and Close, without a second server roundtrip."""
    component = st.components.v2.component('position_dialog_close',
        html='<button type="button">Close</button>',
        css='button {font:inherit;color:inherit;background:transparent;border:1px solid color-mix(in srgb,currentColor 25%,transparent);border-radius:6px;padding:.4rem .8rem;cursor:pointer;} button:focus-visible {outline:2px solid #5470c6;outline-offset:2px;}',
        js='''export default function(component) {
            const button=component.parentElement.querySelector('button');
            const close=()=>document.querySelector('[role="dialog"] button[aria-label="Close"]')?.click();
            button.addEventListener('click',close);
            return ()=>button.removeEventListener('click',close);
        }''')
    component(key='position_dialog_close')
