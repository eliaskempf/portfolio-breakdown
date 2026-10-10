"""Shared visual styling for the local app; no portfolio calculations."""

from portfolio_app.currency_display import currency_symbol

from html import escape

import streamlit as st
from portfolio_app.settings import GAIN_COLOR, LOSS_COLOR

from portfolio_app.grid_interactions import install_grid_interactions
from portfolio_app.metric_interactions import toggle_gain_unit

CSS = """
<style>
.block-container {max-width:1440px;padding:2rem 2.5rem 3rem;}
[data-testid="stHeader"] {background:transparent;}
/* Keep native, keyboard-accessible field help beside its label, not at the
   far edge of the input. Use semantic test IDs instead of generated classes. */
[data-testid="stWidgetLabel"] > div:has(> [data-testid="stTooltipIcon"]) {flex:0 0 auto;margin-inline-start:6px;}
.st-key-app_header {border-bottom:1px solid color-mix(in srgb,currentColor 12%,transparent);padding-bottom:16px;margin-bottom:8px;}
.app-brand {display:flex;align-items:center;gap:10px;font-size:1.3rem;font-weight:650;letter-spacing:-.5px;}
.app-brand img {width:38px;height:38px;flex-shrink:0;}
.st-key-app_header [data-testid="stHorizontalBlock"] {flex-wrap:wrap;gap:12px;}
.stDialog > div:has(.st-key-welcome_personal) {width:min(880px,calc(100vw - 32px))!important;max-width:880px;margin:clamp(24px,12vh,120px) auto!important;}
.st-key-welcome_demo,.st-key-welcome_personal {padding:24px!important;border-radius:16px!important;min-height:285px;}
.st-key-welcome_demo {background:color-mix(in srgb,#5470c6 12%,transparent);border-color:#5470c6!important;}
.st-key-welcome_personal {background:color-mix(in srgb,#4aa9b3 7%,transparent);}
.welcome-symbol {font-size:2.5rem;line-height:1.1;color:#5470c6;margin-bottom:12px;}
.st-key-welcome_demo .welcome-symbol {width:40px;height:40px;border-radius:50%;background:conic-gradient(#5470c6 0deg 220deg,#4aa9b3 220deg 310deg,#9a6dd7 310deg);font-size:0;}
.st-key-welcome_personal .welcome-symbol {color:#4aa9b3;}
.st-key-demo_loading {max-width:38rem;margin:clamp(2rem,8vh,6rem) auto;}
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
[class*="st-key-gain_positive_"] [data-testid="stMetricValue"] {color:var(--st-green-text-color,__GAIN_COLOR__);}
[class*="st-key-gain_negative_"] [data-testid="stMetricValue"] {color:var(--st-red-text-color,__LOSS_COLOR__);}
[data-testid="stTabs"] [role="tablist"] {gap:24px;border-bottom:1px solid color-mix(in srgb,currentColor 14%,transparent);margin:4px 0 20px;}
[data-testid="stTabs"] [role="tab"] {padding:10px 0;font-size:.9rem;font-weight:500;}
[data-testid="stForm"] {border-radius:6px;padding:20px;}
[data-testid="stButton"] button,[data-testid="stFormSubmitButton"] button {border-radius:5px;font-weight:500;}
/* The popover replaces its chevron when opening. Keep the click target on the
   stable button so the detached icon cannot be mistaken for an outside click. */
[data-testid="stPopoverButton"] * {pointer-events:none;}
[data-testid="stExpander"] {border-radius:6px!important;}
[data-testid="stDataFrame"] {border-radius:5px;overflow:hidden;}
[data-testid="stPlotlyChart"] {border-radius:6px;}
.allocation-total {display:flex;flex-wrap:wrap;justify-content:space-between;gap:12px;border-bottom:1px solid color-mix(in srgb,currentColor 16%,transparent);padding:10px 0;font-size:.9rem;font-weight:600;}
.brand {font-size:1.1rem;font-weight:650;letter-spacing:-.25px;padding:4px 0 16px;}
.empty-state {border:1px solid color-mix(in srgb,currentColor 14%,transparent);border-radius:6px;padding:32px;}
[data-testid="stAppDeployButton"] {display:none;}
@media(max-width:700px) {[data-testid="stHorizontalBlock"]{flex-wrap:wrap}[data-testid="stColumn"]{min-width:min(100%,260px)}.block-container{padding:3.5rem 1rem 2rem}h1{font-size:1.5rem!important}[data-testid="stMetric"]{padding:10px}}
</style>
""".replace("__GAIN_COLOR__", GAIN_COLOR).replace("__LOSS_COLOR__", LOSS_COLOR)


def mark_view_ready() -> None:
    """Signal completed empty/error views too, without adding layout space."""
    st.html('<style data-portfolio-view-ready="true">/* View rendered */</style>')


def apply_style() -> None:
    st.html(CSS)
    install_grid_interactions()


def workspace_header(demo: bool) -> None:
    st.title("Portfolio")


def empty_overview() -> None:
    st.info("Open Positions and choose Add position to get started.")


def allocation_total(label: str, value: float) -> None:
    st.html((f'<div class="allocation-total"><span>{escape(label)}</span><span>€{value:,.2f} · 100%</span></div>').replace('€', currency_symbol()))


def performance_metric(label: str, value: str, amount, *, key: str) -> None:
    """Keep signed performance readable even without colour or a known cost."""
    tone = 'positive' if amount is not None and amount > 0 else 'negative' if amount is not None and amount < 0 else 'neutral'
    with st.container(key=f'gain_{tone}_{key}'):
        st.metric(label, value)


def value_metric(value, performance, *, missing=0, percent=False, key, on_toggle_gain=None):
    """One value card with an adjacent, optionally clickable unrealized gain."""
    gain = performance.return_pct if percent else performance.gain_reporting
    gain_text = 'Unavailable' if gain is None else f'{gain:+,.2f}%' if percent else (f'{"-" if gain < 0 else "+"}€{abs(gain):,.2f}').replace('€', currency_symbol())
    with st.container(key=key):
        st.metric('Priced value' if missing else 'Current value', (f'€{value:,.2f}').replace('€', currency_symbol()),
                  delta=gain_text, delta_color='normal' if gain else 'off', delta_arrow='off',
                  delta_description='Return on cost' if percent else 'Unrealized gain / loss',
                  help=f'{missing} missing prices. Performance coverage: {performance.covered_count} of {performance.held_count} held positions with converted costs and available prices. Excludes dividends and realized gains.')
    if performance.estimated_count:
        st.caption(f'Estimated gains · {performance.estimated_count} positions use confirmed FX approximations')
    if on_toggle_gain:
        toggle_gain_unit(percent=percent, on_toggle=on_toggle_gain, container_key=key)
