"""Read-only position details and instrument market-price history."""

from portfolio_app.currency_display import reporting_currency
import pandas as pd
import streamlit as st

from portfolio_app.display_names import instrument_name
from portfolio_app.history import HistoryService, DemoHistoryProvider
from portfolio_app.market_data import history_for
from portfolio_app.presentation import performance_metric


def render_position_detail(row, data_dir, *, demo=False, allocation=None):
    st.subheader(instrument_name(row))
    st.caption(' · '.join(str(value) for value in [row['name'], row.get('ticker'), row.get('isin')] if value))
    def money(value, currency=None, signed=False):
        currency = currency or reporting_currency()
        if value is None or pd.isna(value):
            return '—'
        return f'{value:+,.2f} {currency}' if signed else f'{value:,.2f} {currency}'
    first, second, third = st.columns(3)
    first.metric('Current value', money(row.get('current_value_reporting')))
    with second:
        performance_metric('Unrealized gain', money(row.get('unrealized_gain_reporting'), signed=True),
                           row.get('unrealized_gain_reporting'), key='position_gain')
    with third:
        performance_metric('Return on cost', '—' if pd.isna(row.get('return_pct', float('nan'))) else f'{row.return_pct:+.2f}%',
                           row.get('return_pct'), key='position_return')
    names = {b.id: b.name for b in allocation.buckets} if allocation else {}
    details = {'Quantity': f"{row.shares:g} {row.get('quantity_unit') or 'units'}", 'Account': row.get('account') or '—',
               'Category': names.get(row.get('bucket_id'), row.get('portfolio') or 'Unassigned'),
               'Average buy-in': money(row.get('acquisition_price'), row.get('acquisition_currency') or 'currency unspecified'),
               'Total buy-in': money(row.shares * row.acquisition_price, row.get('acquisition_currency') or 'currency unspecified'),
               'Reporting buy-in': money(row.get('cost_basis_reporting')),
               'Reporting average buy-in': money(row.get('cost_basis_reporting') / row.shares if row.shares else None),
               'Market price': money(row.get('current_price'), row.get('quote_currency') or ''),
               'Quote date': row.get('price_observed_at') or '—'}
    with st.expander('Position details'):
        st.dataframe(pd.DataFrame(details.items(), columns=['Position', 'Details']), hide_index=True, width='stretch')
    from portfolio_app.cost_basis import active_components
    with st.expander('Original costs and conversions'):
        parts = active_components(row)
        records = []
        for part in parts:
            conversion = part.get('conversions', {}).get(reporting_currency(), {})
            records.append({'Quantity': part['shares'], 'Original cost': part['amount'] or 'Unknown',
                'Currency': part['currency'] or 'Unknown', 'Purchase date': part['date'] or 'Unknown',
                'Conversion': conversion.get('method', 'Historical lookup' if part['date'] else 'Not supplied'),
                'Saved rate': conversion.get('rate', ''), 'Rate date': conversion.get('observed_on', '')})
        st.dataframe(pd.DataFrame(records), hide_index=True)
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
    if row.get('price_source') == 'gold_spot':
        st.info('Gold spot valuation uses the latest available quote. Gold price history is not available here yet.')
        return
    from portfolio_app.market_history_ui import render_market_history
    service = HistoryService(DemoHistoryProvider(), data_dir / '.cache' / 'history') if demo else history_for(data_dir)
    render_market_history(row.ticker, data_dir, key=f'position_edit_history_{row.position_id}_',
                          demo=demo, manual=pd.notna(row.get('manual_price', float('nan'))), service=service)
