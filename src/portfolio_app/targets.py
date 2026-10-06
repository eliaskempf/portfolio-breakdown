"""Derive whole-portfolio targets through the same exposure/allocation pipeline."""

from dataclasses import dataclass

import pandas as pd

from portfolio_app.etf import FundSnapshot, expand_etfs
from portfolio_app.exposures import normalize_exposures
from portfolio_app.grouping import InstrumentGroup, group_exposures


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
    frames = []
    for measure in (targets.fillna(0), targets.isna().astype(float)):
        source = positions.copy()
        source["current_value_reporting"] = measure
        frame = normalize_exposures(source)
        if lookthrough:
            frame = expand_etfs(frame, funds, positions if holdings is None else holdings)
        if group is not None:
            frame = group_exposures(frame, group)
        frames.append(frame)
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
    known = pd.Series([totals.known.get(key, 0.) for key in keys], index=result.index)
    missing = pd.Series([totals.missing.get(key, 0.) > 0 for key in keys], index=result.index)
    result["Current portfolio %"] = (100 * result["Value"] / portfolio_value
                                      if valuation_complete and portfolio_value > 0 else float("nan"))
    result["Target portfolio %"] = (100 * known).mask(missing)
    result["Gap (pp)"] = result["Current portfolio %"] - result["Target portfolio %"]
    if missing.any():
        result["Known target portfolio %"] = 100 * known
        result["Target status"] = missing.map({True: "Incomplete", False: "Complete"})
    return result
