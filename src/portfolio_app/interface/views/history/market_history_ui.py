"""Shared native chart, history controls and loading states for position dialogs."""
from portfolio_app.interface.shared.chart_hover import chart_hover, money_history_axes
from dataclasses import dataclass, replace

import plotly.graph_objects as go
import streamlit as st

from portfolio_app.interface.shared.background_ui import status_fragment
from portfolio_app.interface.shared.dialog_runtime import local_fragment_updates
from portfolio_app.interface.shared.render_sections import render_sections

from portfolio_app.interface.shared.charts import chart_config, style_figure, PALETTE
from portfolio_app.interface.shared.currency_display import reporting_currency
from portfolio_app.market.history import HistoryResult, HistoryService, DemoHistoryProvider, PERIODS
from portfolio_app.market.market_data import BackgroundHistory, history_for


@dataclass
class _MarketReading:
    period: str
    units: str
    source: tuple[HistoryResult, bool]
    display: HistoryResult
    retry_needed: dict[str, bool]
    pending: bool


def render_market_history(ticker, data_dir, *, key, demo=False, manual=False,
                          heading='Market-price history', index_points=False, is_active=None,
                          expected_currency=None, native_currency=None, service=None, compact=False, render_details=None,
                          caption='Instrument closing prices in {currency} · Excludes dividend reinvestment · Not your personal return history'):
    if not compact and heading:
        st.markdown(f'**{heading}**')
    if service is None:
        service = HistoryService(DemoHistoryProvider(data_dir), data_dir / '.cache' / 'history') if demo else history_for(data_dir)
    def quote(symbol, period, **options):
        if isinstance(service, BackgroundHistory):
            return service.snapshot(symbol, period, **options)
        result = service.get(symbol, period, **options)
        return result, not demo and service.pending(symbol, period)

    def selectable_currency(source):
        native = source[0].currency or expected_currency or native_currency
        return native if native and not index_points and native != reporting_currency() else None

    def read(source, period, history_currency):
        result, pending = source
        if result.prices and expected_currency and result.currency != expected_currency:
            result = replace(result, dates=(), prices=(), status='unavailable',
                             note='History returned an unexpected currency; the reference chart is withheld.')
        # Keep each input's retry state before conversion combines their statuses.
        retry_needed = {ticker: result.status in {'stale', 'unavailable'}}
        if result.prices and not index_points and result.currency != history_currency:
            from portfolio_app.market.history import convert_price_history
            legs = {}
            for units in {result.currency, history_currency} - {'EUR'}:
                symbol = f'{units}EUR=X'
                legs[units], checking = quote(symbol, period)
                fx = legs[units]
                retry_needed[symbol] = not fx.prices or fx.currency != 'EUR' or fx.status in {'stale', 'unavailable'}
                pending = pending or checking
            result = convert_price_history(result, history_currency, legs)
        return _MarketReading(period, history_currency, source, result, retry_needed, pending)

    publication = None
    @st.fragment
    def market_result():
        nonlocal publication
        if is_active is not None and not is_active():
            return
        ready, publication = publication, None
        with st.container(horizontal=True, horizontal_alignment='distribute', vertical_alignment='bottom'):
            period = st.segmented_control('Period', list(PERIODS), default='1Y', required=True,
                                          key=key+'period', label_visibility='collapsed') or '1Y'
            if ready is not None and ready.period != period:
                ready = None
            source = ready.source if ready is not None else quote(ticker, period, manual=manual)
            native = selectable_currency(source)
            units = reporting_currency()
            if native:
                units = st.selectbox('Currency', [units, native], key=key+'currency_code', width=110,
                    help='Chart currency. Conversion uses historical exchange rates.')
        reading = ready if ready is not None and ready.units == units else read(source, period, units)
        result = reading.display
        sections = render_sections(key, 'market_plot', 'market_description', 'market_status')
        if result.prices:
            with sections['market_plot']:
                _draw_prices(result, index_points)
            if not compact:
                sections['market_description'].caption(caption.format(currency=result.currency))

        @local_fragment_updates()
        def redraw_chart():
            st.rerun()

        mounted = False
        @st.fragment
        def market_status():
            if is_active is not None and not is_active():
                return
            result = reading.display
            details = render_sections(key, 'market_retrieved', 'market_note', 'market_refresh')
            if result.prices and not compact:
                details['market_retrieved'].caption(f'History last retrieved: {result.fetched_at}')
            if result.note and (not compact or result.status not in {'fresh', 'cached'}):
                message = details['market_note'].warning if result.status == 'stale' else details['market_note'].info
                message(result.note)
            with details['market_refresh']:
                if reading.pending:
                    st.caption('Updating market-price history in the background…')
                elif not demo and result.status in {'stale', 'unavailable'} and ticker and not manual:
                    if st.button('Retry history', key=key+'retry'):
                        for symbol, needs_retry in reading.retry_needed.items():
                            if needs_retry:
                                service.get(symbol, period, refresh=True)
                        reading.pending = True
                        st.rerun(scope='fragment')
            if render_details is not None:
                render_details(result)

            @status_fragment()
            @local_fragment_updates()
            def watch(poll):
                nonlocal publication, reading
                if not reading.pending or (is_active is not None and not is_active()):
                    return
                if any(service.pending(symbol, period) for symbol in reading.retry_needed):
                    poll(2)
                    return
                if not mounted:
                    # Finish mounting the enclosing page before publishing locally.
                    poll(.1)
                    return
                source = quote(ticker, period, manual=manual)
                updated = read(source, period, units)
                options_changed = selectable_currency(source) != native
                # A valid displayed series stays visible until its replacement
                # is complete. With no series yet, offer newly known native units.
                if updated.pending and (reading.display.prices or not options_changed):
                    reading.retry_needed = updated.retry_needed
                    poll(2)
                    return
                previous, reading = reading.display, updated
                current = updated.display
                if options_changed or (current.dates, current.prices, current.currency) != (previous.dates, previous.prices, previous.currency):
                    publication = updated
                    redraw_chart()
                st.rerun()  # Only status and source details changed.
            watch()

        with sections['market_status']:
            market_status()
        mounted = True
        return reading.display
    return market_result()


def _draw_prices(result, index_points):
    units = 'Index points' if index_points else result.currency
    figure = style_figure(go.Figure(go.Scatter(x=result.dates, y=result.prices, mode='lines',
        name='Price', line=dict(color=PALETTE[0], width=2.5), hovertemplate='Price: %{y:,.2f} '+units+'<extra></extra>')))
    figure.update_layout(height=340, yaxis_title=units, margin=dict(l=0, r=0, t=12, b=0))
    if not index_points:
        money_history_axes(figure, result.currency)
    st.plotly_chart(chart_hover(figure), width='stretch', config=chart_config())
