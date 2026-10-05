"""Normalize position values independently of taxonomy and aggregation."""

import pandas as pd


def normalize_exposures(valued_holdings: pd.DataFrame) -> pd.DataFrame:
    result = valued_holdings.loc[valued_holdings["current_value_reporting"].notna()].copy()
    result = result.drop(columns=["purchase_history", "cost_basis_details"], errors="ignore")
    result = result.rename(columns={
        "id": "asset_id", "name": "asset_name", "current_value_reporting": "value",
        "position_id": "source_position_id",
    })
    result["source_type"] = "instrument"
    result["source_instrument"] = result["asset_id"]
    result["direct_or_indirect"] = "direct"
    if 'analysis_asset_id' in result:
        linked = result.analysis_asset_id.fillna('').ne('')
        result.loc[linked, 'asset_id'] = result.loc[linked, 'analysis_asset_id']
        result.loc[linked, 'asset_name'] = result.loc[linked, 'analysis_asset_name']
    return result
