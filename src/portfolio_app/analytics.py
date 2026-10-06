"""Pure snapshot analytics; no market access or Streamlit dependencies."""
from dataclasses import dataclass

import pandas as pd

from portfolio_app.fundamentals import Fundamentals, finite
from portfolio_app.performance import PerformanceSummary, summarize_performance


@dataclass(frozen=True)
class AggregateMetric:
    value: float | None
    covered_value: float
    eligible_value: float
    excluded_count: int


@dataclass
class SnapshotAnalytics:
    known_value: float
    valuation_complete: bool
    missing_valuations: int
    largest_weight: float | None
    top_five_weight: float | None
    effective_holdings: float | None
    metrics: dict[str, AggregateMetric]
    positions: pd.DataFrame
    performance: PerformanceSummary | None


def metric_value(snapshot: Fundamentals | None, key: str):
    metric = snapshot.metrics.get(key) if snapshot else None
    return finite(metric.value) if metric else None


def snapshot_analytics(valued, fundamentals: dict[str, Fundamentals]) -> SnapshotAnalytics:
    held = valued.loc[valued.shares.gt(0)].copy()
    held['current_value_reporting'] = pd.to_numeric(held.current_value_reporting, errors='coerce').map(finite)
    valid = held.current_value_reporting.notna() & held.current_value_reporting.ge(0)
    known = held.loc[valid].copy()
    total = float(known.current_value_reporting.sum())
    values = known.groupby('id').current_value_reporting.sum().sort_values(ascending=False)
    weights = values / total if total > 0 else values * 0
    known['metric_kind'] = [fundamentals.get(row.id, Fundamentals(row.ticker, getattr(row, 'instrument_type', 'unknown') or 'unknown')).kind
                            for row in known.itertuples()]
    result = {}
    for name in ('trailing_pe', 'forward_pe', 'fee', 'distribution_yield'):
        eligible = known.loc[known.metric_kind.isin(['equity'] if name.endswith('_pe') else
                             ['etf'] if name == 'fee' else ['equity', 'etf'])]
        rates = pd.Series([metric_value(fundamentals.get(row.id), name) for row in eligible.itertuples()],
                          index=eligible.index, dtype=float)
        accepted = rates.notna() & (rates.gt(0) if name.endswith('_pe') else rates.ge(0))
        covered = eligible.loc[accepted]
        amount = float(covered.current_value_reporting.sum())
        answer = None
        if amount > 0:
            if name.endswith('_pe'):
                earnings = finite((covered.current_value_reporting / rates.loc[accepted]).sum())
                answer = amount / earnings if earnings is not None and earnings > 0 else None
            else:
                answer = float((covered.current_value_reporting * rates.loc[accepted]).sum()) / amount
        result[name] = AggregateMetric(finite(answer), amount, float(eligible.current_value_reporting.sum()), int((~accepted).sum()))
        if name in {'fee', 'distribution_yield'}:
            result[name + '_reporting'] = AggregateMetric(finite(answer * amount) if answer is not None else None,
                                                    amount, result[name].eligible_value, result[name].excluded_count)
        held[name] = [metric_value(fundamentals.get(row.id), name) for row in held.itertuples()]
    performance = summarize_performance(held) if 'unrealized_gain_reporting' in held else None
    return SnapshotAnalytics(total, bool(valid.all()), int((~valid).sum()),
                             float(weights.iloc[0]) if total > 0 else None,
                             float(weights.head(5).sum()) if total > 0 else None,
                             1 / float((weights ** 2).sum()) if total > 0 else None,
                             result, held, performance)
