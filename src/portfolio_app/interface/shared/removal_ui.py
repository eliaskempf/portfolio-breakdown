"""Review a saved configuration removal before committing it."""
from hashlib import sha256

import streamlit as st


def confirm_removal(action, *, scope, keep, consequence):
    # A native disclosure closes when its owner unmounts or the record changes.
    key = 'removal_' + sha256(repr(scope).encode()).hexdigest()[:20]
    review = st.popover(action, key=key, on_change='rerun')
    if not review.open:
        return False
    with review:
        st.caption(consequence)
        with st.container(horizontal=True):
            st.button(keep, key=key+'_keep', on_click=st.session_state.update, args=({key: False},))
            return st.button(action, key=key+'_confirm')
