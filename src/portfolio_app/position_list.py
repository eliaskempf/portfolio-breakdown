"""Accessible position list with stable row identities for opening the editor."""

from portfolio_app.currency_display import money_label

from hashlib import sha256

import math

import streamlit as st
from portfolio_app.display_names import instrument_name
from portfolio_app.list_ui import ListColumn, render_list


def list_context(path) -> str:
    return sha256(str(path.resolve()).encode()).hexdigest()[:16]


def position_rows(holdings, allocation=None, *, percent=False):
    buckets = {bucket.id: bucket.name for bucket in allocation.buckets} if allocation else {}
    total = holdings.current_value_reporting.sum() if 'current_value_reporting' in holdings else 0
    complete = 'current_value_reporting' in holdings and holdings.current_value_reporting.notna().all()
    def number(value):
        return float(value) if value is not None and math.isfinite(float(value)) else None
    rows = []
    for row in holdings.itertuples():
        value = number(getattr(row, 'current_value_reporting', None))
        gain = number(getattr(row, 'unrealized_gain_reporting', None))
        gain_reporting = gain
        return_pct = number(getattr(row, 'return_pct', None)) if gain is not None else None
        if percent and gain is not None:
            gain = number(getattr(row, 'return_pct', None))
        rows.append(dict(id=row.position_id, name=instrument_name(row), fullName=row.name,
            ticker=getattr(row, 'ticker', ''), isin=getattr(row, 'isin', ''),
            account=getattr(row, 'account', ''), portfolio=getattr(row, 'portfolio', ''),
            bucket=buckets.get(getattr(row, 'bucket_id', ''), 'Unassigned'), quantity=row.shares,
            value=value, allocation=100 * value / total if complete and total > 0 and value is not None else None,
            gain=gain, gainReporting=gain_reporting, returnPct=return_pct, note=getattr(row, 'performance_note', '')))
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
               ('quantity', 'Quantity'), ('value', 'Value'), ('allocation', 'Allocation (%)'),
               ('gain', 'Return (%)' if percent else 'Gain')]
    if view != 'Holdings':
        columns = [('name', 'Investment'), ('value', 'Value'), *metric_columns]
    _render_table(rows, columns, context=context, revision=snapshot.revision, on_open=open_position)
    render_position_metric_sources(detail)


def render_overview_positions(valued, config, *, context, scope, open_position=None, edit_position=None):
    rows = position_rows(valued, config)
    revision = sha256(repr(rows).encode()).hexdigest()

    def open_row(event):
        callback = edit_position if event['action'] == 'edit' else open_position
        if callback:
            callback(event['id'])

    columns = [('name', 'Investment'), ('value', 'Value'), ('allocation', f'% of {scope}'),
               ('returnPct', 'Return (%)'), ('gainReporting', 'Gain')]
    _render_table(rows, columns, context=context, revision=revision, on_open=open_row,
                  interactive=bool(open_position), editable=bool(edit_position))


def _render_table(rows, columns, *, context, revision, on_open, interactive=True, editable=True):
    for row in rows:
        row['title'] = ' · '.join(value for value in [row['fullName'], row['ticker'], row['isin'], row['account']] if value)
        row['editLabel'] = 'Edit ' + row['name'] + (' · ' + row['account'] if row['account'] else '')
    from portfolio_app.fundamentals import DEFINITIONS
    def metric_help(field):
        definition = DEFINITIONS.get(field.removeprefix('metric_'))
        return definition.description if definition else 'Saved position attribute; missing values appear as a dash.'
    numeric = {'quantity', 'value', 'allocation', 'gain', 'returnPct', 'gainReporting'}
    column_specs = [ListColumn(field, money_label(label) if field in {'value', 'gainReporting'} or (field == 'gain' and label == 'Gain') else label, numeric=field in numeric or field.startswith('metric_'), decimals=10 if field == 'quantity' else 2,
                              signed=field in {'gain', 'returnPct', 'gainReporting'},
                              display=field + '_display' if field.startswith('metric_') else '',
                              help={'name': 'Investment name; select a row for position details.',
                                    'quantity': 'Total units currently held, including fractional units.',
                                    'value': 'Current value in portfolio currency at the available unit price and exchange rate.',
                                    'allocation': 'Current position value as a percentage of ' + (label.removeprefix('% of ') if label.startswith('% of ') else 'the displayed portfolio selection') + '.',
                                    'portfolio': 'Optional portfolio or strategy grouping saved with this position.',
                                    'account': 'Account or storage location saved with this position.',
                                    'returnPct': 'Unrealized gain divided by known recorded purchase cost, in percent.',
                                    'gainReporting': 'Unrealized gain in portfolio currency on known recorded purchase cost.',
                                    'gain': 'Unrealized gain divided by known purchase cost, in percent.' if label == 'Return (%)' else 'Unrealized gain on known recorded purchase cost.',
                                    'bucket': 'Category owning this position in the saved allocation hierarchy.',
                                    'metric_beta': 'Sensitivity to the selected benchmark on common weekly reporting-currency returns.',
                                    'metric_volatility': 'Annualized variability estimated from historical weekly reporting-currency returns.'}.get(field, metric_help(field)), tooltip='note' if field in {'gain', 'gainReporting', 'returnPct'} else ('title' if field == 'name' else field + '_note' if field.startswith('metric_') else '')) for field, label in columns]
    render_list(rows, column_specs, key=f'position_list_{context}', context=context, title='Positions',
                revision=revision, on_open=on_open if interactive else None, editable=editable,
                search_label='Filter positions', search_fields=['name', 'fullName', 'ticker', 'isin', 'account', 'portfolio', 'bucket'],
                default_sort='value')
