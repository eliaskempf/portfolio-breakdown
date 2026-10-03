"""Local SVG intro and session-scoped startup gate; no network or build step."""
from pathlib import Path
from uuid import uuid4
import streamlit as st
import streamlit.components.v1 as components

_component = components.declare_component(
    "portfolio_breakdown_intro", path=str(Path(__file__).parent / "intro_frontend")
)


def breakdown_intro(
    *, key: str = "breakdown_intro", height: int = 320,
    hold_ms: int = 950, swirl_ms: int = 1850,
    replay: bool = False, reduced_motion: bool = True,
) -> bool:
    """Render once per Streamlit session; return True after completion.

    Ordinary widget reruns do not restart the animation. Pass the one-shot
    result of st.button() as replay to restart deliberately. Keep a stable key.
    The completed state retains the exact original logo. A fresh browser
    connection/session plays again. Timing is in milliseconds.
    """
    if not 120 <= height <= 1000:
        raise ValueError("height must be between 120 and 1000 pixels")
    if not 0 <= hold_ms <= 10000 or not 300 <= swirl_ms <= 10000:
        raise ValueError("hold_ms must be 0–10000; swirl_ms must be 300–10000")
    state_key = f"__portfolio_intro_{key}"
    if state_key not in st.session_state or replay:
        st.session_state[state_key] = {"token": uuid4().hex, "completed": False}
    state = st.session_state[state_key]
    result = _component(
        key=key, run_token=state["token"], completed=state["completed"],
        height=int(height), hold_ms=int(hold_ms), swirl_ms=int(swirl_ms),
        reduced_motion=reduced_motion, default=None,
    )
    if result == state["token"]:
        state["completed"] = True
    return bool(state["completed"])


def render_startup_intro() -> bool:
    """Return False while playing. Skip remains usable if the component fails."""
    if st.session_state.get('startup_intro_done'):
        return True
    with st.container(key='startup_intro'):
        st.html('''<style>
        [data-testid="stApp"]:has(.st-key-startup_intro) {background:#11151c;color:#e3e7ef;}
        .st-key-startup_intro {padding-top:clamp(24px,12vh,140px);}
        .st-key-startup_intro button {color:#e3e7ef!important;border-color:#4b566c!important;}
        </style>''')
        done = breakdown_intro(height=360)
        with st.container(horizontal=True, horizontal_alignment='center'):
            skip = st.button('Skip intro', type='tertiary')
        if done or skip:
            st.session_state['startup_intro_done'] = True
            st.rerun()
    return False
