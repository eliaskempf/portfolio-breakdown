"""Accessible position list with stable row identities for opening the editor."""

from portfolio_app.currency_display import money_label

from hashlib import sha256

import math

import streamlit as st
from portfolio_app.display_names import instrument_name
from portfolio_app.list_ui import ListColumn, render_list
from portfolio_app.futures_presentation import future_summary, future_name


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
            bucket=buckets.get(getattr(row, 'bucket_id', ''), 'Unassigned'),
            quantity=getattr(row, 'contracts', row.shares) if getattr(row, 'instrument_type', '') == 'future' else row.shares,
            value=value, allocation=100 * value / total if complete and total > 0 and value is not None else None,
            gain=gain, gainReporting=gain_reporting, returnPct=return_pct,
            exposure=number(getattr(row, 'signed_notional_reporting', None)), note=getattr(row, 'performance_note', '')))
    return rows


def render_position_list(path, snapshot, allocation=None, *, valued=None, account_valued=None, percent=False, demo=False) -> None:
    from portfolio_app.position_metrics_ui import position_metric_toolbar, render_position_metric_sources
    holdings = snapshot.holdings if valued is None else valued
    view, metric_columns, metrics, detail = position_metric_toolbar(holdings, path.parent, demo=demo, account_valued=account_valued)
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
    if holdings.attrs.get('investment_members'):
        columns.insert(4, ('exposure', 'Market exposure'))
    if view != 'Holdings':
        columns = [('name', 'Investment'), ('value', 'Value'), *metric_columns]
    _render_table(rows, columns, context=context, revision=snapshot.revision, on_open=open_position)
    render_position_metric_sources(detail)


def overview_position_rows(valued, config, *, future=None):
    rows = position_rows(valued.loc[valued.position_id.ne('future:overlay')], config)
    if future is not None and (future.contracts or future.reference_capital_eur is not None):
        funded = future.reference_capital_eur is not None
        total = valued.loc[valued.position_id.ne('future:overlay'), 'current_value_reporting'].sum() + (future.capital_value if funded else 0.)
        if funded:
            for row in rows:
                row['allocation'] = 100 * row['value'] / total if total > 0 and row['value'] is not None and valued.current_value_reporting.notna().all() else None
        rows.append(dict(id='future:overlay', name=future_name(future), details=future_summary(future),
            fullName='Saved mock equity index future', ticker=future.instrument, isin='', account='',
            portfolio='', bucket=future.bucket_id, quantity=future.contracts, value=future.capital_value if funded else None,
            allocation=100 * future.capital_value / total if funded and total > 0 and valued.current_value_reporting.notna().all() else None,
            gainReporting=future.lifetime_gain, returnPct=future.reference_return_pct, exposure=future.notional,
            note='Lifetime gain after transaction fees / fixed reference capital; funding is not gain.' if funded else 'Cumulative mock futures P&L, included once in portfolio value. No return on margin is implied.'))
    return rows


def render_overview_positions(valued, config, *, context, scope, open_position=None, edit_position=None,
                              future=None, open_future=None, exposure_amounts=None):
    rows = overview_position_rows(valued, config, future=future)
    if exposure_amounts is not None:
        total = sum(exposure_amounts.get(row['id'], float('nan')) for row in rows)
        complete = math.isfinite(total) and total > 0
        for row in rows:
            amount = exposure_amounts.get(row['id'], float('nan'))
            row['value'] = float(amount) if math.isfinite(amount) else None
            row['allocation'] = amount / total * 100 if complete and math.isfinite(amount) else None
    if future and future.proxy_position_id:
        for row in rows:
            if row['id'] == 'future:overlay':
                row['companionId'] = future.proxy_position_id
    revision = sha256(repr(rows).encode()).hexdigest()

    def open_row(event):
        if event['id'] == 'future:overlay':
            if open_future:
                open_future()
            return
        callback = edit_position if event['action'] == 'edit' else open_position
        if callback:
            callback(event['id'])

    columns = [('name', 'Investment'), ('value', 'Exposure' if exposure_amounts is not None else 'Value'), ('allocation', '% of exposure' if exposure_amounts is not None else f'% of {scope}'),
               ('returnPct', 'Return (%)'), ('gainReporting', 'Gain')]
    if exposure_amounts is None and valued.attrs.get('investment_members'):
        columns.insert(2, ('exposure', 'Market exposure'))
    _render_table(rows, columns, context=context, revision=revision, on_open=open_row,
                  interactive=bool(open_position), editable=bool(edit_position), details_column='name')


def _render_table(rows, columns, *, context, revision, on_open, interactive=True, editable=True, details_column=None):
    for row in rows:
        row['title'] = ' · '.join(value for value in [row['fullName'], row['ticker'], row['isin'], row['account']] if value)
        row['editLabel'] = 'Edit ' + row['name'] + (' · ' + row['account'] if row['account'] else '')
    from portfolio_app.fundamentals import DEFINITIONS
    def metric_help(field):
        definition = DEFINITIONS.get(field.removeprefix('metric_'))
        return definition.description if definition else 'Saved position attribute; missing values appear as a dash.'
    numeric = {'quantity', 'value', 'allocation', 'gain', 'returnPct', 'gainReporting', 'exposure'}
    column_specs = [ListColumn(field, money_label(label) if field in {'value', 'gainReporting', 'exposure'} or (field == 'gain' and label == 'Gain') else label, numeric=field in numeric or field.startswith('metric_'), decimals=10 if field == 'quantity' else 2,
                              signed=field in {'gain', 'returnPct', 'gainReporting'},
                              display=field + '_display' if field.startswith('metric_') else '',
                              help={'name': 'Investment name; select a row for position details.',
                                    'quantity': 'Total units or currently open contracts; a futures investment retains its closed-contract history.',
                                    'exposure': 'Signed futures notional in portfolio currency. Exposure is separate from capital value.',
                                    'value': 'Current value in portfolio currency at the available unit price and exchange rate.',
                                    'allocation': 'Current position value as a percentage of ' + (label.removeprefix('% of ') if label.startswith('% of ') else 'the displayed portfolio selection') + '.',
                                    'portfolio': 'Optional portfolio or strategy grouping saved with this position.',
                                    'account': 'Account or storage location saved with this position.',
                                    'returnPct': 'Recorded gain divided by purchase cost or futures reference capital; see the position note.',
                                    'gainReporting': 'Unrealized holdings gain or cumulative net futures investment P&L in portfolio currency.',
                                    'gain': 'Recorded gain divided by purchase cost or futures reference capital.' if label == 'Return (%)' else 'Unrealized holdings gain or cumulative net futures investment P&L.',
                                    'bucket': 'Category owning this position in the saved allocation hierarchy.',
                                    'metric_beta': 'Sensitivity to the selected benchmark on common weekly reporting-currency returns.',
                                    'metric_volatility': 'Annualized variability estimated from historical weekly reporting-currency returns.'}.get(field, metric_help(field)), tooltip='note' if field in {'gain', 'gainReporting', 'returnPct'} else ('title' if field == 'name' else field + '_note' if field.startswith('metric_') else '')) for field, label in columns]
    render_list(rows, column_specs, key=f'position_list_{context}', context=context, title='Positions',
                revision=revision, on_open=on_open if interactive else None, editable=editable,
                search_label='Filter positions', search_fields=['name', 'fullName', 'ticker', 'isin', 'account', 'portfolio', 'bucket'],
                default_sort='value', details_column=details_column)
