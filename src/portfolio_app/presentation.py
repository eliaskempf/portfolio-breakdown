"""Shared visual styling for the local app; no portfolio calculations."""

from html import escape

import streamlit as st

from portfolio_app.grid_interactions import install_grid_interactions

CSS = """
<style>
.stApp {background:#f5f7f5;color:#1a3034;font-family:Inter,ui-sans-serif,system-ui,sans-serif;}
[data-testid="stHeader"] {background:transparent;}
[data-testid="stSidebar"] {background:#eaf0ec;border-right:1px solid #dce5df;}
[data-testid="stSidebar"] h2 {font-size:15px;letter-spacing:-.2px;margin-top:12px;}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {gap:.8rem;}
.block-container {max-width:1260px;padding:2.5rem 3rem 4rem;}
[data-testid="stCaptionContainer"] p {color:#657a70!important;}
h1 {font-size:2.6rem!important;letter-spacing:-1.5px;font-weight:650!important;color:#173b35;}
h2 {font-size:1.3rem!important;letter-spacing:-.45px;color:#24483e;}
h3 {letter-spacing:-.3px;}
[data-testid="stCaptionContainer"] {color:#6c7e79;line-height:1.55;}
[data-testid="stMetric"] {background:white;border:1px solid #e0e8e2;border-radius:16px;padding:22px 24px;box-shadow:0 3px 15px #173b3504;}
[data-testid="stMetricValue"] {font-size:2rem;font-weight:600;letter-spacing:-1px;color:#214c3f;}
[data-testid="stMetricLabel"] {color:#6b8078;font-size:12px;}
[data-testid="stTabs"] [role="tablist"] {gap:24px;border-bottom:1px solid #dce5df;margin:18px 0 26px;}
[data-testid="stTabs"] [role="tab"] {padding:12px 4px;font-size:14px;font-weight:600;color:#698277;}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] {color:#22694e;}
[data-baseweb="tab-highlight"] {background:#347d60;}
[data-testid="stForm"] {background:#fff;border:1px solid #dfe7e1;border-radius:16px;padding:22px;}
[data-testid="stButton"] button,[data-testid="stFormSubmitButton"] button {border-radius:9px;border-color:#d6e2da;font-weight:550;min-height:40px;}
button[kind="primary"],button[kind="primaryFormSubmit"] {background:#276c50!important;border-color:#276c50!important;color:white!important;}
[data-testid="stTextInput"] [data-baseweb="input"],[data-testid="stNumberInput"] [data-baseweb="input"],[data-baseweb="select"]>div {background:#fff;border-color:#dce5df;border-radius:8px;}
[data-testid="stExpander"] {border:1px solid #dfe7e1!important;border-radius:12px!important;background:#fff;}
[data-testid="stDataFrame"] {border-radius:12px;overflow:hidden;border:1px solid #e1e7e2;}
[data-testid="stPlotlyChart"] {background:#fff;border:1px solid #e1e7e2;border-radius:16px;}
.allocation-total {display:flex;flex-wrap:wrap;justify-content:space-between;gap:12px;background:#eaf0ec;border:1px solid #d5e1d9;border-radius:10px;padding:14px 18px;color:#24483e;font-weight:600;}
[data-testid="stAlert"] {border-radius:10px;font-size:13px;}
.brand {display:flex;align-items:center;gap:10px;font-size:20px;letter-spacing:-.7px;font-weight:700;color:#244c3c;padding:8px 0 22px;}
.brand-icon {display:grid;place-items:center;width:34px;height:34px;background:#2d6b50;color:#fff;border-radius:10px;font-size:24px;}
.eyebrow {display:flex;align-items:center;gap:10px;color:#6c8478;font-size:11px;font-weight:650;letter-spacing:1.5px;text-transform:uppercase;margin-bottom:8px;}
.pill {border:1px solid #d4e4d9;background:#eaf3eb;color:#397251;border-radius:20px;padding:4px 9px;letter-spacing:.4px;font-size:10px;}
.empty-state {background:#fff;border:1px solid #dfe8e0;border-radius:18px;padding:48px 32px;margin:10px 0;text-align:center;}
.empty-state .symbol {font-size:42px;color:#79a187;margin-bottom:14px;}
.empty-state h2 {font-size:25px!important;margin-bottom:8px;}.empty-state p {color:#708277;font-size:14px;}
@media(max-width:700px) {.block-container{padding:4.5rem 1rem 3rem}h1{font-size:2rem!important}[data-testid="stMetric"]{padding:16px}.empty-state{padding:30px 16px}}
</style>
"""


def apply_style() -> None:
    st.html(CSS)
    install_grid_interactions()
    st.sidebar.html('<div class="brand"><span class="brand-icon">◈</span> Portfolio</div>')


def workspace_header(demo: bool) -> None:
    label = "Demo workspace" if demo else "Personal workspace"
    badge = "Resets on restart" if demo else "Saved on your device"
    st.html(f'<div class="eyebrow">{label}<span class="pill">{badge}</span></div>')
    st.title("Your portfolio")
    st.caption("A clear view of what you own, and where you're invested.")


def empty_overview() -> None:
    st.html('<div class="empty-state"><div class="symbol">◈</div><h2>Start with your first investment</h2><p>Add a position using Manage positions to start exploring your portfolio.</p><p>Search for a stock or ETF, enter your shares, and see your allocation take shape.</p></div>')


def allocation_total(label: str, value: float) -> None:
    st.html(f'<div class="allocation-total"><span>{escape(label)}</span><span>€{value:,.2f} · 100%</span></div>')
