"""Shared visual styling for the local app; no portfolio calculations."""

from html import escape

import streamlit as st

from portfolio_app.grid_interactions import install_grid_interactions
from portfolio_app.metric_interactions import toggle_gain_unit

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
:is(.st-key-overview_value,.st-key-exposure_value) [data-testid="stMetric"] > div {display:grid;grid-template-columns:max-content minmax(0,1fr);column-gap:16px;align-items:center;}
:is(.st-key-overview_value,.st-key-exposure_value) [data-testid="stMetricLabel"] {grid-column:1 / -1;}
:is(.st-key-overview_value,.st-key-exposure_value) [data-testid="stMetricValue"] {grid-column:1;grid-row:2;}
:is(.st-key-overview_value,.st-key-exposure_value) [data-testid="stMetric"] > div > div:has(> [data-testid="stMetricDelta"]) {grid-column:2;grid-row:2;margin-top:0;flex-wrap:wrap;}
.st-key-overview_allocation {container-type:inline-size;}
.st-key-overview_allocation_summary {transform:translateY(-24px);}
:is(.st-key-overview_value,.st-key-exposure_value) [data-testid="stMetricDelta"][role="button"] {cursor:pointer;}
:is(.st-key-overview_value,.st-key-exposure_value) [data-testid="stMetricDelta"][role="button"]:hover {filter:brightness(.85);}
:is(.st-key-overview_value,.st-key-exposure_value) [data-testid="stMetricDelta"][role="button"]:focus-visible {outline:2px solid currentColor;outline-offset:3px;}
.st-key-exposure_theme_results {container-type:inline-size;}
.st-key-exposure_toolbar [data-testid="stColumn"] {min-width:0;}
@container(max-width:860px) {
  .st-key-overview_allocation_summary {transform:none;}
  .st-key-overview_allocation [data-testid="stHorizontalBlock"] {flex-direction:column;align-items:stretch;}
  .st-key-overview_allocation [data-testid="stColumn"] {width:100%!important;flex:1 1 100%;min-width:0;}
  .st-key-exposure_theme_results [data-testid="stHorizontalBlock"] {flex-direction:column;align-items:stretch;}
  .st-key-exposure_theme_results [data-testid="stColumn"] {width:100%!important;flex:1 1 100%;min-width:0;}
}
[class*="st-key-gain_positive_"] [data-testid="stMetricValue"] {color:var(--st-green-text-color,#27836c);}
[class*="st-key-gain_negative_"] [data-testid="stMetricValue"] {color:var(--st-red-text-color,#b84655);}
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
@media(max-width:700px) {[data-testid="stHorizontalBlock"]{flex-wrap:wrap}[data-testid="stColumn"]{min-width:min(100%,260px)}.block-container{padding:3.5rem 1rem 2rem}h1{font-size:1.5rem!important}[data-testid="stMetric"]{padding:10px}}
</style>
"""


def apply_style() -> None:
    st.html(CSS)
    install_grid_interactions()
    st.sidebar.html('<div class="brand">Portfolio</div>')


def workspace_header(demo: bool) -> None:
    st.title("Portfolio")


def empty_overview() -> None:
    st.info("Open Positions and choose Add position to get started.")


def allocation_total(label: str, value: float) -> None:
    st.html(f'<div class="allocation-total"><span>{escape(label)}</span><span>€{value:,.2f} · 100%</span></div>')


def performance_metric(label: str, value: str, amount, *, key: str) -> None:
    """Keep signed performance readable even without colour or a known cost."""
    tone = 'positive' if amount is not None and amount > 0 else 'negative' if amount is not None and amount < 0 else 'neutral'
    with st.container(key=f'gain_{tone}_{key}'):
        st.metric(label, value)


def value_metric(value, performance, *, missing=0, percent=False, key, on_toggle_gain=None):
    """One value card with an adjacent, optionally clickable unrealized gain."""
    gain = performance.return_pct if percent else performance.gain_eur
    gain_text = 'Unavailable' if gain is None else f'{gain:+,.2f}%' if percent else f'{"-" if gain < 0 else "+"}€{abs(gain):,.2f}'
    with st.container(key=key):
        st.metric('Priced value' if missing else 'Current value', f'€{value:,.2f}',
                  delta=gain_text, delta_color='normal' if gain else 'off', delta_arrow='off',
                  delta_description='Return on cost' if percent else 'Unrealized gain / loss',
                  help=f'{missing} missing prices. Performance coverage: {performance.covered_count} of {performance.held_count} held positions with EUR buy-ins and available prices. Excludes dividends and realized gains.')
    if on_toggle_gain:
        toggle_gain_unit(percent=percent, on_toggle=on_toggle_gain, container_key=key)
