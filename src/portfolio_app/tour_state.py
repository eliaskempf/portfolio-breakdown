"""Suspend feature-owned view inputs without restoring widget triggers."""
import streamlit as st
from portfolio_app.state_lifecycle import capture_tour, transition


def capture_view() -> dict:
    return capture_tour(st.session_state)


def clear_view() -> None:
    transition(st.session_state, 'tour')
