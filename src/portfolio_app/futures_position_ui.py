"""Compact position overview, with an explicitly labelled cached index proxy."""
from portfolio_app.chart_hover import chart_hover
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from portfolio_app.charts import chart_config

from portfolio_app.charts import style_figure
from portfolio_app.futures import CONTRACTS
from portfolio_app.futures_presentation import future_details, future_name
from portfolio_app.futures_returns import load_return_history
from portfolio_app.holdings import DataError
from portfolio_app.money import euros


def render_index_chart(future, directory):
    st.markdown('**MSCI World · EUR performance**' if future.instrument == 'FMWN' else '**Index performance**')
    history = None
    if directory is not None and future.instrument == 'FMWN':
        try:
            history = load_return_history(directory)
        except DataError:
            st.info('Saved index proxy history is unavailable. Refresh it in Futures analysis.')
            return
    if history is None:
        st.info('No index history saved for this position. Load it in Futures analysis.')
        return
    prices, _ = history
    period = st.segmented_control('Chart period', ['1M', '3M', '1Y', 'All'], default='1Y',
                                  key='future_position_chart_period', label_visibility='collapsed') or '1Y'
    end = prices.index[-1]
    start = end - pd.DateOffset(months={'1M': 1, '3M': 3, '1Y': 12}[period]) if period != 'All' else prices.index[0]
    selected = prices.loc[prices.index >= start]
    if len(selected) < 2:
        st.info('Not enough saved observations for this period.')
        return
    normalized = selected / selected.iloc[0] * 100
    figure = style_figure(go.Figure(go.Scatter(x=normalized.index, y=normalized.to_list(), mode='lines',
        name='MSCI World ETF proxy (EUR)', line=dict(color='#5470c6', width=2),
        hovertemplate='ETF proxy: %{y:,.2f} · rebased to 100<extra></extra>')))
    if future.entry_date and selected.index[0] <= pd.Timestamp(future.entry_date) <= selected.index[-1]:
        figure.add_shape(type='line', x0=future.entry_date, x1=future.entry_date, y0=0, y1=1, yref='paper',
                         line=dict(color='#94a3b8', dash='dot'))
        figure.add_annotation(x=future.entry_date, y=1, yref='paper', text='Entry', showarrow=False, yshift=12)
    figure.update_layout(height=280, margin=dict(l=8, r=8, t=24, b=12), showlegend=False,
                         yaxis_title='Rebased to 100', xaxis_title=None)
    st.plotly_chart(chart_hover(figure), key='future_position_index_chart', width='stretch', config=chart_config())
    st.caption(f'MSCI World ETF proxy · EUNL.DE · adjusted EUR prices through {end:%d %b %Y}')


def render_future_position(future, directory=None):
    @st.dialog(future_name(future), width='large')
    def details():
        direction = 'Long' if future.contracts > 0 else 'Short' if future.contracts < 0 else 'Closed'
        st.caption(f'{CONTRACTS[future.instrument][0]} · Eurex · {direction} · Simulated position')
        value, gain, performance = st.columns(3)
        value.metric('Position value', euros(future.capital_value) if future.reference_capital_eur is not None else '—',
                     help='Initial collateral + net additional funding + lifetime gain after fees. The current collateral balance already includes gains; they are not added twice.')
        gain.metric('Lifetime gain', euros(future.lifetime_gain), help='Current and previous contracts’ P&L, less transaction fees. Funding is excluded.')
        performance.metric('Return', f'{future.reference_return_pct:+.2f}%' if future.reference_return_pct is not None else '—',
                           help='Lifetime gain divided by fixed reference collateral. Not a time-weighted return; cash earns no interest.')
        render_index_chart(future, directory)
        from portfolio_app.futures_mtm_ui import render_daily_mtm
        render_daily_mtm(future, directory)
        with st.expander('Contract & collateral'):
            rows = future_details(future)
            left, right = st.columns(2)
            collateral_labels = {'Reference collateral', 'Current collateral balance', 'Collateral assumption', 'Initial margin', 'Maintenance margin'}
            for label, container, items in [
                ('Contract', left, [row for row in rows if row['label'] not in collateral_labels]),
                ('Collateral & gains', right, [row for row in rows if row['label'] in collateral_labels] + [
                    {'label': 'Net additional funding', 'value': euros(future.net_funding_eur)},
                    {'label': 'Previous contracts P&L', 'value': euros(future.closed_pnl_eur)},
                    {'label': 'Lifetime transaction fees', 'value': euros(future.fees_eur)},
                ]),
            ]:
                with container:
                    st.markdown(f'**{label}**')
                    st.table(pd.DataFrame(items), hide_index=True, hide_header=True, border='horizontal')
        with st.expander('Price sources'):
            from portfolio_app.futures_market_ui import render_price_provenance
            render_price_provenance(future, future.entry_price, future.mark_price)
        if st.button('Open Futures analysis', on_click=lambda: st.session_state.update(main_tabs='Futures')):
            st.rerun(scope='app')
    details()
