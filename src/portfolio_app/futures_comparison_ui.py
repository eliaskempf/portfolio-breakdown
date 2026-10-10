"""Charts consume the shared futures assumptions; no independent financial inputs."""
from dataclasses import replace

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from portfolio_app.futures_comparison import comparison_dates
from portfolio_app.futures_rolling import simulate_holding
from portfolio_app.holdings import DataError


@st.cache_data(show_spinner=False, max_entries=6)
def holding_result(position, settings):
    return simulate_holding(position, settings)


def render_comparison(position, settings, provenance, base_value):
    st.subheader('Collateral & savings')
    return_estimate = provenance['estimate']
    if settings.historical_drift and return_estimate is None:
        st.warning(provenance['error'] or 'Historical index growth needs a saved EUR proxy history. Refresh historical index returns '
                   'under Shared futures assumptions below, or explicitly select a manual assumption.')
        return
    if provenance['financing_error']:
        st.warning(provenance['financing_error'])
        return
    if provenance['benchmark_error']:
        st.warning(provenance['benchmark_error'])
        return
    try:
        start, end = comparison_dates(settings, position)
        settings = replace(settings, horizon_days=(end-start).days)
    except DataError as exc:
        st.error(str(exc))
        return
    rolling, rolling_years = settings.rolling, settings.rolling_years
    index_return, financing, volatility = settings.index_return, settings.financing_rate, settings.volatility*100
    scale_margin, historical_drift = settings.scale_margin, settings.historical_drift
    capital, minimum, maximum = settings.comparison_capital, settings.capital_min, settings.capital_max
    stress, roll_fee, slippage = settings.margin_stress, settings.roll_fee_per_side, settings.roll_slippage_points
    st.info(f'Simulation: {start.isoformat()} → {end.isoformat()} · {settings.horizon_days} calendar days. '
            f'Starting futures price {position.mark_price:,.2f} points.')
    if position.quote_date and start.isoformat() != position.quote_date:
        st.warning(f'The price reference is from {position.quote_date}, not the selected start date. This is a scenario using that older/newer price.')
    st.caption(f'Opening margin: €{position.margin*stress:,.2f} · Maintenance floor: €{position.maintenance_margin*stress:,.2f} '
               f'· {abs(position.contracts)} contracts · {"Short" if position.contracts < 0 else "Long"} · '
               f'Exposure €{abs(position.notional):,.2f}')
    if base_value is not None and maximum > base_value:
        st.info('Some comparison amounts exceed the current base portfolio value. These are hypothetical funding levels.')
    try:
        with st.spinner('Comparing collateral levels…'):
            result, path, schedule = holding_result(position, settings)
    except DataError as exc:
        st.error(str(exc))
        return
    if rolling:
        st.caption(f'{rolling_years} years · {len(schedule)} planned quarterly rolls if funded throughout. '
                   'Risk accumulates across the entire period; cash and P&L carry forward, with no top-ups or re-entry.')
    st.caption(f'Financing source: {provenance["financing_label"]}')
    st.caption(f'Model assumptions: index drift {index_return:.1%}/year · financing {financing:.2%}/year · '
               f'volatility {volatility:.1f}%/year · margin {"scales with contract value" if scale_margin else "fixed in euros"}. '
               'These are constant scenario inputs, not forecasts.')
    if historical_drift:
        st.caption(f"Growth source: historical MSCI World EUR proxy (EUNL.DE), {return_estimate['start_date']} → "
                   f"{return_estimate['end_date']} · historical CAGR {return_estimate['cagr']:.2%}. "
                   'Proxy data, not FMWN settlement history; past performance is not a forecast.')
    st.caption('This is a forward simulation from the selected start, calibrated using historical index growth; '
               'it is not the realised performance of the previous quarter. Use Historical quarters for the past reconstruction.')
    dates = pd.Timestamp(start) + pd.to_timedelta(path.day, unit='D')
    chart = go.Figure()
    chart.add_scatter(x=dates, y=path.low, mode='lines', line=dict(width=0), showlegend=False)
    chart.add_scatter(x=dates, y=path.high, mode='lines', line=dict(width=0), fill='tonexty',
                      fillcolor='rgba(84,112,198,.16)', name='Futures 5th–95th percentile')
    chart.add_scatter(x=dates, y=path['median'], mode='lines', name='Futures median', line=dict(color='#5470c6'))
    chart.add_scatter(x=dates, y=path.cash, mode='lines', name='Cash benchmark', line=dict(color='#1b9e77'))
    chart.add_scatter(x=dates, y=path.index_median, mode='lines', name='Index buy-and-hold median', line=dict(color='#d99127'))
    chart.update_layout(height=440, xaxis_title='Date', yaxis_title='Value including starting cash (€)',
                        legend=dict(orientation='h', y=1.12), margin=dict(l=10, r=10, t=35, b=10))
    st.plotly_chart(chart, width='stretch', config={'displayModeBar': True, 'displaylogo': False,
                         'modeBarButtons': [['zoom2d', 'pan2d', 'resetScale2d']], 'doubleClick': 'reset'})
    if path['median'].isna().all():
        st.warning('Starting cash does not cover initial margin plus costs. Cash and the fully funded index alternatives remain available.')
    else:
        a, b, c = st.columns(3)
        a.metric('Futures median at end', f"€{path['median'].iloc[-1]:,.2f}")
        b.metric('Cash benchmark at end', f'€{path.cash.iloc[-1]:,.2f}')
        c.metric('Simulated margin-floor breach', f'{path.breach.iloc[-1]:.1f}%')
    st.caption('Futures values include cash, settlement P&L and model exits. The band is a simulated distribution, not a forecast guarantee. '
               'Earlier mock P&L and other portfolio holdings are excluded. Use Reset axes in the chart toolbar to undo zooming, or double-click the plot. Settings are below.')
    st.caption('Index buy-and-hold invests the same starting cash fully in the index, without leverage. '
               'Its median uses the same simulated net-total-return paths as the futures; it stays invested after any futures exit. '
               'It is always long, including when the futures position is short. No futures financing, margin or execution costs apply; '
               'additional index-investment trading costs and taxes are excluded.')
    st.subheader('Margin-floor breach versus starting cash')
    st.caption(f'Chance of reaching the maintenance floor at least once from {start.isoformat()} to {end.isoformat()} '
               f'({settings.horizon_days} calendar days). Contract count stays fixed at {abs(position.contracts)} '
               f'({"short" if position.contracts < 0 else "long"}) while starting cash varies.')
    risk = go.Figure()
    risk.add_scatter(x=result['Collateral (EUR)'], y=result['Margin breach (%)'], mode='lines+markers', name='Simulated breach',
        hovertemplate='Starting cash €%{x:,.0f}<br>Margin-floor breach %{y:.1f}%<extra></extra>')
    risk.add_vline(x=capital, line_dash='dot', line_color='#d99127')
    if pd.notna(path.breach.iloc[-1]):
        risk.add_scatter(x=[capital], y=[path.breach.iloc[-1]], mode='markers', name='Selected starting cash',
                         marker=dict(size=11, color='#d99127'),
                         hovertemplate='Selected cash €%{x:,.0f}<br>Margin-floor breach %{y:.1f}%<extra></extra>')
    if rolling:
        risk.add_scatter(x=result['Collateral (EUR)'], y=result['Roll funding failure (%)'],
                         mode='lines+markers', name='Cannot fund next contract')
        risk.add_scatter(x=result['Collateral (EUR)'], y=result['Any forced exit (%)'],
                         mode='lines', name='Any forced exit', line=dict(dash='dash'))
    opening_cost = roll_fee + 100*slippage
    opening = position.margin*stress + abs(position.contracts)*opening_cost
    if minimum < opening <= max(maximum, capital):
        risk.add_vrect(x0=minimum, x1=opening, fillcolor='rgba(148,163,184,.16)', line_width=0)
        risk.add_vline(x=opening, line_dash='dash', line_color='#94a3b8')
    risk.update_layout(height=340, xaxis_title='Starting cash (€)', yaxis_title='Simulated paths (%)',
                       yaxis_range=[0, 100], legend=dict(orientation='h', y=1.12), margin=dict(l=10, r=10, t=35, b=10))
    st.plotly_chart(risk, width='stretch', config={'displayModeBar': True, 'displaylogo': False,
                         'modeBarButtons': [['zoom2d', 'pan2d', 'resetScale2d']], 'doubleClick': 'reset'})
    st.caption(f'Opening requirement including costs: €{opening:,.2f}. Cash below this cannot open the position and has no breach estimate. '
               'The dotted line marks your selected starting cash. '
               'Extra simulated points concentrate near the opening requirement, with fewer points in the higher-cash tail. '
               'Edit the plotted cash range under Shared futures assumptions below.')
    if result['Margin breach (%)'].isna().all():
        st.info('None of the cash amounts in the plotted range covers the opening requirement.')
    if rolling:
        st.subheader('Cumulative risk over the holding period')
        cumulative = go.Figure()
        for column, label in [('breach', 'Maintenance-floor breach'), ('roll_failure', 'Cannot fund next contract'),
                              ('forced_exit', 'Any forced exit')]:
            cumulative.add_scatter(x=dates, y=path[column], mode='lines', name=label)
        cumulative.update_layout(height=330, xaxis_title='Date', yaxis_title='Simulated paths (%)',
                                 yaxis=dict(autorange=True, rangemode='tozero'), legend=dict(orientation='h', y=1.15))
        st.plotly_chart(cumulative, width='stretch', config={'displayModeBar': True, 'displaylogo': False,
                         'modeBarButtons': [['zoom2d', 'pan2d', 'resetScale2d']], 'doubleClick': 'reset'})
        st.caption('For the selected starting cash: probability of a first exit by each date. '
                   'Failure to meet initial margin at a roll stops the strategy even without a maintenance breach. '
                   'These are separate, mutually exclusive first-exit causes; their sum is any forced exit. '
                   'A lower maintenance-breach estimate can reflect earlier exits at rolls.')
        with st.expander('Planned roll dates'):
            st.dataframe(schedule, hide_index=True, width='stretch')
    with st.expander('Collateral details & end values'):
        st.dataframe(result[['Collateral (EUR)', 'Opening status', 'Notional / collateral (x)', 'Initial loss buffer (EUR)',
                             'Adverse move to floor (%)', 'Initial floor price (points)', 'Margin breach (%)']],
                     hide_index=True, width='stretch')
        st.caption('The adverse move is a fall for a long, a rise for a short. It is the initial cash-only threshold before '
                   'interest, not a permanent liquidation price. An absent positive-price barrier is shown as blank. '
                   'Price barriers are also blank when rolling margins scale with contract value. '
                   'Insufficient opening collateral has no simulated trade or probability.')
        st.markdown('**Same starting cash: futures, cash and index**')
        wealth = go.Figure()
        x = result['Collateral (EUR)']
        wealth.add_scatter(x=x, y=result['Futures p05 end value (EUR)'], mode='lines', line=dict(width=0), showlegend=False)
        wealth.add_scatter(x=x, y=result['Futures p95 end value (EUR)'], mode='lines', line=dict(width=0),
                           fill='tonexty', fillcolor='rgba(84,112,198,.16)', name='Futures 5th–95th percentile')
        wealth.add_scatter(x=x, y=result['Futures median end value (EUR)'], mode='lines+markers', name='Futures median')
        wealth.add_scatter(x=x, y=result['Cash benchmark end value (EUR)'], mode='lines', name='Cash benchmark')
        wealth.add_scatter(x=x, y=result['Index median end value (EUR)'], mode='lines', name='Index buy-and-hold median', line=dict(color='#d99127'))
        wealth.update_layout(height=340, xaxis_title='Same starting cash (€)', yaxis_title='End value (€)',
                             legend=dict(orientation='h', y=1.15), margin=dict(l=10, r=10, t=35, b=10))
        st.plotly_chart(wealth, width='stretch', config={'displayModeBar': True, 'displaylogo': False,
                         'modeBarButtons': [['zoom2d', 'pan2d', 'resetScale2d']], 'doubleClick': 'reset'})
        st.dataframe(result[['Collateral (EUR)', 'Cash benchmark gain (EUR)', 'Futures median gain (EUR)',
                             'Futures p05 end value (EUR)', 'Futures p95 end value (EUR)', 'Futures beat cash (%)']],
                     hide_index=True, width='stretch')
        st.caption('Futures values include the original collateral, cash interest and P&L after model exits; they are not notional values. '
                   'After exit, remaining cash earns the chosen futures cash yield and the model never re-enters. '
                   'Losses can exceed collateral; negative balances are not clipped. All results are before tax.')
    with st.expander('Model and limitations'):
        if rolling:
            st.markdown('5,000 shared seeded index paths, observed on weekdays, with constant volatility and index drift. '
                        'Quarterly maturities use the third Friday and rolls the preceding weekday; exchange holidays are not modelled. '
                        'The first maturity uses the saved contract date. Approximate price = net-total-return index × '
                        '(1 + financing rate / 360) raised to calendar days to maturity. The starting index is inferred from the saved futures mark. '
                        'Financing is embedded in this price; the roll-price gap is never booked as P&L. '
                        'Daily settlement, positive cash interest, per-side execution costs and initial-margin funding at each roll are included. '
                        'Margin is fixed in euros or proportional to contract value, as selected. Exits are permanent. '
                        'Model uncertainty includes jumps, intraday moves, changing rates/volatility, '
                        'broker margin changes, portfolio offsets, liquidity, debt interest and taxes are omitted. '
                        'Long horizons magnify these assumptions; zero simulated breaches is not zero real-world risk.')
        else:
            st.markdown('Single-contract mode uses the same index-growth, financing, cash-settlement, execution-cost and margin '
                        'model as rolling mode, stopping by the selected contract expiry. Financing is deducted exactly once '
                        'through the futures price’s remaining carry. No roll is made. Weekday observations can miss intraday breaches; '
                        'constant growth, volatility, financing and margin ratios remain scenario assumptions.')
        st.markdown('The cash benchmark keeps the chosen rate constant and compounds it daily. Real €STR changes, '
                    'fund fees, tracking differences and deposit terms affect realised cash returns.')
