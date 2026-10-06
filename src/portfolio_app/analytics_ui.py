"""Shared analytics display helpers and controls."""
from hashlib import sha256

import pandas as pd
import streamlit as st

from portfolio_app.fundamentals import DEFINITIONS, finite
from portfolio_app.risk import DEFAULT_BENCHMARK


def context_key(data_dir, demo=False):
    return 'analytics_' + sha256(f'{data_dir.resolve()}:{demo}'.encode()).hexdigest()[:12]


def display_value(metric, key):
    value = finite(metric.value)
    if value is None:
        return '—'
    if key in {'trailing_pe', 'forward_pe', 'fund_pe'} and value <= 0:
        return 'Not meaningful'
    if metric.unit == 'fraction':
        return f'{value * 100:,.2f}%'
    if metric.unit not in {'ratio', ''}:
        for scale, suffix in ((1e12, 'T'), (1e9, 'B'), (1e6, 'M')):
            if abs(value) >= scale:
                return f'{value / scale:,.2f}{suffix} {metric.unit}'
    return f'{value:,.2f}' + (f' {metric.unit}' if metric.unit not in {'ratio', ''} else '')


def risk_settings(key):
    benchmark = st.text_input('Benchmark ticker', DEFAULT_BENCHMARK, help='Quote ticker for the comparison benchmark; this does not add a holding.', key=key + '_benchmark').strip().upper()
    years = st.selectbox('History window', [1, 3, 5], help='Historical period used to estimate returns and risk; insufficient data remains visible.', index=1,
                         format_func=lambda years: f'{years} year' + ('s' if years != 1 else ''), key=key + '_years')
    st.caption('Default: MSCI ACWI ETF (IUSQ.DE). Adjusted weekly returns in EUR; at least 52 common observations.')
    return benchmark, years


def render_sources(snapshots):
    records = []
    for snapshot in snapshots.values():
        for name, metric in snapshot.metrics.items():
            records.append({'Instrument': snapshot.ticker, 'Metric': DEFINITIONS[name].label,
                'Value': display_value(metric, name), 'Source': metric.source,
                'Verified / period': metric.as_of or 'Provider period unspecified',
                'Retrieved': snapshot.fetched_at or 'Dated metadata',
                'Status': snapshot.status, 'Note': metric.note or snapshot.note})
    if records:
        st.dataframe(pd.DataFrame(records), hide_index=True, width='stretch',
                     column_config={'Source': st.column_config.LinkColumn('Source')})
    st.caption('Missing values are unavailable, not zero. Company ratios and fund-reported ratios use different definitions. '
               'Issuer fee observations are dated; use the position’s Key metrics view to update an annual fee.')


def data_quality_caption(snapshots):
    stale = sum(snapshot.status == 'stale' for snapshot in snapshots.values())
    missing = sum(not any(metric.value is not None for metric in snapshot.metrics.values()) for snapshot in snapshots.values())
    if stale or missing:
        st.caption(' · '.join([*([f'{stale} instruments use cached fallback data'] if stale else []),
                               *([f'Metrics unavailable for {missing} instruments'] if missing else [])]))
