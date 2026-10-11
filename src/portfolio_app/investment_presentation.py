"""Logical investment views without changing contract-level accounting inputs."""
from dataclasses import replace

import pandas as pd


def investment_exposure(result, displayed):
    """Attribute capital and exposure sources while preserving gross contributions."""
    mapping = displayed.attrs.get('investment_source_map', {})
    if not displayed.attrs.get('investment_members'):
        return result
    positions = displayed.set_index('position_id')
    original_kinds = result.capital.set_index('source_position_id').kind.to_dict()
    capital = pd.DataFrame([
        dict(source_position_id=row.position_id, name=row.name, bucket_id=row.bucket_id,
             value=row.current_value_reporting,
             kind=original_kinds.get(row.position_id, 'owned asset'))
        for row in displayed.itertuples()
    ])
    attributed_cash = {source for amounts in displayed.attrs.get('investment_cash', {}).values() for source in amounts}
    represented = set(displayed.position_id) | set(mapping) | attributed_cash
    external = result.capital.loc[~result.capital.source_position_id.isin(represented)]
    # Legacy explicit debt may be part of the result without a corresponding holding.
    capital = pd.concat([capital, external], ignore_index=True)
    exposures = result.exposures.copy()
    exposures['source_position_id'] = exposures.source_position_id.map(lambda identity: mapping.get(identity, identity))
    # Keep constituent and contract rows separate: netting here would understate gross exposure.
    for identity in displayed.attrs.get('investment_members', {}):
        if identity in positions.index:
            exposures.loc[exposures.source_position_id.eq(identity), 'bucket_id'] = positions.loc[identity, 'bucket_id']
    return replace(result, capital=capital, exposures=exposures)


def investment_risk_positions(selected, raw):
    """Expand selected investments into exact contracts and their attributed cash."""
    members = selected.attrs.get('investment_members', {})
    if not members:
        return selected
    selected_ids = set(selected.position_id)
    investments = selected_ids.intersection(members)
    rows = selected.loc[~selected.position_id.isin(investments)].to_dict('records')
    indexed = raw.set_index('position_id', drop=False)
    sources = selected.attrs.get('investment_source_map', {})
    cash = selected.attrs.get('investment_cash', {})
    for identity in investments:
        children = {source for source, owner in sources.items() if owner == identity}
        rows.extend(raw.loc[raw.position_id.isin(children)].to_dict('records'))
        for source, amounts in cash.get(identity, {}).items():
            original = indexed.loc[source].to_dict()
            original.update(position_id=f'investment-cash:{identity}:{source}',
                id=f'investment-cash:{identity}:{source}',
                name=f'{selected.loc[selected.position_id.eq(identity), "name"].iloc[0]} · {original["quote_currency"]} cash',
                instrument_type='cash', current_value_native=amounts['native'],
                current_value_reporting=amounts['reporting'], current_price=amounts['native'],
                cost_basis_reporting=float('nan'), unrealized_gain_reporting=float('nan'), return_pct=float('nan'))
            rows.append(original)
    result = pd.DataFrame(rows, columns=raw.columns if not rows else None)
    result.attrs = {key: value for key, value in raw.attrs.items() if not key.startswith('investment_')}
    return result
