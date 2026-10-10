"""Retain user inputs when lazy views unmount their widgets."""
import streamlit as st


def preserve_view_inputs():
    from portfolio_app.state_lifecycle import retained_inputs
    for key in retained_inputs(st.session_state):
        st.session_state[key] = st.session_state[key]


def persistent_editor(data, *, key, **kwargs):
    """Keep edited rows across unmounts without assigning read-only widget state.

    Callers include source revisions in keys, so a successful save or external
    edit starts a fresh draft. The editor's baseline stays fixed while mounted.
    """
    drafts = st.session_state.setdefault('view_editor_drafts', {})
    bases = st.session_state.setdefault('view_editor_bases', {})
    if key not in st.session_state or key not in bases:
        bases[key] = drafts.get(key, data).copy(deep=True)
    edited = st.data_editor(bases[key], key=key, **kwargs)
    drafts[key] = edited.copy(deep=True)
    # Bound obsolete revisions retained during a long editing session.
    while len(drafts) > 32:
        oldest = next(iter(drafts))
        drafts.pop(oldest)
        bases.pop(oldest, None)
    return edited


def reset_workspace():
    """Discard workspace-owned forms/results before switching portfolios."""
    from portfolio_app.state_lifecycle import transition
    transition(st.session_state, 'workspace')
