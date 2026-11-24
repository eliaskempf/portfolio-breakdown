"""Shared native chart, history controls and loading states for position dialogs."""
from portfolio_app.interface.shared.chart_hover import chart_hover, money_history_axes
from dataclasses import replace

import plotly.graph_objects as go
import streamlit as st

from portfolio_app.interface.shared.background_ui import status_fragment
from portfolio_app.interface.shared.render_sections import render_sections

from portfolio_app.interface.shared.charts import chart_config, style_figure, PALETTE
from portfolio_app.interface.shared.currency_display import reporting_currency
from portfolio_app.market.history import HistoryService, DemoHistoryProvider, PERIODS
from portfolio_app.market.market_data import history_for


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
            from portfolio_app.market.history import convert_price_history
            legs = {}
            for units in {result.currency, history_currency} - {'EUR'}:
                symbol = f'{units}EUR=X'
                requested.append(symbol)
                legs[units] = service.get(symbol, period)
            result = convert_price_history(result, history_currency, legs)
        return result, requested

    result, requested = read(use_initial=period == saved_period)
    pending = not demo and any(service.pending(symbol, period) for symbol in requested)
    from portfolio_app.interface.shared.dialog_runtime import local_fragment_updates

    @st.fragment
    def market_result():
        if is_active is not None and not is_active():
            return
        sections = render_sections(key, 'market_plot', 'market_description', 'market_status')
        if result.prices:
            with sections['market_plot']:
                _draw_prices(result, index_points)
            if not compact:
                sections['market_description'].caption(caption.format(currency=result.currency))

        @status_fragment()
        @local_fragment_updates()
        def progress(poll):
            nonlocal result, requested, pending
            if is_active is not None and not is_active():
                return
            if pending and not any(service.pending(symbol, period) for symbol in requested):
                updated, requested = read()
                pending = any(service.pending(symbol, period) for symbol in requested)
                if not pending:
                    previous, result = result, updated
                    if (result.dates, result.prices, result.currency) != (previous.dates, previous.prices, previous.currency):
                        st.rerun()
            details = render_sections(key, 'market_retrieved', 'market_note', 'market_refresh')
            if result.prices and not compact:
                details['market_retrieved'].caption(f'History last retrieved: {result.fetched_at}')
            if result.note and (not compact or result.status not in {'fresh', 'cached'}):
                message = details['market_note'].warning if result.status == 'stale' else details['market_note'].info
                message(result.note)
            with details['market_refresh']:
                if pending:
                    st.caption('Updating market-price history in the background…')
                    poll(2)
                elif not demo and result.status in {'stale', 'unavailable'} and ticker and not manual:
                    if st.button('Retry history', key=key+'retry'):
                        service.get(ticker, period, refresh=True)
                        pending = True
                        st.rerun(scope='fragment')

        with sections['market_status']:
            progress()
    market_result()
    return result


def _draw_prices(result, index_points):
    units = 'Index points' if index_points else result.currency
    figure = style_figure(go.Figure(go.Scatter(x=result.dates, y=result.prices, mode='lines',
        name='Price', line=dict(color=PALETTE[0], width=2.5), hovertemplate='Price: %{y:,.2f} '+units+'<extra></extra>')))
    figure.update_layout(height=340, yaxis_title=units, margin=dict(l=0, r=0, t=12, b=0))
    if not index_points:
        money_history_axes(figure, result.currency)
    st.plotly_chart(chart_hover(figure), width='stretch', config=chart_config())
