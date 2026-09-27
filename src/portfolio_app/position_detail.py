"""Read-only position details and instrument market-price history."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from portfolio_app.display_names import instrument_name
from portfolio_app.history import HistoryService, DemoHistoryProvider, PERIODS
from portfolio_app.market_data import history_for
from portfolio_app.charts import style_figure
from portfolio_app.presentation import performance_metric


def render_position_detail(row, data_dir, *, demo=False, allocation=None):
    st.subheader(instrument_name(row))
    st.caption(' · '.join(str(value) for value in [row['name'], row.get('ticker'), row.get('isin')] if value))
    def money(value, currency='EUR', signed=False):
        if value is None or pd.isna(value):
            return '—'
        return f'{value:+,.2f} {currency}' if signed else f'{value:,.2f} {currency}'
    first, second, third = st.columns(3)
    first.metric('Current value', money(row.get('current_value_eur')))
    with second:
        performance_metric('Unrealized gain', money(row.get('unrealized_gain_eur'), signed=True),
                           row.get('unrealized_gain_eur'), key='position_gain')
    with third:
        performance_metric('Return on cost', '—' if pd.isna(row.get('return_pct', float('nan'))) else f'{row.return_pct:+.2f}%',
                           row.get('return_pct'), key='position_return')
    names = {b.id: b.name for b in allocation.buckets} if allocation else {}
    details = {'Quantity': f'{row.shares:g}', 'Account': row.get('account') or '—',
               'Category': names.get(row.get('bucket_id'), row.get('portfolio') or 'Unassigned'),
               'Average buy-in': money(row.get('acquisition_price'), row.get('acquisition_currency') or 'currency unspecified'),
               'Total buy-in': money(row.shares * row.acquisition_price, row.get('acquisition_currency') or 'currency unspecified'),
               'Market price': money(row.get('current_price'), row.get('quote_currency') or ''),
               'Quote date': row.get('price_observed_at') or '—'}
    with st.expander('Position details'):
        st.dataframe(pd.DataFrame(details.items(), columns=['Position', 'Details']), hide_index=True, width='stretch')
    if row.get('performance_note'):
        st.info(row.performance_note + '. Use Edit position to complete your buy-in.')
    if row.get('valuation_note'):
        st.caption(row.valuation_note)
    view = st.segmented_control('Position detail view', ['Price history', 'Key metrics'], default='Price history',
                                key=f'position_edit_detail_view_{row.position_id}', label_visibility='collapsed') or 'Price history'
    if view == 'Key metrics':
        from portfolio_app.position_metrics_ui import render_instrument_metrics
        render_instrument_metrics(row, data_dir, demo=demo)
        return
    st.markdown('**Market-price history**')
    period = st.segmented_control('Period', list(PERIODS), default='1Y', key=f'position_edit_history_{row.position_id}') or '1Y'
    service = HistoryService(DemoHistoryProvider(), data_dir / '.cache' / 'history') if demo else history_for(data_dir)
    manual = pd.notna(row.get('manual_price', float('nan')))
    result = service.get(row.ticker, period, manual=manual)
    pending = not demo and service.pending(row.ticker, period)

    @st.fragment(run_every=.5 if pending else None)
    def progress():
        if not st.session_state.get('position_edit_dialog'):
            return
        if pending and not service.pending(row.ticker, period):
            st.rerun()
        if pending:
            st.caption('Updating market-price history in the background…')
    progress()
    if result.prices:
        figure = style_figure(go.Figure(go.Scatter(x=result.dates, y=result.prices, mode='lines',
            line=dict(color='#5470c6', width=2), hovertemplate='%{x}<br>%{y:,.2f} '+result.currency+'<extra></extra>')))
        figure.update_layout(height=300, yaxis_title=result.currency, margin=dict(l=12, r=12, t=12, b=30))
        st.plotly_chart(figure, width='stretch', config={'displayModeBar': False})
        st.caption(f'Instrument closing prices in {result.currency} · Excludes dividend reinvestment · Not your personal return history')
        st.caption(f'History last retrieved: {result.fetched_at}')
    if result.note:
        (st.warning if result.status == 'stale' else st.info)(result.note)
    if not demo and not pending and result.status in {'stale', 'unavailable'} and row.ticker and not manual:
        if st.button('Retry history'):
            service.get(row.ticker, period, refresh=True)
            st.rerun()
