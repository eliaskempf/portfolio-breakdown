"""Accessible position list with stable row identities for opening the editor."""

from hashlib import sha256

import math

import streamlit as st
from portfolio_app.display_names import instrument_name
from portfolio_app.list_ui import ListColumn, render_list


def list_context(path) -> str:
    return sha256(str(path.resolve()).encode()).hexdigest()[:16]


def position_rows(holdings, allocation=None, *, percent=False):
    buckets = {bucket.id: bucket.name for bucket in allocation.buckets} if allocation else {}
    total = holdings.current_value_eur.sum() if 'current_value_eur' in holdings else 0
    complete = 'current_value_eur' in holdings and holdings.current_value_eur.notna().all()
    def number(value):
        return float(value) if value is not None and math.isfinite(float(value)) else None
    rows = []
    for row in holdings.itertuples():
        value = number(getattr(row, 'current_value_eur', None))
        gain = number(getattr(row, 'unrealized_gain_eur', None))
        gain_eur = gain
        return_pct = number(getattr(row, 'return_pct', None)) if gain is not None else None
        if percent and gain is not None:
            gain = number(getattr(row, 'return_pct', None))
        rows.append(dict(id=row.position_id, name=instrument_name(row), fullName=row.name,
            ticker=getattr(row, 'ticker', ''), isin=getattr(row, 'isin', ''),
            account=getattr(row, 'account', ''), portfolio=getattr(row, 'portfolio', ''),
            bucket=buckets.get(getattr(row, 'bucket_id', ''), 'Unassigned'), quantity=row.shares,
            value=value, allocation=100 * value / total if complete and total > 0 and value is not None else None,
            gain=gain, gainEur=gain_eur, returnPct=return_pct, note=getattr(row, 'performance_note', '')))
    return rows


def render_position_list(path, snapshot, allocation=None, *, valued=None, percent=False, demo=False) -> None:
    from portfolio_app.position_metrics_ui import position_metric_toolbar, render_position_metric_sources
    holdings = snapshot.holdings if valued is None else valued
    view, metric_columns, metrics, detail = position_metric_toolbar(holdings, path.parent, demo=demo)
    rows = position_rows(holdings, allocation, percent=percent)
    instruments = holdings.set_index('position_id').id.to_dict()
    for row in rows:
        row.update(metrics.get(instruments[row['id']], {}))
    context = list_context(path)
    def open_position(event):
        st.session_state['position_edit_open_request'] = event

    columns = [('name', 'Investment'), ('bucket', 'Category') if allocation else ('portfolio', 'Portfolio / sleeve'),
               ('quantity', 'Quantity'), ('value', 'Value (EUR)'), ('allocation', 'Allocation (%)'),
               ('gain', 'Return (%)' if percent else 'Gain (EUR)')]
    if view != 'Holdings':
        columns = [('name', 'Investment'), ('value', 'Value (EUR)'), *metric_columns]
    _render_table(rows, columns, context=context, revision=snapshot.revision, on_open=open_position)
    render_position_metric_sources(detail)


def render_overview_positions(valued, config, *, context, scope, open_position=None, edit_position=None):
    rows = position_rows(valued, config)
    revision = sha256(repr(rows).encode()).hexdigest()

    def open_row(event):
        callback = edit_position if event['action'] == 'edit' else open_position
        if callback:
            callback(event['id'])

    columns = [('name', 'Investment'), ('value', 'Value (EUR)'), ('allocation', f'% of {scope}'),
               ('returnPct', 'Return (%)'), ('gainEur', 'Gain (EUR)')]
    _render_table(rows, columns, context=context, revision=revision, on_open=open_row,
                  interactive=bool(open_position), editable=bool(edit_position))


def _render_table(rows, columns, *, context, revision, on_open, interactive=True, editable=True):
    for row in rows:
        row['title'] = ' · '.join(value for value in [row['fullName'], row['ticker'], row['isin'], row['account']] if value)
        row['editLabel'] = 'Edit ' + row['name'] + (' · ' + row['account'] if row['account'] else '')
    numeric = {'quantity', 'value', 'allocation', 'gain', 'returnPct', 'gainEur'}
    column_specs = [ListColumn(field, label, numeric=field in numeric or field.startswith('metric_'), decimals=10 if field == 'quantity' else 2,
                              signed=field in {'gain', 'returnPct', 'gainEur'},
                              tooltip='title' if field == 'name' else field + '_note' if field.startswith('metric_') else '',
                              display=field + '_display' if field.startswith('metric_') else '',
                              help='Unrealized gain on recorded EUR cost') for field, label in columns]
    render_list(rows, column_specs, key=f'position_list_{context}', context=context, title='Positions',
                revision=revision, on_open=on_open if interactive else None, editable=editable,
                search_label='Filter positions', search_fields=['name', 'fullName', 'ticker', 'isin', 'account', 'portfolio', 'bucket'],
                default_sort='value')
