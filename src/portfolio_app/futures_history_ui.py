"""A chart-first historical reconstruction with explicit quarterly roll boundaries."""
from datetime import date

import plotly.graph_objects as go
import streamlit as st
from portfolio_app.money import euros
from portfolio_app.charts import chart_config

from portfolio_app.futures_history import HistoricalSettings, historical_projection
from portfolio_app.futures_history_data import ESTR_SOURCE, load_history, refresh_history, unpack_history
from portfolio_app.futures_market import PROXY_URL
from portfolio_app.futures_workspace import load_historical, save_historical
from portfolio_app.holdings import DataError


def render_historical(position, directory, context, comparison, settings_area):
    st.subheader('Historical quarters · reconstructed futures versus cash')
    st.caption('Hold the same number of contracts through 1, 2, 3… successive quarterly maturities. '
               'Quarter count changes the holding period, not simultaneous exposure.')
    try:
        saved, revision = load_historical(directory)
        history = load_history(directory)
    except DataError as exc:
        st.error(str(exc))
        return
    key = f'futures_input_{context}_history_{revision}_'
    quarters = st.number_input('Quarterly contracts in sequence', min_value=1, max_value=12, value=saved.quarters, key=key+'quarters')
    with settings_area.expander('Historical comparison settings'):
        end = st.date_input('Historical end date', value=date.fromisoformat(saved.end_date or position.quote_date or date.today().isoformat()), key=key+'end')
        st.caption('Starting cash, commissions, slippage, cash yield, financing source and margin treatment come from Shared futures assumptions.')
        st.markdown(f'[EUR MSCI World ETF proxy]({PROXY_URL}) · [Historical ECB €STR]({ESTR_SOURCE})')
        if st.button('Refresh historical comparison data'):
            try:
                with st.spinner('Loading observed equity and cash-rate history…'):
                    refresh_history(directory, position.quote_date or date.today().isoformat())
            except (OSError, ValueError) as exc:
                st.error(str(exc))
            else:
                st.rerun()
        if history:
            st.caption(f"Observed proxy: {history['ticker']} via Yahoo adjusted daily closes; snapshot through {history['as_of']}; "
                       f"retrieved {history['fetched_at']}. The shared source selections determine how the rates are used.")
        settings = HistoricalSettings(quarters, end.isoformat())
        if st.button('Save historical comparison'):
            try:
                save_historical(directory, settings, revision)
            except (OSError, ValueError) as exc:
                st.error(str(exc))
            else:
                st.success('Historical comparison settings saved.')
    if history is None:
        st.info('Load historical comparison data in Settings & data below to draw the past comparison.')
        return
    try:
        prices, rates = unpack_history(history)
        curve, legs, result = historical_projection(position, comparison, settings, prices, rates)
    except DataError as exc:
        st.error(str(exc))
        return
    st.caption(f"{curve.date.iloc[0]:%d %b %Y} → {curve.date.iloc[-1]:%d %b %Y} · {quarters} completed quarterly legs · "
               f"{abs(position.contracts)} contracts at a time · {'Short' if position.contracts < 0 else 'Long'}")
    st.info('Observed equity returns; reconstructed FMWN prices using the shared financing source. Actual historical FMWN daily settlements and IBKR margins are unavailable here.')
    st.caption('Cash benchmark: observed historical €STR.' if comparison.benchmark_source == 'ecb' else f'Cash benchmark: shared manual rate {comparison.savings_rate:.3%}.')
    financing_label = (f'Historical ECB €STR + spread ({100*comparison.financing_spread:+.2f} percentage points)'
                       if comparison.financing_source == 'ecb' else f'Manual rate {comparison.financing_rate:.3%}')
    st.caption(f'Financing source: {financing_label} · Shared commission €{comparison.roll_fee_per_side:g}/side · '
               f'Slippage {comparison.roll_slippage_points:g} points/side · Cash yield {comparison.cash_rate:.2%} · '
               f'Margin {"scales with contract value" if comparison.scale_margin else "fixed in euros"}.')
    chart = go.Figure()
    chart.add_scatter(x=curve.date, y=curve.futures, mode='lines', name='FMWN reconstruction', line=dict(color='#5470c6'))
    chart.add_scatter(x=curve.date, y=curve.cash, mode='lines', name='Historical €STR cash' if comparison.benchmark_source == 'ecb' else 'Shared cash benchmark', line=dict(color='#1b9e77'))
    chart.add_scatter(x=curve.date, y=curve['index'], mode='lines', name='Index buy-and-hold (ETF proxy)', line=dict(color='#d99127'))
    for row in legs.iloc[:-1].itertuples():
        chart.add_vline(x=row.end.timestamp()*1000, line_dash='dot', line_color='#94a3b8')
    chart.update_layout(height=440, xaxis_title='Date · dotted lines mark rolls', yaxis_title='Value including starting cash (€)',
                        legend=dict(orientation='h', y=1.12), margin=dict(l=10, r=10, t=35, b=10))
    st.plotly_chart(chart, width='stretch', config=chart_config(zoom=True))
    st.caption('Index buy-and-hold invests the same starting cash in the EUR MSCI World ETF proxy at the start of the selected period. '
               'It follows adjusted prices with reinvested distributions and stays invested throughout, without leverage or margin exits. '
               'It is always long and excludes additional trading costs and taxes; fund costs and tracking differences are embedded in the proxy.')
    a, b, c = st.columns(3)
    a.metric('Reconstructed futures end value', euros(curve.futures.iloc[-1]) if result['eligible'] else 'Unavailable')
    b.metric('Historical cash end value', euros(curve.cash.iloc[-1]))
    c.metric('Difference versus cash', euros(curve.futures.iloc[-1]-curve.cash.iloc[-1]) if result['eligible'] else '—')
    if result['exit_date'] is not None or not result['eligible']:
        st.warning(f"{result['status']}" + (f" on {result['exit_date']:%d %b %Y}; no re-entry or top-ups." if result['exit_date'] is not None else '.'))
    else:
        st.caption(result['status'])
    st.caption(f'Past margin checks use the selected current requirements: initial {euros(position.margin*comparison.margin_stress)}, '
               f'maintenance {euros(position.maintenance_margin*comparison.margin_stress)}. This is one historical path, not a breach probability.')
    with st.expander('Quarterly contract details'):
        st.dataframe(legs.rename(columns=dict(start='Model entry date', end='Model close / roll date', expiry='Model maturity',
                                              entry_points='Reconstructed entry (points)', exit_points='Reconstructed exit (points)')),
                     hide_index=True, width='stretch')
        st.caption('Prices describe the scheduled legs even when the account model exits early.')
    with st.expander('Reconstruction method'):
        st.markdown('The EUR MSCI World ETF return path is scaled to the saved FMWN settlement. A flat financing curve uses '
                    'the selected shared source: earlier observed €STR plus the shared constant spread, or the explicit manual rate. '
                    'The spread is an assumption, not verified historical funding. This approximates quarterly futures prices; it does not '
                    'recover historical forward curves, basis, spreads or actual fills. The settlement anchor uses information known today.')
        st.caption(f'Anchor: {position.reference_price:,.2f} points on {position.quote_date}; proxy anchor date {result["anchor_proxy_date"]}.')
        st.markdown('Rolls close the old maturity and open the next on the last available proxy trading day before the third Friday. '
                    'A difference between old and new contract prices is not booked as profit. Costs apply per side, and each new leg '
                    'must meet initial margin. A maintenance breach closes the position with no subsequent re-entry.')
        st.markdown('The shared cash benchmark source selects observed €STR (with prior rates on non-publication days) or the shared manual rate. '
                    'Both accrue on ACT/360. '
                    'The futures cash balance earns the separately assumed yield, including after exit. Taxes, debt interest, '
                    'intraday breaches and historical broker margin changes are omitted. This is a reconstructed scenario, not an executable backtest.')
