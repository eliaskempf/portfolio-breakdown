"""Shared wording and columns for allocation targets."""

import streamlit as st


def target_column_config() -> dict:
    return {
        "Current portfolio %": st.column_config.NumberColumn("Current (% of portfolio)", format="%.2f %%"),
        "Target portfolio %": st.column_config.NumberColumn("Target (% of portfolio)", format="%.2f %%"),
        "Gap (pp)": st.column_config.NumberColumn("Current − target (pp)", format="%+.2f",
                                                 help="Positive means above target; negative means below. Both use the whole portfolio."),
        "Known target portfolio %": st.column_config.NumberColumn("Known target subtotal (%)", format="%.2f %%"),
    }


def target_caption(*, valuation_complete: bool) -> None:
    if not valuation_complete:
        st.caption("Missing prices: whole-portfolio current percentages and gaps are unavailable.")
