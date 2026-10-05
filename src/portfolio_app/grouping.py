"""Optional display groups that preserve source positions and allocated value."""

from dataclasses import dataclass

import pandas as pd

from portfolio_app.holdings import DataError
from portfolio_app.taxonomy import Classifications, paths_for, taxonomy_names


@dataclass(frozen=True)
class InstrumentGroup:
    asset_id: str
    name: str
    members: frozenset[str]
    classification_sources: frozenset[str]


def group_exposures(exposures: pd.DataFrame, group: InstrumentGroup) -> pd.DataFrame:
    """Fold source instruments, including all of an expanded ETF's residual.

    Matching an underlying asset alone is insufficient: exposure to the same
    company from another ETF must remain outside this group. Rows retain source
    metadata, so account/portfolio aggregation and filters remain valid.
    """
    if group.asset_id in set(exposures["asset_id"]) | set(exposures["source_instrument"]):
        raise DataError("The display group ID conflicts with an existing asset.")
    result = exposures.copy()
    mask = result["source_instrument"].isin(group.members)
    result.loc[mask, "asset_id"] = group.asset_id
    result.loc[mask, "asset_name"] = group.name
    result.loc[mask, ["ticker", "isin"]] = ""
    result.loc[mask, "source_type"] = "instrument_group"
    for field in ("shares", "acquisition_price", "current_price", "fx_to_reporting", "target_allocation", "portfolio_weight"):
        if field in result:
            result.loc[mask, field] = float("nan")
    return result


def group_classifications(classifications: Classifications, group: InstrumentGroup) -> Classifications:
    """The display group follows its ETF's classifications; never persist them."""
    if group.asset_id in classifications:
        raise DataError("The display group ID conflicts with existing classifications.")
    entry = {name: tuple(sorted({path for asset in group.classification_sources
                                for path in paths_for(classifications, asset, name)}))
             for name in taxonomy_names(classifications)}
    return {**classifications, group.asset_id: entry}


def group_members_table(valued: pd.DataFrame, group: InstrumentGroup) -> pd.DataFrame:
    """Show the original fund and stocks, including explicitly unvalued members."""
    rows = valued.loc[valued["id"].isin(group.members)]
    table = rows.groupby(["id", "name"], sort=False, as_index=False)["current_value_reporting"].sum(min_count=1)
    total = table["current_value_reporting"].sum()
    table["Within group (%)"] = 100 * table["current_value_reporting"] / total if total else float("nan")
    return table.rename(columns={"name": "Investment", "current_value_reporting": "Value"}).sort_values(
        "Value", ascending=False, kind="stable", na_position="last", ignore_index=True,
    )[["Investment", "Value", "Within group (%)"]]
