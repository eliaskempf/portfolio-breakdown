"""Complete asset exposure and provenance tables, independent of Streamlit."""
import pandas as pd

from portfolio_app.display_names import instrument_name
from portfolio_app.label_presentation import asset_badges


def complete_exposures(exposures, selected):
    """Keep unavailable source valuations visible without fabricating weights."""
    result = exposures.copy()
    missing = []
    for row in selected.loc[selected.current_value_eur.isna()].to_dict('records'):
        identity, name = row.get('analysis_asset_id'), row.get('analysis_asset_name')
        missing.append(dict(asset_id=identity if isinstance(identity, str) and identity else row['id'],
                            asset_name=name if isinstance(name, str) and name else instrument_name(row),
                            ticker=row.get('ticker', ''), isin=row.get('isin', ''), value=float('nan'),
                            source_position_id=row['position_id'], source_instrument=row['id'],
                            source_type='instrument', direct_or_indirect='direct'))
    if missing:
        result = pd.concat([result, pd.DataFrame(missing)], ignore_index=True)
    return result


def asset_exposure_table(exposures, *, classifications=None, taxonomy='labels', complete=True):
    # Aggregate whole columns once. An absent source contributes zero; an
    # existing source with any unavailable value remains unknown.
    result = exposures.drop_duplicates('asset_id').set_index('asset_id')[['asset_name', 'ticker']].rename(
        columns={'asset_name': 'Asset', 'ticker': 'Ticker'})
    for column, kind in [('Direct (EUR)', 'direct'), ('ETF-derived (EUR)', 'indirect'), ('Total (EUR)', None)]:
        rows = exposures if kind is None else exposures.loc[exposures.direct_or_indirect.eq(kind)]
        grouped = rows.groupby('asset_id', sort=False).value
        totals = grouped.sum().mask(grouped.count() < grouped.size())
        result[column] = totals.reindex(result.index, fill_value=0.)
    result['Labels'] = [asset_badges(classifications or {}, asset, taxonomy) for asset in result.index]
    if 'source_position_id' in exposures:
        counts = exposures.groupby('asset_id').source_position_id.nunique()
        result['Sources'] = counts.reindex(result.index).map(lambda count: f'{count} position' + ('s' if count != 1 else ''))
    result = result.reset_index()
    denominator = result['Total (EUR)'].sum()
    result['Allocation %'] = 100 * result['Total (EUR)'] / denominator if complete and denominator > 0 else float('nan')
    return result.sort_values(['Total (EUR)', 'Asset'], ascending=[False, True], na_position='last', ignore_index=True)


def exposure_sources(exposures, selected, asset_id):
    rows = exposures.loc[exposures.asset_id.eq(asset_id)]
    total = rows.value.sum() if rows.value.notna().all() else float('nan')
    positions = selected.set_index('position_id')
    records = []
    for (position, kind), parts in rows.groupby(['source_position_id', 'direct_or_indirect'], sort=False):
        source = positions.loc[position]
        value = float('nan') if parts.value.isna().any() else parts.value.sum()
        records.append({'Source': instrument_name(source), 'Account': source.get('account', ''),
                        'Exposure': 'Direct' if kind == 'direct' else 'Through ETF', 'Value (EUR)': value,
                        '% of asset exposure': 100 * value / total if total > 0 else float('nan')})
    return pd.DataFrame(records, columns=['Source', 'Account', 'Exposure', 'Value (EUR)', '% of asset exposure'])
