"""Unrealized performance of held positions in their recorded cost currency."""

from dataclasses import dataclass
import math
from collections.abc import Mapping

import pandas as pd


def position_performance(valued: pd.DataFrame, fx_to_eur: Mapping[str, float] | None = None) -> pd.DataFrame:
    """Never infer acquisition currency or convert historical costs at today's FX."""
    result = valued.copy()
    for column in ("cost_basis", "unrealized_gain", "return_pct", "unrealized_gain_eur"):
        result[column] = float("nan")
    result["performance_note"] = ""
    rates = fx_to_eur or {}
    for index, row in result.iterrows():
        note = ""
        cost = row.get("acquisition_price", float("nan"))
        currency = row.get("acquisition_currency", "")
        if row.shares == 0:
            note = "No shares held"
        elif pd.isna(cost):
            note = "Missing average buy-in"
        elif not currency:
            note = "Missing buy-in currency"
        else:
            basis = row.shares * cost
            # Same-currency returns need no FX. Other quotes are converted into
            # the recorded cost currency using current rates, not vice versa.
            if currency == row.quote_currency and pd.notna(row.current_price):
                current = row.shares * row.current_price
            elif currency == "EUR":
                current = row.current_value_eur
            else:
                rate = rates.get(currency, float("nan"))
                current = row.current_value_eur / rate if math.isfinite(rate) and rate > 0 else float("nan")
            if math.isfinite(basis):
                result.at[index, "cost_basis"] = basis
            if not math.isfinite(basis):
                note = "Cost exceeds supported numeric range"
            elif not math.isfinite(current):
                note = "Missing current price or exchange rate"
            elif not math.isfinite(current - basis):
                note = "Gain exceeds supported numeric range"
            else:
                gain = current - basis
                result.at[index, "unrealized_gain"] = gain
                if basis > 0 and math.isfinite(100 * gain / basis):
                    result.at[index, "return_pct"] = 100 * gain / basis
                if currency == "EUR":
                    result.at[index, "unrealized_gain_eur"] = gain
                note = "Return undefined for zero cost" if basis == 0 else ""
        result.at[index, "performance_note"] = note
    return result


@dataclass(frozen=True)
class PerformanceSummary:
    held_count: int
    covered_count: int
    cost_eur: float | None
    gain_eur: float | None
    return_pct: float | None


def summarize_performance(performance: pd.DataFrame) -> PerformanceSummary:
    """Aggregate only comparable, valued EUR-cost rows using total cost weights."""
    held = performance.loc[performance.shares > 0]
    covered = held.loc[held.unrealized_gain_eur.notna()]
    if covered.empty:
        return PerformanceSummary(len(held), 0, None, None, None)
    cost = float(covered.cost_basis.sum())
    gain = float(covered.unrealized_gain_eur.sum())
    return PerformanceSummary(len(held), len(covered), cost, gain, 100 * gain / cost if cost > 0 else None)
