"""Derive whole-portfolio targets through the same exposure/allocation pipeline."""

from dataclasses import dataclass

import pandas as pd

from portfolio_app.funds.etf import FundSnapshot
from portfolio_app.exposure.exposure_analysis import align_measure, project_measures
from portfolio_app.portfolio.grouping import InstrumentGroup


@dataclass
class TargetExposures:
    known: pd.DataFrame
    missing: pd.DataFrame


def target_exposures(positions: pd.DataFrame, funds: list[FundSnapshot], *,
                     lookthrough: bool = False, group: InstrumentGroup | None = None,
                     holdings: pd.DataFrame | None = None) -> TargetExposures:
    """Targets survive missing quotes. Missing targets travel as a separate measure.

    Reusing ETF expansion and grouping preserves source identity, residuals and
    the same allocations as the current-value view. Neither measure is scaled
    to sum to one; stored targets always refer to the whole portfolio.
    """
    targets = positions.get("target_allocation", pd.Series(float("nan"), index=positions.index))
    frames = project_measures(positions, (targets.fillna(0), targets.isna().astype(float)), funds,
                              positions if holdings is None else holdings, lookthrough=lookthrough, group=group)
    return TargetExposures(*frames)


@dataclass
class TargetTotals:
    known: pd.Series
    missing: pd.Series


def target_totals(known: pd.DataFrame, missing: pd.DataFrame, *, key: str, value: str = "value") -> TargetTotals:
    return TargetTotals(known.groupby(key)[value].sum(), missing.groupby(key)[value].sum())


def add_target_columns(table: pd.DataFrame, keys: list, totals: TargetTotals, *,
                       portfolio_value: float, valuation_complete: bool) -> pd.DataFrame:
    """Align targets by stable identity, comparing whole-portfolio percentages.

    A partial target cannot produce a meaningful gap. Nor can incomplete price
    coverage produce an accurate whole-portfolio current percentage.
    """
    result = table.copy()
    identities = pd.Index(keys, tupleize_cols=False)
    known = align_measure(totals.known, identities, result.index)
    missing = align_measure(totals.missing, identities, result.index).gt(0)
    result["Current portfolio %"] = (100 * result["Value"] / portfolio_value
                                      if valuation_complete and portfolio_value > 0 else float("nan"))
    result["Target portfolio %"] = (100 * known).mask(missing)
    result["Gap (pp)"] = result["Current portfolio %"] - result["Target portfolio %"]
    if missing.any():
        result["Known target portfolio %"] = 100 * known
        result["Target status"] = missing.map({True: "Incomplete", False: "Complete"})
    return result
