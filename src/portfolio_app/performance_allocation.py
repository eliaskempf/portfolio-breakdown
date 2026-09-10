"""Carry comparable performance measures through the existing allocation pipeline."""

from dataclasses import dataclass

import pandas as pd

from portfolio_app.etf import FundSnapshot, expand_etfs, matching_fund
from portfolio_app.exposures import normalize_exposures
from portfolio_app.grouping import InstrumentGroup, group_exposures


@dataclass
class PerformanceExposures:
    cost: pd.DataFrame
    current: pd.DataFrame
    missing: pd.DataFrame

    def measures(self):
        return self.cost, self.current, self.missing


def performance_exposures(positions: pd.DataFrame, funds: list[FundSnapshot], *, lookthrough: bool = False,
                          group: InstrumentGroup | None = None, holdings: pd.DataFrame | None = None) -> PerformanceExposures:
    """ETF constituent history is unknown; grouped whole funds retain their costs.

    Missing held positions travel separately so partial returns cannot appear
    complete. Zero-share plans neither contribute cost nor reduce coverage.
    """
    held = positions.shares > 0
    known = held & positions.unrealized_gain_eur.notna()
    grouped = positions.id.isin(group.members) if group else pd.Series(False, index=positions.index)
    if lookthrough:
        expanded = pd.Series([matching_fund(row, funds) is not None for row in positions.to_dict("records")], index=positions.index)
        known &= ~expanded | grouped
    frames = []
    for measure in (positions.cost_basis.where(known, 0), positions.current_value_eur.where(known, 0),
                    (held & ~known).astype(float)):
        source = positions.copy()
        source["current_value_eur"] = measure
        frame = normalize_exposures(source)
        if lookthrough:
            # Grouped instruments keep their whole value and cost, even when
            # other funds are expanded. The final group has the same identity.
            members = frame.source_instrument.isin(group.members) if group else pd.Series(False, index=frame.index)
            frame = pd.concat([frame.loc[members], expand_etfs(frame.loc[~members], funds, positions if holdings is None else holdings)], ignore_index=True)
        if group:
            frame = group_exposures(frame, group)
        frames.append(frame)
    return PerformanceExposures(*frames)


def add_performance_column(table: pd.DataFrame, keys: list, measures: list[pd.DataFrame], *,
                           key: str, value: str = "value", percent: bool = True) -> pd.DataFrame:
    """Align grouped costs and matching current values, never average returns."""
    totals = [frame.groupby(key)[value].sum() for frame in measures]
    result = table.copy()
    cost, current, missing = [pd.Series([total.get(identity, 0.) for identity in keys], index=result.index) for total in totals]
    covered = (cost > 0) | (current > 0)
    gain = (current - cost).where(covered)
    result["Performance"] = (100 * gain / cost.where(cost > 0)) if percent else gain
    result["Performance coverage"] = ["Unavailable" if not has_cost else "Partial" if unknown > 1e-12 else "Complete"
                                      for has_cost, unknown in zip(covered, missing)]
    columns = list(table.columns)
    anchor = columns.index("Investment") + 1 if "Investment" in columns else 1
    return result[[*columns[:anchor], "Performance", "Performance coverage", *columns[anchor:]]]
