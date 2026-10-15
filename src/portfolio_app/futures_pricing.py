"""Shared approximate quarterly pricing and execution conventions."""
import pandas as pd

from portfolio_app.futures import CONTRACTS


def carry_factor(rate, days):
    return (1 + rate / 360) ** days


def execution_cost(position, settings):
    multiplier = CONTRACTS[position.instrument][1]
    return abs(position.contracts) * (settings.roll_fee_per_side + multiplier * settings.roll_slippage_points)


def quarterly_maturities(first_expiry, end):
    expiry, end = pd.Timestamp(first_expiry), pd.Timestamp(end)
    expiries = [expiry]
    for year in range(expiry.year, end.year + 2):
        for month in (3, 6, 9, 12):
            first = pd.Timestamp(year, month, 1)
            candidate = first + pd.Timedelta(days=(4 - first.weekday()) % 7 + 14)
            if candidate > expiries[-1]:
                expiries.append(candidate)
    return expiries
