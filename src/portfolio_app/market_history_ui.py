"""Shared native chart, history controls and loading states for position dialogs."""
from portfolio_app.chart_hover import chart_hover, money_history_axes
from dataclasses import replace

import plotly.graph_objects as go
import streamlit as st

from portfolio_app.charts import chart_config, style_figure, PALETTE
from portfolio_app.currency_display import reporting_currency
from portfolio_app.history import HistoryService, DemoHistoryProvider, PERIODS
from portfolio_app.market_data import history_for


def render_market_history(ticker, data_dir, *, key, demo=False, manual=False,
                          heading='Market-price history', index_points=False, is_active=None,
                          expected_currency=None, native_currency=None, service=None, compact=False,
                          caption='Instrument closing prices in {currency} · Excludes dividend reinvestment · Not your personal return history'):
    if not compact and heading:
        st.markdown(f'**{heading}**')
    if service is None:
        service = HistoryService(DemoHistoryProvider(), data_dir / '.cache' / 'history') if demo else history_for(data_dir)
    # Valuation/contract metadata can identify units while the first chart loads.
    saved_period = st.session_state.get(key+'period') or '1Y'
    initial = service.get(ticker, saved_period, manual=manual)
    native = initial.currency or expected_currency or native_currency
    with st.container(horizontal=True, horizontal_alignment='distribute', vertical_alignment='bottom'):
        period = st.segmented_control('Period', list(PERIODS), default='1Y', required=True, key=key+'period', label_visibility='collapsed') or '1Y'
        history_currency = reporting_currency()
        if not index_points and native and native != reporting_currency():
            history_currency = st.selectbox('Currency', [reporting_currency(), native], key=key+'currency_code', width=110,
                help='Chart currency. Conversion uses historical exchange rates.')

    def read(*, use_initial=False):
        result = initial if use_initial else service.get(ticker, period, manual=manual)
        if result.prices and expected_currency and result.currency != expected_currency:
            result = replace(result, dates=(), prices=(), status='unavailable',
                             note='History returned an unexpected currency; the reference chart is withheld.')
        requested = [ticker]
        if result.prices and not index_points and result.currency != history_currency:
            from portfolio_app.history import convert_price_history
            legs = {}
            for units in {result.currency, history_currency} - {'EUR'}:
                symbol = f'{units}EUR=X'
                requested.append(symbol)
                legs[units] = service.get(symbol, period)
            result = convert_price_history(result, history_currency, legs)
        return result, requested

    result, requested = read(use_initial=period == saved_period)
    pending = not demo and any(service.pending(symbol, period) for symbol in requested)
    completed = not pending

    from portfolio_app.dialog_runtime import local_dialog_updates
    @st.fragment(run_every=2 if pending else None)
    @local_dialog_updates()
    def progress():
        nonlocal result, requested, completed
        if is_active is not None and not is_active():
            return
        if not completed and not any(service.pending(symbol, period) for symbol in requested):
            result, requested = read()
            completed = not any(service.pending(symbol, period) for symbol in requested)
        _draw_history(result, index_points=index_points, compact=compact, caption=caption)
        if not completed:
            st.caption('Updating market-price history in the background…')
        elif not demo and result.status in {'stale', 'unavailable'} and ticker and not manual:
            if st.button('Retry history', key=key+'retry'):
                service.get(ticker, period, refresh=True)
                st.rerun()
    progress()
    return result


def _draw_history(result, *, index_points, compact, caption):
    if result.prices:
        units = 'Index points' if index_points else result.currency
        figure = style_figure(go.Figure(go.Scatter(x=result.dates, y=result.prices, mode='lines',
            name='Price', line=dict(color=PALETTE[0], width=2.5), hovertemplate='Price: %{y:,.2f} '+units+'<extra></extra>')))
        figure.update_layout(height=340, yaxis_title=units, margin=dict(l=0, r=0, t=12, b=0))
        if not index_points:
            money_history_axes(figure, result.currency)
        st.plotly_chart(chart_hover(figure), width='stretch', config=chart_config())
        if not compact:
            st.caption(caption.format(currency=result.currency))
            st.caption(f'History last retrieved: {result.fetched_at}')
    if result.note and (not compact or result.status not in {'fresh', 'cached'}):
        (st.warning if result.status == 'stale' else st.info)(result.note)
