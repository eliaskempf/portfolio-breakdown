"""Read-only position details and instrument market-price history."""

from portfolio_app.currency_display import reporting_currency
import pandas as pd
import streamlit as st

from portfolio_app.display_names import instrument_name
from portfolio_app.history import HistoryService, DemoHistoryProvider
from portfolio_app.market_data import history_for
from portfolio_app.presentation import performance_metric, return_percent


def render_position_detail(row, data_dir, *, demo=False, allocation=None, on_edit=None):
    # Keep the native accessible dialog name; put visible actions alongside the
    # same asset heading, rather than in a separate, widely spaced metadata row.
    st.html('<style>\n    [role="dialog"]:has(.st-key-position_detail_header) > h2[slot="title"] {display:none;}\n    [role="dialog"] .st-key-position_detail_header {padding-right:40px;}\n    </style>')
    with st.container(horizontal=True, horizontal_alignment='distribute', vertical_alignment='center', key='position_detail_header'):
        st.subheader(instrument_name(row), width='content')
        with st.container(horizontal=True, width='content', gap='small'):
            if on_edit:
                st.button('Edit position', icon=':material/edit:', on_click=on_edit)
            if row.get('ticker'):
                from portfolio_app.monitoring_ui import request_monitoring
                st.button('Track', help='Track this asset in Monitoring', icon=':material/notifications:',
                    key='position_track_'+row.position_id, on_click=request_monitoring, kwargs={'symbol':row.ticker})
    identifiers = [str(row.get(k) or '') for k in ('ticker', 'isin')]
    if any(identifiers):
        st.caption(' · '.join(x for x in identifiers if x))
    def money(value, currency=None, signed=False):
        currency = currency or reporting_currency()
        if value is None or pd.isna(value):
            return '—'
        return f'{value:+,.2f} {currency}' if signed else f'{value:,.2f} {currency}'
    from portfolio_app.account_journal import load_journal
    journal, _ = load_journal(data_dir)
    has_history = any(b.position_id == row.position_id and b.active for b in journal.securities)
    useful_history = False
    if has_history:
        from portfolio_app.history_view import history_view
        from portfolio_app.history_observations import HistoryScope, load_observations
        observations, _ = load_observations(data_dir)
        useful_history = history_view(journal,HistoryScope((row.position_id,)),observations,target=reporting_currency()).useful
    views = ['History', 'Transactions', 'Price history', 'Key metrics', 'Details'] if has_history else ['Price history', 'Key metrics', 'Transactions', 'Details']
    view = st.segmented_control('Position detail view', views,
        default='History' if useful_history else 'Price history', required=True, key=f'position_edit_detail_view_{row.position_id}', label_visibility='collapsed') or views[0]
    if view == 'History':
        from portfolio_app.history_ui import render_history_results, _key
        from portfolio_app.history_observations import HistoryScope
        render_history_results(data_dir,journal,HistoryScope((row.position_id,)),key=_key(data_dir,row.position_id)+'result_',label='Position value')
        return
    first, second, third = st.columns(3)
    first.metric('Current value', money(row.get('current_value_reporting')))
    with second:
        performance_metric('Unrealized gain', money(row.get('unrealized_gain_reporting'), signed=True),
                           row.get('unrealized_gain_reporting'), key='position_gain')
    with third:
        performance_metric('Return on cost', return_percent(row.get('return_pct')),
                           row.get('return_pct'), key='position_return')
    if view == 'Transactions':
        from portfolio_app.history_ui import render_position_history
        render_position_history(data_dir, row)
        return
    if view == 'Key metrics':
        from portfolio_app.position_metrics_ui import render_instrument_metrics
        render_instrument_metrics(row, data_dir, demo=demo)
        return
    if view == 'Price history':
        if row.get('price_source') == 'gold_spot':
            st.caption('Gold spot price history is not available yet.')
            return
        from portfolio_app.market_history_ui import render_market_history
        synthetic_prices = demo and not (data_dir / '.market-history-demo').exists()
        service = HistoryService(DemoHistoryProvider(), data_dir / '.cache' / 'history') if synthetic_prices else history_for(data_dir)
        render_market_history(row.ticker, data_dir, key=f'position_edit_history_{row.position_id}_',
            demo=synthetic_prices, manual=pd.notna(row.get('manual_price', float('nan'))), service=service, native_currency=row.get('quote_currency'))
        return
    names = {b.id: b.name for b in allocation.buckets} if allocation else {}
    details = {'Quantity': f"{row.shares:g} {row.get('quantity_unit') or 'units'}", 'Account': row.get('account') or '—',
               'Category': names.get(row.get('bucket_id'), row.get('portfolio') or 'Unassigned'),
               'Average buy-in': money(row.get('acquisition_price'), row.get('acquisition_currency') or 'currency unspecified'),
               'Total buy-in': money(row.shares * row.acquisition_price, row.get('acquisition_currency') or 'currency unspecified'),
               'Reporting buy-in': money(row.get('cost_basis_reporting')),
               'Reporting average buy-in': money(row.get('cost_basis_reporting') / row.shares if row.shares else None),
               'Market price': money(row.get('current_price'), row.get('quote_currency') or ''),
               'Quote date': row.get('price_observed_at') or '—'}
    if has_history:
        from datetime import date
        from portfolio_app.gain_components import security_gain_components
        from portfolio_app.gain_accounting import amount
        components = security_gain_components(journal, row.position_id, observations, date.today().isoformat(),
            reporting_currency(), current_value=amount(row.get('current_value_reporting')))
        details.update({'Recorded realized gain': money(components.realized, signed=True),
                        'Recorded income (net)': money(components.income, signed=True),
                        'Other recorded expenses': money(components.expenses),
                        'Recorded purchase payments': money(components.purchase_payments),
                        'Recorded sale proceeds (net)': money(components.sale_proceeds)})
    with st.container():
        st.dataframe(pd.DataFrame(details.items(), columns=['Position', 'Details']), hide_index=True, width='stretch')
        if has_history and components.reasons:
            st.caption('Some gain components need evidence', help=' '.join(components.reasons))
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
