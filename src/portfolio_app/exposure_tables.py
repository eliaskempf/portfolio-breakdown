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
    records = []
    def total(rows):
        return float('nan') if rows.value.isna().any() else rows.value.sum()
    for asset, rows in exposures.groupby('asset_id', sort=False):
        records.append({'asset_id': asset, 'Asset': rows.iloc[0].asset_name, 'Ticker': rows.iloc[0].ticker,
                        'Direct (EUR)': total(rows.loc[rows.direct_or_indirect.eq('direct')]),
                        'ETF-derived (EUR)': total(rows.loc[rows.direct_or_indirect.eq('indirect')]),
                        'Total (EUR)': total(rows),
                        'Labels': asset_badges(classifications or {}, asset, taxonomy)})
    result = pd.DataFrame(records, columns=['asset_id', 'Asset', 'Ticker', 'Direct (EUR)', 'ETF-derived (EUR)', 'Total (EUR)', 'Labels'])
    denominator = result['Total (EUR)'].sum()
    result['Allocation %'] = 100 * result['Total (EUR)'] / denominator if complete and denominator > 0 else float('nan')
    return result.sort_values(['Total (EUR)', 'Asset'], ascending=[False, True], na_position='last', ignore_index=True)


def exposure_sources(exposures, selected, asset_id):
    rows = exposures.loc[exposures.asset_id.eq(asset_id)]
    positions = selected.set_index('position_id')
    records = []
    for (position, kind), parts in rows.groupby(['source_position_id', 'direct_or_indirect'], sort=False):
        source = positions.loc[position]
        value = float('nan') if parts.value.isna().any() else parts.value.sum()
        records.append({'Source': instrument_name(source), 'Account': source.get('account', ''),
                        'Exposure': 'Direct' if kind == 'direct' else 'Through ETF', 'Value (EUR)': value})
    return pd.DataFrame(records, columns=['Source', 'Account', 'Exposure', 'Value (EUR)'])
