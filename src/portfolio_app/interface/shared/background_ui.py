"""Keep background collection from interrupting an open editor or detail view."""
import streamlit as st


def refresh_allowed():
    state = st.session_state
    return not (state.get('position_edit_dialog') or state.get('position_edit_open_request')
                or any(value for key, value in state.items()
                       if key.endswith(('_dialog_open', '_dialog_requested'))))


def poll_while_mounted(key, milliseconds):
    """Send ticks to the owning status fragment only while work is pending."""
    timer = st.components.v2.component('background_status_timer', js="""
        export default function({data, setTriggerValue, parentElement}) {
            clearInterval(parentElement.pollTimer);
            parentElement.pollTimer = setInterval(() => {
                if (parentElement.isConnected) setTriggerValue('tick', Date.now());
                else clearInterval(parentElement.pollTimer);
            }, data);
            return () => clearInterval(parentElement.pollTimer);
        }
    """)
    timer(key=key, data=milliseconds, on_tick_change=lambda: None)
