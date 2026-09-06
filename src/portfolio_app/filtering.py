"""Position selection: AND across dimensions, OR within each dimension."""

from collections.abc import Mapping, Sequence

import pandas as pd

from portfolio_app.taxonomy import Classifications, TaxonomyPath, is_descendant, paths_for


def filter_holdings(
    holdings: pd.DataFrame,
    classifications: Classifications,
    *,
    metadata: Mapping[str, Sequence[str]] | None = None,
    asset_ids: Sequence[str] | None = None,
    taxonomy_branches: Mapping[str, Sequence[TaxonomyPath]] | None = None,
) -> pd.DataFrame:
    mask = pd.Series(True, index=holdings.index)
    for dimension, selected in (metadata or {}).items():
        mask &= holdings[dimension].isin(selected)
    if asset_ids is not None:
        mask &= holdings["id"].isin(asset_ids)
    for taxonomy, roots in (taxonomy_branches or {}).items():
        mask &= holdings["id"].map(lambda asset_id: any(
            is_descendant(path, root)
            for path in paths_for(classifications, asset_id, taxonomy)
            for root in roots
        ))
    return holdings.loc[mask].copy()
