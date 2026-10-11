"""Data-quality notices kept out of the portfolio's main views."""
import streamlit as st

from portfolio_app.etf_refresh_ui import render_refresh_status


def render_notifications(directory, funds, revision, valued, status, *, demo=False,
                         cache_warning='', exposure_issues=(), prices_pending=False):
    st.subheader('Notifications')
    render_refresh_status(directory, funds, revision, demo=demo)
    if prices_pending:
        st.info('Updating market prices…')
    manual = int(valued.price_status.eq('manual').sum())
    if manual:
        st.info(f'{manual} dated manual/snapshot prices · Update these manually or connect live prices.')
    if cache_warning:
        st.warning(cache_warning)
    if status.estimated_costs:
        st.info(f'{status.estimated_costs} positions use estimated FX costs.')
    for issue in dict.fromkeys(exposure_issues):
        st.warning(issue)
    if status.missing_costs or status.missing_prices or status.stale_quotes:
        st.markdown('**Prices & performance**')
        st.dataframe(status.problems, hide_index=True, width='stretch')
        st.button('Complete buy-ins' if status.missing_costs else 'Review pricing',
            on_click=lambda: st.session_state.update(main_tabs='Positions', positions_workflow_request='Update balances'))
