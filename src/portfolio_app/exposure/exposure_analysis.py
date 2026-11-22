"""Source selection and optional ETF expansion, with no rendering or data access."""
from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

from portfolio_app.funds.etf import expand_etfs
from portfolio_app.exposure.exposures import normalize_exposures, project_contributions
from portfolio_app.portfolio.grouping import group_exposures
from portfolio_app.portfolio.holdings import DataError
from portfolio_app.portfolio.filtering import filter_holdings
from portfolio_app.portfolio.strategic import bucket_positions
from portfolio_app.portfolio.valuation import portfolio_weights


@dataclass(frozen=True)
class ExposureSelection:
    positions: pd.DataFrame
    known_total: float
    selected_known_total: float
    missing: int
    all_missing: int

    @property
    def total(self):
        """Compatibility alias: this is a known subtotal when all_missing > 0."""
        return self.known_total

    @property
    def selected_total(self):
        return self.selected_known_total


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


def project_measures(positions: pd.DataFrame, measures: Sequence[pd.Series], funds, holdings, *,
                     lookthrough=False, group=None, keep_group_whole=False):
    """Apply the same source-to-asset weights to related amounts and coverage flags."""
    # Only measures with the same source rows share a projection. This
    # retains each measure's coverage, validation and inferred metadata types.
    batches = {}
    for index, measure in enumerate(measures):
        if not measure.index.equals(positions.index):
            raise DataError('Contribution measures must align with their source positions.')
        mask = measure.notna().to_numpy(dtype=bool).tobytes()
        batches.setdefault(mask, []).append((index, measure))

    frames = [None] * len(measures)
    for batch in batches.values():
        sources = project_contributions(positions, batch[0][1])
        columns = list(sources.columns)
        # Private keys allow arbitrary user-defined metadata columns.
        keys = ['value', *(object() for _ in batch[1:])]
        for key, (_, measure) in zip(keys[1:], batch[1:]):
            sources[key] = measure.dropna().array
        expanded = sources
        if lookthrough:
            columns = list(dict.fromkeys([*columns, 'instrument_type', 'exposure_kind']))
            if group and keep_group_whole:
                intact = sources.source_instrument.isin(group.members)
                children = expand_etfs(sources.loc[~intact], funds, holdings, additional_measures=keys[1:])
                expanded = pd.concat([sources.loc[intact], children], ignore_index=True)
            else:
                expanded = expand_etfs(sources, funds, holdings, additional_measures=keys[1:])
        if group:
            expanded = group_exposures(expanded, group)
        common = expanded.loc[:, columns].set_axis(columns, axis='columns')
        for key, (index, _) in zip(keys, batch):
            frames[index] = common.assign(value=expanded[key])
    return frames
