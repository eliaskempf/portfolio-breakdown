"""Explicit exposure projections and isolated company moves, independent of UI."""
import json
import pandas as pd

from portfolio_app.futures import require_eur_reporting


def exposure_positions(valued, result, future=None):
    require_eur_reporting(valued)
    projected = valued.copy()
    if future and 'future:overlay' not in set(projected.position_id):
        source = result.exposures.loc[result.exposures.source_position_id.eq('future:overlay')]
        if not source.empty:
            projected = pd.concat([projected, pd.DataFrame([dict(position_id='future:overlay',
                id=future.instrument, name=f'{future.instrument} future', shares=1.,
                current_value_reporting=0., reporting_currency='EUR', bucket_id=source.bucket_id.iloc[0], within_bucket_target=float('nan'))])], ignore_index=True)
    amounts = result.exposures.groupby('source_position_id').market_exposure_eur.agg(
        lambda values: values.sum() if values.notna().all() else float('nan'))
    projected['current_value_reporting'] = projected.position_id.map(amounts)
    projected.attrs = dict(valued.attrs, reporting_currency='EUR')
    return projected


def companion_nodes(nodes, future):
    """Keep companion siblings adjacent without moving either across categories."""
    if future is None or not future.proxy_position_id or nodes.empty:
        return nodes
    ids = {json.loads(row.node_id)[2]: row.node_id for row in nodes.itertuples() if row.kind == 'holding'}
    a, b = ids.get(future.proxy_position_id), ids.get('future:overlay')
    if not a or not b:
        return nodes
    indexed = nodes.set_index('node_id')
    if indexed.loc[a, 'parent_id'] != indexed.loc[b, 'parent_id']:
        return nodes
    order = [key for key in nodes.node_id if key != b]
    order.insert(order.index(a) + 1, b)
    return nodes.set_index('node_id', drop=False).loc[order].reset_index(drop=True)


def company_move(result, asset_id, change_pct, *, rows=None):
    """First-order isolated price shock, with signed notional-based sensitivity."""
    rows = result.exposures if rows is None else rows
    selected = rows.loc[rows.asset_id.eq(asset_id)]
    if selected.empty or selected.exposure_eur.isna().any():
        return None, None
    pnl = float(selected.exposure_eur.sum()) * change_pct / 100
    return pnl, pnl / result.denominator * 100 if result.denominator else None


def canonical_asset_names(rows):
    """One label per exact identity; provider spelling must not split tree nodes."""
    result = rows.copy()
    labels = result.groupby('asset_id', sort=False).asset_name.first()
    result['asset_name'] = result.asset_id.map(labels)
    return result
