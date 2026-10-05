"""Source selection and optional ETF expansion, with no rendering or data access."""
from dataclasses import dataclass

import pandas as pd

from portfolio_app.etf import expand_etfs
from portfolio_app.exposures import normalize_exposures
from portfolio_app.filtering import filter_holdings
from portfolio_app.strategic import bucket_positions
from portfolio_app.valuation import portfolio_weights


@dataclass(frozen=True)
class ExposureSelection:
    positions: pd.DataFrame
    total: float
    selected_total: float
    missing: int
    all_missing: int


def select_sources(valued, classifications, *, metadata=None, asset_ids=None,
                   taxonomy_branches=None, scope='', allocation=None) -> ExposureSelection:
    """Filter source positions before expansion; weights use the selected scope."""
    selected = filter_holdings(valued, classifications, metadata=metadata,
                              asset_ids=asset_ids, taxonomy_branches=taxonomy_branches)
    if scope:
        selected = bucket_positions(selected, allocation, scope) if allocation else selected.loc[selected.portfolio.eq(scope)].copy()
    selected['portfolio_weight'] = portfolio_weights(selected.current_value_reporting)
    return ExposureSelection(selected, float(valued.current_value_reporting.sum()),
                             float(selected.current_value_reporting.sum()),
                             int(selected.current_value_reporting.isna().sum()),
                             int(valued.current_value_reporting.isna().sum()))


def prepare_exposures(selected, funds, holdings, *, lookthrough):
    """Keep source identities and residual Other rows through normalized expansion."""
    exposures = normalize_exposures(selected)
    return expand_etfs(exposures, funds, holdings) if lookthrough else exposures
