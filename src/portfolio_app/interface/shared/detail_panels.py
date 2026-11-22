"""Read-only details rendered on demand from the current view's financial inputs."""
import streamlit as st


def render_detail_panel(label, key, draw, *, current=None):
    mounted = False

    def panel():
        nonlocal mounted
        if mounted and current and not current():
            st.rerun()
        mounted = True
        section = st.expander(label, key=key, on_change='rerun')
        if section.open:
            with section:
                draw()

    if current is None:
        panel()
    else:
        st.fragment(key=key + '_contents')(panel)()
