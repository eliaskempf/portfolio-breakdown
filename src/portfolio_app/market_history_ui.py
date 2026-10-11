"""Shared native chart, history controls and loading states for position dialogs."""
from dataclasses import replace

import plotly.graph_objects as go
import streamlit as st

from portfolio_app.charts import chart_config, style_figure
from portfolio_app.currency_display import reporting_currency
from portfolio_app.history import HistoryService, DemoHistoryProvider, PERIODS
from portfolio_app.market_data import history_for


def render_market_history(ticker, data_dir, *, key, demo=False, manual=False,
                          heading='Market-price history', index_points=False,
                          expected_currency=None, service=None, compact=False,
                          caption='Instrument closing prices in {currency} · Excludes dividend reinvestment · Not your personal return history'):
    if not compact:
        st.markdown(f'**{heading}**')
    period = st.segmented_control('Period', list(PERIODS), default='1Y', key=key+'period') or '1Y'
    if service is None:
        service = HistoryService(DemoHistoryProvider(), data_dir / '.cache' / 'history') if demo else history_for(data_dir)
    result = service.get(ticker, period, manual=manual)
    history_units = ('Native currency' if index_points else st.segmented_control(
        'History currency', ['Portfolio currency', 'Native currency'], default='Portfolio currency', key=key+'currency'))
    if result.prices and expected_currency and result.currency != expected_currency:
        result = replace(result, dates=(), prices=(), status='unavailable',
                         note='History returned an unexpected currency; the reference chart is withheld.')
    requested = [ticker]
    if result.prices and history_units != 'Native currency' and result.currency != reporting_currency():
        from portfolio_app.history import convert_price_history
        legs = {}
        for units in {result.currency, reporting_currency()} - {'EUR'}:
            symbol = f'{units}EUR=X'
            requested.append(symbol)
            legs[units] = service.get(symbol, period)
        result = convert_price_history(result, reporting_currency(), legs)
    pending = not demo and any(service.pending(symbol, period) for symbol in requested)

    @st.fragment(run_every=.5 if pending else None)
    def progress():
        if not st.session_state.get('position_edit_dialog'):
            return
        if pending and not any(service.pending(symbol, period) for symbol in requested):
            st.rerun()
        if pending:
            st.caption('Updating market-price history in the background…')
    progress()
    if result.prices:
        units = 'Index points' if index_points else result.currency
        figure = style_figure(go.Figure(go.Scatter(x=result.dates, y=result.prices, mode='lines',
            line=dict(color='#5470c6', width=2), hovertemplate='%{x}<br>%{y:,.2f} '+units+'<extra></extra>')))
        figure.update_layout(height=300, yaxis_title=units, margin=dict(l=12, r=12, t=12, b=30))
        st.plotly_chart(figure, width='stretch', config=chart_config())
        if not compact:
            st.caption(caption.format(currency=result.currency))
            st.caption(f'History last retrieved: {result.fetched_at}')
    if result.note and (not compact or result.status not in {'fresh', 'cached'}):
        (st.warning if result.status == 'stale' else st.info)(result.note)
    if not demo and not pending and result.status in {'stale', 'unavailable'} and ticker and not manual:
        if st.button('Retry history', key=key+'retry'):
            service.get(ticker, period, refresh=True)
            st.rerun()
    return result
