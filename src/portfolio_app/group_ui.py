"""Controls for the optional SMH and direct-stock display group."""

from hashlib import sha256

import streamlit as st

from portfolio_app.display_names import display_name
from portfolio_app.etf import smh_group_candidates
from portfolio_app.grouping import InstrumentGroup, group_members_table


def smh_group_control(holdings, funds) -> InstrumentGroup | None:
    fund_ids, candidates, defaults = smh_group_candidates(holdings, funds)
    if not fund_ids:
        return None
    enabled = st.checkbox("Group SMH with related stocks", key="allocation_group_smh",
                          help="Combine the UCITS ETF and chosen directly held constituents for allocation analysis. Saved positions remain separate.")
    if not enabled:
        return None
    names = {row.id: display_name(row.name) for row in holdings.itertuples()}
    choices = sorted(candidates, key=lambda asset: (names[asset], asset))
    signature = sha256(repr([(asset, names[asset]) for asset in choices]).encode()).hexdigest()[:16]
    selected = st.multiselect("Stocks in the SMH group", choices, default=sorted(defaults), format_func=names.get,
                              key=f"allocation_group_stocks_{signature}",
                              help="Nvidia and TSMC start selected when available. Other matching stocks from the local ETF snapshot can also be included. Position filters still apply.")
    return InstrumentGroup("view-group:smh-related", "SMH + related stocks", frozenset(fund_ids | set(selected)), frozenset(fund_ids))


def render_group_members(selected, group: InstrumentGroup) -> None:
    st.caption("SMH + related stocks is a display group, counted as one asset and classified like the ETF. "
               "It includes the full selected ETF value plus the chosen direct stocks, each once. "
               "In look-through mode, this group keeps SMH together; Effective exposure below still shows the underlying companies.")
    with st.expander("SMH group members"):
        table = group_members_table(selected, group)
        table["Investment"] = table["Investment"].map(display_name)
        if table.empty:
            st.info("No group members match the current position filters.")
        else:
            st.dataframe(table, hide_index=True, height="content", width="stretch", column_config={
                "EUR value": st.column_config.NumberColumn(format="€ %.2f"),
                "Within group (%)": st.column_config.NumberColumn(format="%.2f %%"),
            })
