"""Shared visual styling for the local app; no portfolio calculations."""

from html import escape

import streamlit as st

from portfolio_app.grid_interactions import install_grid_interactions

CSS = """
<style>
.block-container {max-width:1440px;padding:2rem 2.5rem 3rem;}
[data-testid="stHeader"] {background:transparent;}
[data-testid="stSidebar"] {border-right:1px solid color-mix(in srgb,currentColor 12%,transparent);}
[data-testid="stSidebar"] h2 {font-size:.9rem;font-weight:600;margin-top:.7rem;}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {gap:.7rem;}
h1 {font-size:1.8rem!important;letter-spacing:-.5px;font-weight:600!important;padding-bottom:.4rem!important;}
h2 {font-size:1.15rem!important;font-weight:600!important;}
h3 {font-size:1rem!important;font-weight:600!important;}
[data-testid="stMetric"] {border:1px solid color-mix(in srgb,currentColor 14%,transparent);border-radius:6px;padding:16px 18px;}
[data-testid="stMetricValue"] {font-size:1.7rem;font-weight:600;font-variant-numeric:tabular-nums;}
[data-testid="stMetricLabel"] {font-size:.8rem;opacity:.75;}
[data-testid="stTabs"] [role="tablist"] {gap:24px;border-bottom:1px solid color-mix(in srgb,currentColor 14%,transparent);margin:4px 0 20px;}
[data-testid="stTabs"] [role="tab"] {padding:10px 0;font-size:.9rem;font-weight:500;}
[data-testid="stForm"] {border-radius:6px;padding:20px;}
[data-testid="stButton"] button,[data-testid="stFormSubmitButton"] button {border-radius:5px;font-weight:500;}
[data-testid="stExpander"] {border-radius:6px!important;}
[data-testid="stDataFrame"] {border-radius:5px;overflow:hidden;}
[data-testid="stPlotlyChart"] {border-radius:6px;}
.allocation-total {display:flex;flex-wrap:wrap;justify-content:space-between;gap:12px;border-bottom:1px solid color-mix(in srgb,currentColor 16%,transparent);padding:10px 0;font-size:.9rem;font-weight:600;}
.brand {font-size:1.1rem;font-weight:650;letter-spacing:-.25px;padding:4px 0 16px;}
.empty-state {border:1px solid color-mix(in srgb,currentColor 14%,transparent);border-radius:6px;padding:32px;}
[data-testid="stAppDeployButton"] {display:none;}
@media(max-width:700px) {.block-container{padding:3.5rem 1rem 2rem}h1{font-size:1.5rem!important}[data-testid="stMetric"]{padding:10px}}
</style>
"""


def apply_style() -> None:
    st.html(CSS)
    install_grid_interactions()
    st.sidebar.html('<div class="brand">Portfolio</div>')


def workspace_header(demo: bool) -> None:
    st.title("Portfolio")


def empty_overview() -> None:
    st.info("Add a position in Manage positions to get started.")


def allocation_total(label: str, value: float) -> None:
    st.html(f'<div class="allocation-total"><span>{escape(label)}</span><span>€{value:,.2f} · 100%</span></div>')
