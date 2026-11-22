"""Read-only controls rendered from the current view's financial inputs."""
import streamlit as st


def render_view_fragment(key, draw, *, current=None):
    if current is None:
        draw()
        return
    mounted = False

    @st.fragment(key=key)
    def panel():
        nonlocal mounted
        if mounted and not current():
            st.rerun()
        mounted = True
        draw()

    panel()


def render_detail_panel(label, key, draw, *, current=None):
    def panel():
        section = st.expander(label, key=key, on_change='rerun')
        if section.open:
            with section:
                draw()

    render_view_fragment(key + '_contents', panel, current=current)
