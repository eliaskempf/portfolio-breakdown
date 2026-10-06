"""Performance summary with explicit coverage and currency semantics."""

from portfolio_app.currency_display import currency_symbol

import streamlit as st

from portfolio_app.performance import summarize_performance


def render_performance_summary(performance) -> None:
    summary = summarize_performance(performance)
    if summary.covered_count:
        first, second, third = st.columns(3)
        first.metric("Unrealized gain / loss", (f"€{summary.gain_reporting:+,.2f}").replace('€', currency_symbol()))
        second.metric("Return on cost", f"{summary.return_pct:+.2f}%" if summary.return_pct is not None else "—")
        third.metric("Cost basis", (f"€{summary.cost_reporting:,.2f}").replace('€', currency_symbol()))
        st.caption(f"Coverage: {summary.covered_count} of {summary.held_count} held positions · comparable converted costs")
    else:
        st.info("Add average buy-in prices and their currencies in Manage positions to see performance. "
                "The summary requires complete converted costs. Original purchase currencies remain available in position details.")
    if summary.estimated_count:
        st.warning(f"Gains include {summary.estimated_count} positions with confirmed FX estimates.")
    st.caption("Unrealized performance · Whole portfolio · Excludes dividends and realized gains")


def performance_column_config(*, percent: bool, grouped: bool = True) -> dict:
    return {"Performance": st.column_config.NumberColumn(
        "Return (%)" if percent else "Gain / loss" if grouped else "Gain / loss",
        format="%+.2f %%" if percent else ("€ %+.2f").replace('€', currency_symbol()) if grouped else "%+.2f",
        help="Unrealized gain divided by matching total cost. Partial means only covered positions contribute." if percent else
             "Unrealized gain on covered positions." if grouped else "Amount in portfolio reporting currency."),
        "Performance coverage": st.column_config.TextColumn(help="Complete: all held contributions have converted costs and valuations. Partial: some contributions are excluded. Unavailable: no comparable performance. Estimated: includes confirmed FX approximations.")}
