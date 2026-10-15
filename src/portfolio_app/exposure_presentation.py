"""Explicit exposure projections and isolated company moves, independent of UI."""
import json
from dataclasses import dataclass
import pandas as pd

from portfolio_app.futures import require_eur_reporting


def exposure_positions(valued, result, future=None, *, known_only=False):
    if result.reporting_currency == 'EUR':
        require_eur_reporting(valued)
    elif (future is not None or 'reporting_currency' not in valued
          or not valued.reporting_currency.eq(result.reporting_currency).all()
          or valued.attrs.get('reporting_currency', result.reporting_currency) != result.reporting_currency):
        from portfolio_app.holdings import DataError
        raise DataError('Exposure presentation requires consistent reporting currency.')
    projected = valued.copy()
    if future and 'future:overlay' not in set(projected.position_id):
        source = result.exposures.loc[result.exposures.source_position_id.eq('future:overlay')]
        if not source.empty:
            projected = pd.concat([projected, pd.DataFrame([dict(position_id='future:overlay',
                id=future.instrument, name=f'{future.instrument} future', shares=1.,
                current_value_reporting=0., reporting_currency='EUR', bucket_id=source.bucket_id.iloc[0], within_bucket_target=float('nan'))])], ignore_index=True)
    grouped = result.exposures.groupby('source_position_id').market_exposure_reporting
    amounts = grouped.sum(min_count=1) if known_only else grouped.agg(
        lambda values: values.sum() if values.notna().all() else float('nan'))
    projected['current_value_reporting'] = projected.position_id.map(amounts)
    projected.attrs = dict(valued.attrs, reporting_currency=result.reporting_currency)
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
    if selected.empty or selected.exposure_reporting.isna().any():
        return None, None
    pnl = float(selected.exposure_reporting.sum()) * change_pct / 100
    return pnl, pnl / result.denominator * 100 if result.denominator else None


def canonical_asset_names(rows):
    """One label per exact identity; provider spelling must not split tree nodes."""
    result = rows.copy()
    labels = result.groupby('asset_id', sort=False).asset_name.first()
    result['asset_name'] = result.asset_id.map(labels)
    return result


@dataclass(frozen=True)
class MarketAnalysis:
    """Owned presentation projections with two deliberately different totals.

    Chart nodes contain known contributions. Table nodes preserve unknowns.
    Composition uses selected exposure; equity percentages use portfolio equity.
    """
    rows: pd.DataFrame
    chart_nodes: pd.DataFrame
    table_nodes: pd.DataFrame
    equity_percentages: pd.Series
    missing: bool
    signed: bool


def prepare_market_analysis(result, config, classifications, valued, *, scope='',
                            field='market_exposure_reporting', view='Assets & companies',
                            taxonomy='sector', depth=2, root_label='Portfolio', future=None):
    from portfolio_app.aggregation import build_tree, classify_exposures
    field = {'exposure_eur': 'exposure_reporting', 'market_exposure_eur': 'market_exposure_reporting'}.get(field, field)
    rows = result.exposures
    if scope:
        rows = rows.loc[rows.bucket_id.eq('') if scope == 'unassigned' else rows.bucket_id.isin(config.leaves(scope))]
    rows = rows.copy()
    data = canonical_asset_names(rows)
    data['value'] = data[field]
    if view in {'Themes & sectors', 'Geography'}:
        allocations = classify_exposures(data, classifications, taxonomy)
        tree_options = dict(depth=int(depth), include_holdings=True, root_label=root_label)
    else:
        if view == 'ETF sources':
            names = dict(zip(valued.position_id, valued.name))
            # Capital rows include the canonical future label even when it is
            # not an owned holding. Closed/legacy adjustments keep their labels.
            names.update(dict(zip(result.capital.source_position_id, result.capital.name)))
            if future is not None:
                from portfolio_app.future_projections import FUTURE_SOURCE_ID, future_name
                names[FUTURE_SOURCE_ID] = future_name(future)
            data['asset_id'] = data.source_position_id
            data['asset_name'] = data.source_position_id.map(lambda key: names.get(key, key))
        data['path'] = [()] * len(data)
        allocations = data
        tree_options = dict(include_holdings=True, root_label=root_label)
    table = build_tree(allocations, **tree_options)
    chart = build_tree(allocations.assign(value=allocations.value.fillna(0.)), **tree_options)
    equity = table.value * 100 / result.denominator if result.denominator else pd.Series(float('nan'), index=table.index)
    equity.index = table.node_id
    return MarketAnalysis(rows, chart, table, equity, bool(rows[field].isna().any()), bool(rows[field].lt(0).any()))
