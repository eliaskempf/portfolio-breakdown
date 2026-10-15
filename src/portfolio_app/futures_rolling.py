"""Finite-horizon quarterly rolling scenarios; no fresh cash or re-entry.

NTR index GBM, constant ACT/360 financing and F(t,T)=I(t)*carry(T-t).
Roll gaps change the next entry price, never cash P&L. Weekday observations and
rolls one weekday before third Friday are conventions, not an exchange calendar.
"""
from dataclasses import replace
import math

import numpy as np
import pandas as pd

from portfolio_app.futures_comparison import cash_growth, collateral_grid, comparison_dates, evaluate_paths
from portfolio_app.holdings import DataError
from portfolio_app.futures_pricing import carry_factor, execution_cost, quarterly_maturities


def rolling_calendar(position, settings):
    start, end = map(pd.Timestamp, comparison_dates(settings, position))
    if not settings.rolling:
        if position.contract_expiry and end > pd.Timestamp(position.contract_expiry):
            raise DataError('A single-contract horizon must end by expiry. Select Hold & roll quarterly for longer holdings.')
        dates = pd.bdate_range(start, end).union(pd.DatetimeIndex([start, end])).sort_values()
        return dates, pd.DataFrame(columns=['date', 'old_expiry', 'next_expiry'])
    if not position.contract_expiry:
        raise DataError('Apply a dated contract before simulating quarterly rolls.')
    expiries = quarterly_maturities(position.contract_expiry, end)
    rolls = [day - pd.offsets.BDay(1) for day in expiries]
    if start >= rolls[0]:
        raise DataError('Start before the first model roll day, or apply the next contract reference.')
    dates = pd.bdate_range(start, end).union(pd.DatetimeIndex([start, end])).sort_values()
    schedule = pd.DataFrame([dict(date=roll, old_expiry=old, next_expiry=new)
        for roll, old, new in zip(rolls, expiries, expiries[1:]) if start < roll < end])
    return dates, schedule


def simulate_holding(position, settings, *, paths=5000, seed=104729, index_ratios=None):
    """Stream shared daily paths across cash levels, keeping memory bounded.

    Optional index_ratios[date,path] supplies deterministic NTR index paths for
    tests. Both breach types are absorbing and mutually exclusive first exits.
    """
    if not 1 <= paths <= 20000:
        raise DataError('Use 1–20,000 paths.')
    dates, schedule = rolling_calendar(position, settings)
    if index_ratios is not None:
        index_ratios = np.asarray(index_ratios, dtype=float)
        if (index_ratios.ndim != 2 or index_ratios.shape[0] != len(dates) or index_ratios.shape[1] < 1
                or not np.isfinite(index_ratios).all() or (index_ratios <= 0).any()
                or not np.allclose(index_ratios[0], 1)):
            raise DataError('Index paths must match model dates and start at one.')
        paths = index_ratios.shape[1]
    per_side = settings.roll_fee_per_side + 100*settings.roll_slippage_points
    capitals = np.r_[collateral_grid(position, settings, opening_cost_per_contract=per_side), settings.comparison_capital]
    cost = execution_cost(position, settings)
    initial = position.margin*settings.margin_stress
    maintenance = position.maintenance_margin*settings.margin_stress
    eligible = capitals-cost >= initial
    balances = np.repeat((capitals-cost)[:, None], paths, axis=1)
    active = np.broadcast_to(eligible[:, None] & bool(position.contracts), balances.shape).copy()
    touched = active & (balances <= maintenance)
    balances -= touched*cost
    active &= ~touched
    roll_failed = np.zeros_like(active)
    maturity = pd.Timestamp(position.contract_expiry) if position.contract_expiry else dates[-1]
    carry = lambda days: carry_factor(settings.financing_rate, days)
    spot = np.full(paths, position.mark_price/carry((maturity-dates[0]).days))
    initial_spot = spot.copy()
    previous_price = np.full(paths, position.mark_price)
    rng = np.random.default_rng(seed)
    roll_dates = {row.date: row.next_expiry for row in schedule.itertuples()}
    rows = []

    def record(day):
        elapsed = (day-dates[0]).days
        low, median, high = np.quantile(balances[-1], [.05, .5, .95]) if eligible[-1] else [np.nan]*3
        rows.append(dict(day=elapsed, low=low, median=median, high=high,
            cash=capitals[-1]*cash_growth(settings.savings_rate, elapsed/365.25),
            index_median=capitals[-1]*np.median(spot/initial_spot),
            breach=100*touched[-1].mean() if eligible[-1] else np.nan,
            roll_failure=100*roll_failed[-1].mean() if eligible[-1] else np.nan,
            forced_exit=100*(touched[-1] | roll_failed[-1]).mean() if eligible[-1] else np.nan))

    record(dates[0])
    for i, day in enumerate(dates[1:], 1):
        days = (day-dates[i-1]).days
        dt = days/365.25
        if index_ratios is None:
            spot *= np.exp((settings.index_return-settings.volatility**2/2)*dt
                           + settings.volatility*math.sqrt(dt)*rng.standard_normal(paths))
        else:
            spot = initial_spot*index_ratios[i]
        price = spot*carry((maturity-day).days)
        balances += np.maximum(balances, 0)*(cash_growth(settings.cash_rate, dt)-1)
        balances += active*(100*position.contracts*(price-previous_price))
        scale = price/position.mark_price if settings.scale_margin else 1.
        breach = active & (balances <= maintenance*scale)
        touched |= breach
        balances -= breach*cost
        active &= ~breach
        if day in roll_dates:
            balances -= active*cost  # close old contract
            maturity = roll_dates[day]
            price = spot*carry((maturity-day).days)
            scale = price/position.mark_price if settings.scale_margin else 1.
            failed = active & (balances-cost < initial*scale)
            roll_failed |= failed
            active &= ~failed
            balances -= active*cost  # open next; roll gap is not a settlement
        if i == len(dates)-1:
            balances -= active*cost  # close at finite horizon
        previous_price = price
        # Monthly-ish chart samples, plus every roll and the exact final point.
        if i % 21 == 0 or day in roll_dates or i == len(dates)-1:
            record(day)
    years = (dates[-1]-dates[0]).days/365.25
    # Reuse the single-mode table's initial funding/threshold metadata only.
    metadata_settings = replace(settings, fees_per_contract=cost/abs(position.contracts) if position.contracts else 0.,
                                horizon_days=(dates[-1]-dates[0]).days)
    result = evaluate_paths(position, metadata_settings, np.ones((2, 1)), capitals[:-1])
    # Fully funded, always-long buy-and-hold on the SAME index paths. This
    # benchmark remains invested regardless of futures eligibility or exits.
    result['Index median end value (EUR)'] = capitals[:-1]*np.median(spot/initial_spot)
    for j in range(len(result)):
        if not eligible[j]:
            result.loc[j, ['Roll funding failure (%)', 'Any forced exit (%)']] = np.nan
            continue
        probability = float(touched[j].mean())
        z = 1.96
        center = (probability+z*z/(2*paths))/(1+z*z/paths)
        spread = z*math.sqrt(probability*(1-probability)/paths+z*z/(4*paths*paths))/(1+z*z/paths)
        low, median, high = np.quantile(balances[j], [.05, .5, .95])
        savings = capitals[j]*cash_growth(settings.savings_rate, years)
        values = {'Margin breach (%)': 100*probability,
            'Sampling low (%)': 100*max(0., center-spread) if position.contracts else 0.,
            'Sampling high (%)': 100*min(1., center+spread) if position.contracts else 0.,
            'Roll funding failure (%)': 100*roll_failed[j].mean(),
            'Any forced exit (%)': 100*(touched[j] | roll_failed[j]).mean(),
            'Futures p05 end value (EUR)': low, 'Futures median end value (EUR)': median,
            'Futures p95 end value (EUR)': high, 'Futures median gain (EUR)': median-capitals[j],
            'Futures beat cash (%)': 100*(balances[j] > savings).mean()}
        for column, value in values.items():
            result.loc[j, column] = value
    # The simple cash/price barrier formula assumes fixed absolute margin.
    if settings.scale_margin:
        result[['Adverse move to floor (%)', 'Initial floor price (points)']] = np.nan
    return result, pd.DataFrame(rows), schedule


def simulate_rolling(position, settings, **kwargs):
    """Compatibility entry point; both strategies share simulate_holding."""
    if not settings.rolling:
        raise DataError('Select rolling mode to simulate quarterly rolls.')
    return simulate_holding(position, settings, **kwargs)
