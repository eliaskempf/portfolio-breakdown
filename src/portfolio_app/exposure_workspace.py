"""Shared, measure-explicit exposure projections; no UI or data access."""
from hashlib import sha256
from dataclasses import fields

import pandas as pd

from portfolio_app.etf import constituent_resolver, matching_fund
from portfolio_app.exposure_presentation import canonical_asset_names


def analytical_signature(valued, funds, *policies):
    """Ignore advancing quote ages, never amounts, identities or pricing inputs."""
    diagnostics = {'price_age_hours', 'fx_age_hours', 'price_observed_at', 'fx_observed_at'}
    digest = sha256(valued.drop(columns=list(diagnostics), errors='ignore').to_json(double_precision=15).encode())
    digest.update(repr(policies).encode())
    for fund in funds:
        for field in fields(fund):
            value = getattr(fund, field.name)
            digest.update(field.name.encode())
            digest.update((value.to_json(double_precision=15) if isinstance(value, pd.DataFrame) else repr(value)).encode())
    return digest.hexdigest()


def analysis_sources(valued, result, future=None):
    """Include journal and sandbox sources without turning notional into wealth."""
    sources = valued.copy()
    additions = []
    held = set(sources.position_id)
    for row in result.capital.itertuples():
        if row.source_position_id not in held:
            additions.append(dict(position_id=row.source_position_id, id=row.source_position_id,
                name=row.name, bucket_id=row.bucket_id, current_value_reporting=row.value,
                instrument_type='liability' if row.kind == 'liability' else 'cash', shares=1.))
    if future is not None:
        # A legacy settlement adjustment and its market overlay are separate
        # sources; a funded strategy uses one source with two distinct measures.
        additions = [row for row in additions if row['position_id'] != 'future:overlay']
        overlay = result.exposures.loc[result.exposures.source_position_id.eq('future:overlay')]
        if 'future:overlay' not in held:
            additions.append(dict(position_id='future:overlay', id=future.instrument,
                name=f'{future.instrument} future', bucket_id=overlay.bucket_id.iloc[0] if not overlay.empty else '',
                current_value_reporting=future.capital_value if future.reference_capital_eur is not None else 0.,
                instrument_type='future', shares=1., proxy_position_id=future.proxy_position_id))
    if additions:
        sources = pd.concat([sources, pd.DataFrame(additions)], ignore_index=True)
    for column in ('ticker', 'isin', 'short_name', 'account', 'portfolio', 'bucket_id', 'exposure_kind'):
        if column not in sources:
            sources[column] = ''
        sources[column] = sources[column].fillna('')
    sources['reporting_currency'] = result.reporting_currency
    sources.attrs = dict(valued.attrs, reporting_currency=result.reporting_currency)
    return sources


def analysis_contributions(result, sources, funds, selected_ids, *, measure='Market exposure',
                           field='market_exposure_reporting', expanded_funds=()):
    """Select source contributions, then project their requested representation.

    The complete result retains proxy resolution even when the proxy's owned
    position is outside the selected scope. Collapsing preserves unknown totals.
    Funded futures cash/claims stay at source level, never at proxy-company level.
    """
    rows = result.exposures.loc[result.exposures.source_position_id.isin(selected_ids)].copy()
    rows['funded_value'] = rows.value
    rows['value'] = rows[field] if measure == 'Market exposure' else rows.funded_value
    rows['known_value'] = rows.value
    positions = sources.set_index('position_id')
    enabled = {fund.isin for fund in expanded_funds}
    collapse = set()
    for source in sources.loc[sources.position_id.isin(selected_ids)].to_dict('records'):
        fund = matching_fund(source, funds)
        if source.get('instrument_type') == 'future':
            proxy_id = source.get('proxy_position_id', '')
            proxy = positions.loc[proxy_id].to_dict() if proxy_id in positions.index else {}
            fund = matching_fund(proxy, funds)
            if measure != 'Market exposure' or fund is None or fund.isin not in enabled:
                collapse.add(source['position_id'])
        elif fund is not None and fund.isin not in enabled:
            collapse.add(source['position_id'])
    collapsed = []
    for source_id, group in rows.loc[rows.source_position_id.isin(collapse)].groupby('source_position_id', sort=False):
        source = positions.loc[source_id]
        row = group.iloc[0].to_dict()
        for amount in ('value', 'funded_value', 'exposure_reporting', 'market_exposure_reporting'):
            row[amount] = group[amount].sum() if group[amount].notna().all() else float('nan')
        row['known_value'] = group.value.sum(min_count=1)
        if measure != 'Market exposure' and source.instrument_type == 'future':
            row['value'] = source.current_value_reporting
            row['known_value'] = row['value']
        row.update(asset_id=source['id'], asset_name=source['name'], source_type='future' if source.instrument_type == 'future' else 'instrument')
        collapsed.append(row)
    rows = pd.concat([rows.loc[~rows.source_position_id.isin(collapse)], pd.DataFrame(collapsed)], ignore_index=True)
    if measure != 'Market exposure':
        absent = set(selected_ids) - set(rows.source_position_id)
        extra = [dict(source_position_id=row.position_id, source_instrument=row.id, asset_id=row.id,
                      asset_name=row.name, value=row.current_value_reporting, known_value=row.current_value_reporting, source_type='instrument')
                 for row in sources.loc[sources.position_id.isin(absent)].itertuples()]
        rows = pd.concat([rows, pd.DataFrame(extra)], ignore_index=True)
    # Metadata follows source identity; ticker/ISIN follow the resulting asset.
    for column in sources.columns:
        if column not in rows and column not in {'id', 'name', 'position_id', 'ticker', 'isin'}:
            rows[column] = rows.source_position_id.map(positions[column])
    metadata = {}
    resolve = constituent_resolver(sources)
    for fund in funds:
        for item in fund.constituents.to_dict('records'):
            asset, _ = resolve(item)
            metadata.setdefault(asset, (item.get('ticker', ''), item.get('isin', '')))
    for row in sources.to_dict('records'):
        linked = row.get('analysis_asset_id')
        asset = linked if isinstance(linked, str) and linked else row['id']
        metadata[asset] = (row.get('ticker', ''), row.get('isin', ''))
    rows['ticker'] = rows.asset_id.map(lambda key: metadata.get(key, ('', ''))[0])
    rows['isin'] = rows.asset_id.map(lambda key: metadata.get(key, ('', ''))[1])
    rows['direct_or_indirect'] = rows.source_type.map(
        lambda kind: 'future' if kind == 'future' else 'indirect' if kind in {'etf_constituent', 'etf_other'} else 'direct')
    rows['source_channel'] = rows.direct_or_indirect.map({'direct': 'Direct', 'indirect': 'ETF', 'future': 'Future'})
    proxy = rows.source_type.eq('future') & ~rows.source_position_id.isin(collapse) & rows.method.fillna('').str.startswith('ETF holdings proxy:')
    rows.loc[proxy, 'source_channel'] = 'Futures proxy'
    return canonical_asset_names(rows)
