"""Normalize position values independently of taxonomy and aggregation."""

import pandas as pd


def normalize_exposures(valued_holdings: pd.DataFrame) -> pd.DataFrame:
    result = valued_holdings.loc[valued_holdings["current_value_eur"].notna()].copy()
    result = result.drop(columns=["purchase_history"], errors="ignore")
    result = result.rename(columns={
        "id": "asset_id", "name": "asset_name", "current_value_eur": "value",
        "position_id": "source_position_id",
    })
    result["source_type"] = "instrument"
    result["source_instrument"] = result["asset_id"]
    result["direct_or_indirect"] = "direct"
    return result
