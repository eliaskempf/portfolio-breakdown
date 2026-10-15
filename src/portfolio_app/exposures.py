"""Project source-position measures independently of classification/aggregation.

Each row retains source identity and custom metadata. ``value`` is the explicit
measure supplied by the caller: currency amounts, target fractions and missing
coverage indicators use the same allocation machinery, never the same units.
The caller owns completeness and denominator policy. Unknown measures are
omitted here; source selection retains their count for presentation.
"""
import pandas as pd

from portfolio_app.holdings import DataError, metadata_dimensions

# Compatibility diagnostics remain available on whole-instrument rows. Expansion
# clears these because they do not describe constituent quantities or prices.
SOURCE_COLUMNS = (
    'position_id', 'id', 'name', 'ticker', 'isin', 'wkn', 'short_name',
    'instrument_type', 'exposure_kind', 'analysis_asset_id', 'analysis_asset_name',
    'shares', 'acquisition_price', 'current_price', 'fx_to_reporting',
    'target_allocation', 'portfolio_weight', 'reporting_currency',
)
CONTRIBUTION_COLUMNS = ('asset_id', 'asset_name', 'source_position_id',
                        'source_instrument', 'source_type', 'direct_or_indirect', 'value')


def project_contributions(positions: pd.DataFrame, measure: pd.Series) -> pd.DataFrame:
    """Return an owned projection, preserving source row order and aligned units."""
    required = {'id', 'name', 'position_id'}
    if not positions.columns.is_unique or required - set(positions.columns):
        raise DataError('Contribution sources require unique columns and instrument/position identities.')
    if not measure.index.equals(positions.index):
        raise DataError('Contribution measures must align with their source positions.')
    columns = list(dict.fromkeys([*(key for key in SOURCE_COLUMNS if key in positions),
                                  *metadata_dimensions(positions)]))
    result = positions.loc[measure.notna(), columns].copy()
    result = result.rename(columns={'id': 'asset_id', 'name': 'asset_name', 'position_id': 'source_position_id'})
    result['value'] = measure.loc[measure.notna()]
    result['source_type'] = 'instrument'
    result['source_instrument'] = result['asset_id']
    result['direct_or_indirect'] = 'direct'
    if 'analysis_asset_id' in result:
        linked = result.analysis_asset_id.fillna('').ne('')
        result.loc[linked, 'asset_id'] = result.loc[linked, 'analysis_asset_id']
        result.loc[linked, 'asset_name'] = result.loc[linked, 'analysis_asset_name']
    return result


def normalize_exposures(valued_holdings: pd.DataFrame) -> pd.DataFrame:
    """Compatibility projection of known current reporting-currency amounts."""
    return project_contributions(valued_holdings, valued_holdings['current_value_reporting'])
