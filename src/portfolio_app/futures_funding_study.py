"""Paired historical windows and attribution, independent from presentation."""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math

import numpy as np
import pandas as pd

from portfolio_app.futures_funding import MODEL_VERSION, prepare_replay, replay_policy, trailing_volatility, validate_prices
from portfolio_app.holdings import DataError

COMPONENTS = ('market_pnl', 'carry_pnl', 'bank_interest', 'broker_interest', 'costs')


@dataclass
class FundingResult:
    windows: pd.DataFrame
    summary: pd.DataFrame
    metadata: dict


def run_identity(position, settings, study, data_hash):
    snapshot = dict(model=MODEL_VERSION, position=asdict(position), financial=asdict(settings),
                    study=asdict(study), data_hash=data_hash)
    return sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest(), snapshot


def historical_windows(prices, study):
    validate_prices(prices)
    volatility = trailing_volatility(prices)
    starts = np.arange(0, len(prices), study.step)
    ends = prices.index.searchsorted(prices.index[starts]+pd.DateOffset(years=study.years))
    covered = ends < len(prices)
    starts, ends = starts[covered], ends[covered]
    warm = np.isfinite(volatility.iloc[starts].to_numpy())
    keep = warm.copy() if study.volatility_margin or study.low_vol_only else np.ones(len(starts), dtype=bool)
    if study.low_vol_only:
        keep &= volatility.iloc[starts].to_numpy() <= study.vol_threshold
    metadata = dict(covered_windows=len(starts), excluded_warmup=int((~warm).sum()) if study.volatility_margin or study.low_vol_only else 0,
        excluded_cohort=int((warm & (volatility.iloc[starts].to_numpy() > study.vol_threshold)).sum()) if study.low_vol_only else 0,
        window_count=int(keep.sum()), overlapping=True)
    if not keep.any():
        raise DataError('History does not contain complete windows for the selected horizon, warm-up and cohort.')
    return list(zip(starts[keep].tolist(), ends[keep].tolist())), volatility, metadata


def paired_window(position, settings, study, prices, volatility, *, record=False, candidate=0):
    path = prepare_replay(position, settings, prices, volatility=volatility, volatility_margin=study.volatility_margin)
    baseline, baseline_log = replay_policy(path, settings, study.reserve, study.baseline, record=record)
    choices = [(candidate, study.candidates[candidate])] if record else enumerate(study.candidates)
    rows, logs = [], {}
    for idx, policy in choices:
        result, ledger = replay_policy(path, settings, study.reserve, policy, record=record)
        eligible = result['eligible'] and baseline['eligible']
        row = dict(candidate=idx, start=prices.index[0], end=prices.index[-1],
            simulation_start=path.dates[0], simulation_end=path.dates[-1], **result)
        row['paired_eligible'] = eligible
        row['baseline_eligible'] = baseline['eligible']
        row['baseline_wealth'] = baseline['wealth']
        row['baseline_opening_cash'] = baseline['opening_cash']
        row['delta_wealth'] = result['wealth']-baseline['wealth'] if eligible else np.nan
        for component in COMPONENTS:
            row['delta_'+component] = result[component]-baseline[component] if eligible else np.nan
        if eligible:
            attribution = sum(row['delta_'+name] * (-1 if name == 'costs' else 1) for name in COMPONENTS)
            if not math.isclose(row['delta_wealth'], attribution, abs_tol=1e-6, rel_tol=1e-10):
                raise DataError('Paired funding attribution failed reconciliation.')
        rows.append(row)
        if record:
            logs[idx] = ledger
    return rows, logs, baseline_log


def summarize_windows(windows):
    summaries = []
    for candidate, all_rows in windows.groupby('candidate', sort=True):
        rows = all_rows.loc[all_rows.paired_eligible]
        n = len(rows)
        delta = rows.delta_wealth
        result = dict(candidate=candidate, windows=len(all_rows), paired_windows=n,
            infeasible_windows=len(all_rows)-n,
            liquidation_frequency=100*rows.liquidations.gt(0).mean(),
            roll_failure_frequency=100*rows.roll_failures.gt(0).mean(),
            shortfall_frequency=100*rows.reserve_shortfall.mean(),
            mean_delta=delta.mean(), median_delta=delta.median(), worst_delta=delta.min(),
            lower_tail_mean=delta.nsmallest(max(1, math.ceil(.01*n))).mean(),
            average_broker=rows.average_broker.mean(), opening_cash=rows.opening_cash.mean(),
            missed_intervals=rows.missed_intervals.mean())
        for component in COMPONENTS:
            result['delta_'+component] = rows['delta_'+component].mean()
        summaries.append(result)
    return pd.DataFrame(summaries)


def run_funding_study(position, settings, study, history):
    windows, volatility, metadata = historical_windows(history.prices, study)
    records = []
    for start, end in windows:
        rows, _, _ = paired_window(position, settings, study, history.prices.iloc[start:end+1], volatility)
        records.extend(rows)
    frame = pd.DataFrame(records)
    identity, snapshot = run_identity(position, settings, study, history.digest)
    metadata.update(snapshot, identity=identity, source=history.source, qualification=history.qualification,
                    source_start=str(history.prices.index[0].date()), source_end=str(history.prices.index[-1].date()))
    return FundingResult(frame, summarize_windows(frame), metadata)


def short_horizon_moves(prices, observations, *, short=False):
    """Raw proxy moves across all windows; path extremes include interim closes."""
    validate_prices(prices)
    if type(observations) is not int or not 1 <= observations < len(prices):
        raise DataError('Insufficient history for that transfer delay.')
    values = prices.to_numpy()
    moves = np.column_stack([values[i:len(values)-observations+i]/values[:-observations]-1
                             for i in range(1, observations+1)])
    adverse = -moves if short else moves
    lows = adverse.min(axis=1)
    return dict(observations=observations, windows=len(lows), worst_endpoint=float(adverse[:, -1].min()),
                worst_running=float(lows.min()), p01_running=float(np.quantile(lows, .01)))
