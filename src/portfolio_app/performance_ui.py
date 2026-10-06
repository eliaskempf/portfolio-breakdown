"""Performance summary with explicit coverage and currency semantics."""

import streamlit as st

from portfolio_app.performance import summarize_performance


def render_performance_summary(performance) -> None:
    summary = summarize_performance(performance)
    if summary.covered_count:
        first, second, third = st.columns(3)
        first.metric("Unrealized gain / loss (EUR)", f"€{summary.gain_eur:+,.2f}", help='Current value minus known purchase cost in EUR. Missing or incompatible costs are excluded.')
        second.metric("Return on cost", f"{summary.return_pct:+.2f}%" if summary.return_pct is not None else "—", help='Unrealized gain divided by matching known purchase cost; partial coverage remains indicated.')
        third.metric("Cost basis (EUR)", f"€{summary.cost_eur:,.2f}", help='Recorded purchase cost for holdings covered by EUR cost data.')
        st.caption(f"Coverage: {summary.covered_count} of {summary.held_count} held positions · EUR buy-ins only")
    else:
        st.info("Add average buy-in prices and their currencies in Manage positions to see performance. "
                "The EUR summary requires EUR buy-ins; other currencies appear in the holdings table.")
    st.caption("Unrealized performance · Whole portfolio · Excludes dividends and realized gains")


def performance_column_config(*, percent: bool, grouped: bool = True) -> dict:
    return {"Performance": st.column_config.NumberColumn(
        "Return (%)" if percent else "Gain / loss (EUR)" if grouped else "Gain / loss",
        format="%+.2f %%" if percent else "€ %+.2f" if grouped else "%+.2f",
        help="Unrealized gain divided by matching total cost. Partial means only covered positions contribute." if percent else
             "Unrealized gain on covered EUR-cost positions." if grouped else "Amount in the Buy-in currency column."),
        "Performance coverage": st.column_config.TextColumn(help="Complete: all held contributions have EUR costs and valuations. Partial: some contributions are excluded. Unavailable: no comparable performance.")}
