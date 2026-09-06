"""Shared wording and columns for derived allocation targets."""

import streamlit as st


def target_column_config() -> dict:
    return {
        "Current portfolio %": st.column_config.NumberColumn("Current (% of portfolio)", format="%.2f %%"),
        "Target portfolio %": st.column_config.NumberColumn("Derived target (% of portfolio)", format="%.2f %%"),
        "Gap (pp)": st.column_config.NumberColumn("Current − target (pp)", format="%+.2f",
                                                 help="Positive means above target; negative means below. Both use the whole portfolio."),
        "Known target portfolio %": st.column_config.NumberColumn("Known target subtotal (%)", format="%.2f %%"),
    }


def target_caption(*, valuation_complete: bool) -> None:
    st.caption("Derived targets sum the selected positions’ saved targets, using the same label splits, ETF weights and display groups. "
               "Targets and gaps use the whole portfolio, without renormalizing after filters. "
               "Incomplete targets show their known subtotal and no gap; set missing position targets to complete them.")
    if not valuation_complete:
        st.caption("Some portfolio prices are missing. Derived targets remain available; whole-portfolio current percentages and gaps are blank until valuation is complete.")
