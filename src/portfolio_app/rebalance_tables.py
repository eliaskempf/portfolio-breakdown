"""Presentation tables for plans; identities stay separate from display labels."""
from collections import Counter

import numpy as np
import pandas as pd

from portfolio_app.display_names import instrument_name
from portfolio_app.strategic import category_labels


def position_labels(positions, config=None):
    categories = category_labels(config) if config else {}
    labels = {}
    for row in positions.to_dict('records'):
        parts = [instrument_name(row), row.get('account', ''), categories.get(row.get('bucket_id', ''), '')]
        labels[row['position_id']] = ' · '.join(str(part) for part in parts if part)
    counts = Counter(labels.values())
    return {key: f'{label} (position {i + 1})' if counts[label] > 1 else label
            for i, (key, label) in enumerate(labels.items())}


def allocation_table(positions, trades, *, new_money, config=None):
    """Include every source row, even when a category received no budget/trades.

    Percentages here always use the whole supplied scope. Per-category solver
    percentages must not be concatenated into a portfolio allocation table.
    """
    rows = positions.copy()
    delta = trades.set_index('position_id')['Trade']
    rows['Trade'] = rows.position_id.map(delta).fillna(0.)
    rows['Investment'] = [instrument_name(row) for row in rows.to_dict('records')]
    rows['Action'] = np.where(rows['Trade'] > 0, 'Buy', np.where(rows['Trade'] < 0, 'Sell', 'Hold'))
    rows['Current'] = rows.current_value_reporting
    rows['After'] = rows.current_value_reporting + rows['Trade']
    total = rows.current_value_reporting.sum() if rows.current_value_reporting.notna().all() else float('nan')
    final = total + new_money
    rows['Current %'] = rows.current_value_reporting / total * 100 if total > 0 else float('nan')
    rows['After %'] = rows['After'] / final * 100 if final > 0 else float('nan')
    rows['Target %'] = rows.target_allocation * 100
    rows['Gap (pp)'] = rows['After %'] - rows['Target %']
    columns = ['Investment']
    if config:
        rows['Category'] = rows.bucket_id.map(category_labels(config)).fillna('Unassigned')
        columns.append('Category')
    if 'account' in rows and rows.account.fillna('').ne('').any():
        rows['Account'] = rows.account
        columns.append('Account')
    columns += ['Action', 'Trade', 'Current', 'After', 'Current %', 'After %', 'Target %', 'Gap (pp)']
    return rows[columns].reset_index(drop=True)


def suggested_trades(table):
    columns = [c for c in table if c not in {'Current %', 'After %', 'Target %', 'Gap (pp)', 'Lower %', 'Upper %', 'Max allocation %'}]
    return table.loc[table['Trade'].ne(0), columns].sort_values(
        'Trade', key=lambda values: values.abs(), ascending=False, kind='stable')


def category_budgets(budgets, trades, positions, config):
    rows = budgets.copy()
    categories = positions.set_index('position_id').bucket_id
    spent = trades.assign(category=trades.position_id.map(categories)).groupby('category')['Trade'].sum()
    rows['Category'] = rows['Bucket ID'].map(category_labels(config))
    rows['Invested'] = rows['Bucket ID'].map(spent).fillna(0.)
    rows['Unallocated'] = rows['Budget'] - rows['Invested']
    return rows.rename(columns={'Budget': 'Reserved'})[
        ['Category', 'Reserved', 'Invested', 'Unallocated']]


def portfolio_impact(before, after, config, *, extra_cash=0., parent=''):
    """Compare siblings, never add overlapping parent/child rows together."""
    leaves = config.leaves(parent)
    before_scope = before.loc[before.bucket_id.isin(leaves)] if parent else before
    after_scope = after.loc[after.bucket_id.isin(leaves)] if parent else after
    current_total = before_scope.current_value_reporting.sum() if before_scope.current_value_reporting.notna().all() else float('nan')
    planned_total = after_scope.current_value_reporting.sum() if after_scope.current_value_reporting.notna().all() else float('nan')
    planned_total += extra_cash if not parent else 0.
    rows = []

    def append(name, current, planned, target):
        current_pct = current / current_total * 100 if current_total > 0 else float('nan')
        planned_pct = planned / planned_total * 100 if planned_total > 0 else float('nan')
        target_pct = target * 100 if target is not None else float('nan')
        rows.append({'Category': name, 'Current %': current_pct, 'After %': planned_pct,
                     'Target %': target_pct, 'Gap (pp)': planned_pct - target_pct})

    def value(frame, mask):
        values = frame.loc[mask, 'current_value_reporting']
        return values.sum() if values.notna().all() else float('nan')

    for category in config.children(parent):
        children = config.leaves(category.id)
        append(category.name, value(before, before.bucket_id.isin(children)),
               value(after, after.bucket_id.isin(children)), category.target)
    if not parent:
        if before.bucket_id.eq('').any() or after.bucket_id.eq('').any():
            append('Unassigned', value(before, before.bucket_id.eq('')), value(after, after.bucket_id.eq('')), None)
        if extra_cash:
            append('Unallocated contribution', 0., extra_cash, None)
    return pd.DataFrame(rows, columns=['Category', 'Current %', 'After %', 'Target %', 'Gap (pp)'])
