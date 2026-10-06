"""Position-list views and instrument metrics inside the existing detail dialog."""
from datetime import date

import pandas as pd
import streamlit as st

from portfolio_app.analytics import metric_value
from portfolio_app.analytics_service import load_metrics, load_risk
from portfolio_app.analytics_ui import (context_key, data_quality_caption, display_value,
    render_sources, risk_settings)
from portfolio_app.fundamentals import DEFINITIONS, FundFee, Metric, fee_key, finite, load_fee_overrides, save_fee_override
from portfolio_app.holdings import DataError

PRESETS = {
    'Valuation': ['trailing_pe', 'forward_pe', 'fund_pe'],
    'Income & fees': ['distribution_yield', 'fee'],
    'Risk': ['beta', 'volatility'],
}
LABELS = {name: definition.label + (' (%)' if definition.unit == 'fraction' else '')
          for name, definition in DEFINITIONS.items()} | {
          'trailing_pe': 'Trailing P/E', 'forward_pe': 'Forward P/E', 'fund_pe': 'Fund P/E',
          'fee': 'Annual fee (%)', 'distribution_yield': 'Cash yield (%)',
          'beta': 'Beta', 'volatility': 'Volatility (%)'}


def position_metric_toolbar(holdings, data_dir, *, demo=False):
    key = context_key(data_dir, demo) + '_positions'
    toolbar, settings = st.columns([5, 1], vertical_alignment='center')
    with toolbar:
        view = st.segmented_control('Position view', ['Holdings', *PRESETS], help='Switch the position list between holdings, performance, fundamentals and risk views.', default='Holdings',
                                    key=key + '_view', label_visibility='collapsed') or 'Holdings'
    if view == 'Holdings':
        return view, [], {}, None
    with settings, st.popover('Options', help='Choose visible columns and market-data options for this position view.', icon=':material/tune:', width='stretch'):
        choices = list(PRESETS[view])
        choices += (['price_book', 'price_sales', 'market_cap', 'fund_pb', 'revenue_growth',
                     'earnings_growth', 'profit_margin', 'return_equity'] if view == 'Valuation' else
                    ['payout_ratio', 'fund_assets'] if view == 'Income & fees' else [])
        chosen = st.multiselect('Visible metrics', choices, help='Choose which metric columns appear in the position list.', default=PRESETS[view],
                                format_func=LABELS.get, key=key + '_columns_' + view)
        benchmark, years = risk_settings(key) if view == 'Risk' else ('', 3)
        refresh = st.button('Refresh metrics', help='Request fresh fundamentals for the displayed instruments.', icon=':material/refresh:', disabled=demo, key=key + '_refresh_' + view)
    snapshots, risk = {}, None
    if view == 'Risk':
        if 'current_value_eur' not in holdings:
            st.info('Current valuations are needed to calculate risk.')
        elif benchmark:
            with st.spinner('Calculating risk…'):
                risk, status = load_risk(holdings, data_dir, demo, benchmark, years, refresh)
            st.caption(f'{benchmark} · {years}-year window · EUR weekly returns · {risk.observations} common observations')
            if risk.excluded:
                st.caption(f'{len(risk.excluded)} instruments lack comparable history or valuation. Open data details for exclusions.')
            if status.Status.eq('stale').any():
                st.warning('Risk estimates include cached fallback histories.')
            if risk.status == 'unavailable':
                st.info(risk.note)
    else:
        try:
            with st.spinner('Loading instrument metrics…'):
                snapshots = load_metrics(holdings, data_dir, demo=demo, refresh=refresh)
            data_quality_caption(snapshots)
        except DataError as exc:
            st.error(str(exc))
    values = {}
    for identity in holdings.id.unique():
        entry = {}
        for name in chosen:
            field = 'metric_' + name
            if name in {'beta', 'volatility'}:
                value = finite(risk.holdings.loc[identity, 'Beta' if name == 'beta' else 'Annual volatility']) if risk and identity in risk.holdings.index else None
                if name == 'volatility' and value is not None:
                    value *= 100
                entry[field + '_note'] = f'{benchmark} · {years} years · EUR weekly returns'
            else:
                snapshot = snapshots.get(identity)
                metric = snapshot.metrics.get(name, Metric()) if snapshot else Metric()
                value = metric_value(snapshot, name)
                if name in {'trailing_pe', 'forward_pe', 'fund_pe'} and value is not None and value <= 0:
                    value = None
                    entry[field + '_display'] = 'N/M'
                if DEFINITIONS[name].unit == 'fraction' and value is not None:
                    value *= 100
                if DEFINITIONS[name].unit == 'currency' and value is not None:
                    entry[field + '_display'] = display_value(metric, name)
                entry[field + '_note'] = f'{DEFINITIONS[name].description} {metric.note} {metric.source}'
            entry[field] = value
        values[identity] = entry
    detail = {'snapshots': snapshots} if view != 'Risk' else {'risk': risk, 'status': status} if risk else None
    return view, [('metric_' + name, LABELS[name]) for name in chosen], values, detail


def render_position_metric_sources(detail):
    if not detail:
        return
    with st.expander('Metric sources & coverage'):
        if 'snapshots' in detail:
            render_sources(detail['snapshots'])
        else:
            risk = detail['risk']
            st.caption(risk.note)
            if risk.excluded:
                st.dataframe(pd.DataFrame(risk.excluded.items(), columns=['Instrument', 'Reason']), hide_index=True)
            st.dataframe(detail['status'], hide_index=True, width='stretch')


def render_instrument_metrics(row, data_dir, *, demo=False):
    key = context_key(data_dir, demo) + '_detail_' + row['id']
    header, refresh_column = st.columns([4, 1], vertical_alignment='center')
    header.markdown('**Key metrics**')
    refresh = refresh_column.button('Refresh', help='Request updated market data for this view.', icon=':material/refresh:', type='tertiary',
                                    disabled=demo, key=key + '_refresh')
    try:
        with st.spinner('Loading metrics…'):
            snapshots = load_metrics(pd.DataFrame([dict(row)]), data_dir, demo=demo, refresh=refresh)
    except DataError as exc:
        st.error(str(exc))
        return
    snapshot = snapshots[row['id']]
    if snapshot.kind not in {'equity', 'etf'}:
        st.info('Fundamental metrics are not available for this instrument type.')
        return
    primary = ['trailing_pe', 'forward_pe', 'distribution_yield'] if snapshot.kind == 'equity' else ['fee', 'fund_pe', 'distribution_yield']
    for column, name in zip(st.columns(3), primary):
        column.metric(DEFINITIONS[name].label, display_value(snapshot.metrics.get(name, Metric()), name),
                      help=DEFINITIONS[name].description)
    data_quality_caption(snapshots)
    if snapshot.kind == 'equity':
        groups = [('Valuation', ['market_cap', 'price_book', 'price_sales']),
                  ('Profitability & growth', ['profit_margin', 'return_equity', 'payout_ratio', 'revenue_growth', 'earnings_growth'])]
    else:
        groups = [('Fund details', ['fund_assets', 'fund_pb'])]
    for column, (title, keys) in zip(st.columns(len(groups)), groups):
        with column:
            st.markdown(f'**{title}**')
            frame = pd.DataFrame([{'Metric': DEFINITIONS[name].label,
                                   'Value': display_value(snapshot.metrics.get(name, Metric()), name)} for name in keys])
            st.dataframe(frame, hide_index=True, width='stretch', height='content')
    with st.expander('Definitions & sources'):
        st.dataframe(pd.DataFrame([{'Metric': DEFINITIONS[name].label, 'Definition': DEFINITIONS[name].description}
                                  for name in snapshot.metrics]), hide_index=True, width='stretch')
        render_sources(snapshots)
    if snapshot.kind == 'etf' and (row.get('isin') or row.get('ticker')):
        render_fee_editor(row, data_dir, key)


def render_fee_editor(row, data_dir, key):
    with st.expander('Maintain fund fee'):
        st.caption('A private override for this share class, with its verification date and source.')
        path = data_dir / 'fund-fees.json'
        current = load_fee_overrides(path).get(fee_key(row))
        with st.form(key + '_fee'):
            rate = st.number_input('Annual fund fee (%)', help='Annual ongoing fund charge in percent, used as an indicative fee estimate.', min_value=0., max_value=100.,
                                    value=current.rate * 100 if current else None, step=.01, format='%.4f')
            source = st.text_input('Fee source', help='Reference supporting the fund fee entered here.', value=current.source if current else '')
            verified = st.date_input('Fee verification date', value=date.fromisoformat(current.verified_on) if current else date.today(), max_value=date.today())
            accumulating = st.checkbox('Verified accumulating share class', help='Confirm the share class reinvests distributions; this affects interpretation of its price history.', value=current.accumulating if current else False)
            save = st.form_submit_button('Save private fee', type='primary')
            remove = st.form_submit_button('Remove private fee', disabled=current is None)
        if save or remove:
            try:
                if save and rate is None:
                    raise DataError('Enter the annual fee.')
                fee = FundFee(rate / 100, source, verified.isoformat(), 'User-verified annual fee', accumulating) if save else None
                save_fee_override(path, fee_key(row), fee)
                st.rerun()
            except (DataError, OSError, ValueError) as exc:
                st.error(str(exc))
