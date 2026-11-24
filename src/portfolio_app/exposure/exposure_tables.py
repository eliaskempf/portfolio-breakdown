"""Complete asset exposure and provenance tables, independent of Streamlit."""
import pandas as pd

from portfolio_app.interface.shared.display_names import instrument_name
from portfolio_app.exposure.label_presentation import asset_badges


def asset_search_mask(names: pd.Series, tickers: pd.Series, query: str) -> pd.Series:
    """Match a literal substring across the displayed asset name and ticker."""
    terms = names.fillna('') + ' ' + tickers.fillna('')
    return terms.str.casefold().str.contains(query.strip().casefold(), regex=False)


def complete_exposures(exposures, selected):
    """Keep unavailable source valuations visible without fabricating weights."""
    result = exposures.copy()
    missing = []
    for row in selected.loc[selected.current_value_reporting.isna()].to_dict('records'):
        identity, name = row.get('analysis_asset_id'), row.get('analysis_asset_name')
        missing.append(dict(asset_id=identity if isinstance(identity, str) and identity else row['id'],
                            asset_name=name if isinstance(name, str) and name else instrument_name(row),
                            ticker=row.get('ticker', ''), isin=row.get('isin', ''), value=float('nan'),
                            source_position_id=row['position_id'], source_instrument=row['id'],
                            source_type='instrument', direct_or_indirect='direct'))
    if missing:
        result = pd.concat([result, pd.DataFrame(missing)], ignore_index=True)
    return result


def asset_exposure_table(exposures, *, classifications=None, taxonomy='labels', complete=True, equity=None):
    # Aggregate whole columns once. An absent source contributes zero; an
    # existing source with any unavailable value remains unknown.
    result = exposures.drop_duplicates('asset_id').set_index('asset_id')[['asset_name', 'ticker']].rename(
        columns={'asset_name': 'Asset', 'ticker': 'Ticker'})
    for column, kind in [('Direct', 'direct'), ('ETF-derived', 'indirect'), ('Futures-derived', 'future'), ('Total', None)]:
        rows = exposures if kind is None else exposures.loc[exposures.direct_or_indirect.eq(kind)]
        grouped = rows.groupby('asset_id', sort=False).value
        totals = grouped.sum().mask(grouped.count() < grouped.size())
        result[column] = totals.reindex(result.index, fill_value=0.)
    result['Labels'] = [asset_badges(classifications or {}, asset, taxonomy) for asset in result.index]
    if 'source_position_id' in exposures:
        counts = exposures.groupby('asset_id').source_position_id.nunique()
        result['Sources'] = counts.reindex(result.index).map(lambda count: f'{count} position' + ('s' if count != 1 else ''))
    result = result.reset_index()
    denominator = result['Total'].sum()
    result['Allocation %'] = 100 * result['Total'] / denominator if complete and denominator > 0 else float('nan')
    if exposures.value.isna().any() and 'known_value' in exposures:
        known = exposures.groupby('asset_id').known_value.sum(min_count=1)
        result['Known exposure'] = result.asset_id.map(known)
    if equity is not None:
        result['Equity %'] = 100 * result['Total'] / equity if complete and equity > 0 else float('nan')
    return result.sort_values(['Total', 'Asset'], ascending=[False, True], na_position='last', ignore_index=True)


def asset_source_values(exposures):
    """One amount per asset, position and exposure kind, in first appearance order."""
    keys = ['asset_id', 'source_position_id', 'direct_or_indirect']
    columns = [*keys, 'value']
    if 'source_channel' in exposures:
        columns.append('source_channel')
    rows = exposures[columns].dropna(subset=keys)
    repeated = rows.duplicated(keys)
    values = rows.loc[~repeated].rename(columns={'value': 'Value', 'source_channel': 'Exposure'}).reset_index(drop=True)
    if repeated.any():
        # Different legs can reach the same asset from the same position. Preserve
        # pandas' compensated sum and keep any partially valued group unknown.
        grouped = rows.groupby(keys, sort=False, observed=True).value
        amounts = grouped.sum().mask(grouped.count() < grouped.size())
        values['Value'] = amounts.reindex(pd.MultiIndex.from_frame(values[keys])).array
    totals = values.groupby('asset_id', sort=False)['Value']
    total = totals.transform('sum').mask(totals.transform('count') < totals.transform('size'))
    values['% of asset exposure'] = 100 * values['Value'] / total.where(total > 0)
    if 'Exposure' not in values:
        values['Exposure'] = values.direct_or_indirect.map({'direct': 'Direct', 'indirect': 'ETF', 'future': 'Futures proxy'})
    return values


def asset_source_amounts(exposures, selected):
    """Add position metadata to the source amounts shown in financial details."""
    values = asset_source_values(exposures)
    channels = values.pop('Exposure')
    positions = selected.set_index('position_id')
    source = values.source_position_id
    names = {row.position_id: instrument_name(row) for row in selected.itertuples(index=False)}
    values['Source'] = pd.Series(names, dtype=str).reindex(source).array
    values['Account'] = positions['account'].reindex(source).array if 'account' in positions else ''
    values['Exposure'] = channels
    return values


def source_contributions(exposures, selected, *, measure=None, portfolio_exposures=None):
    values = asset_source_amounts(exposures, selected)
    source = values.source_position_id
    if measure == 'Market exposure':
        # Weight within a source includes its other assets, including any unresolved legs.
        held = (exposures if portfolio_exposures is None else
                portfolio_exposures.loc[portfolio_exposures.source_position_id.isin(source)])
        totals = held.groupby('source_position_id').value
        values['Position value'] = source.map(totals.sum().mask(totals.count() < totals.size()))
    else:
        positions = selected.set_index('position_id')
        values['Position value'] = source.map(positions['current_value_reporting']) if 'current_value_reporting' in positions else float('nan')
    values['Asset weight (%)'] = 100 * values['Value'] / values['Position value'].where(values['Position value'].ne(0))
    return values


def exposure_sources(exposures, selected, asset_id, *, measure=None):
    rows = exposures.loc[exposures.asset_id.eq(asset_id)]
    positions = selected.loc[selected.position_id.isin(rows.source_position_id)]
    values = source_contributions(rows, positions, measure=measure, portfolio_exposures=exposures)
    return values[['Source', 'Account', 'Exposure', 'Position value',
                   'Asset weight (%)', 'Value', '% of asset exposure']]
