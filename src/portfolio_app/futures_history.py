"""Historical *reconstruction* of rolling FMWN; never labelled actual futures fills."""
from dataclasses import dataclass
from datetime import date
import math

import numpy as np
import pandas as pd

from portfolio_app.futures_comparison import cash_growth, financing_from_estr
from portfolio_app.holdings import DataError


@dataclass(frozen=True)
class HistoricalSettings:
    quarters: int = 4
    end_date: str = ''

    def __post_init__(self):
        if type(self.quarters) is not int or not 1 <= self.quarters <= 12:
            raise DataError('Choose 1–12 completed quarterly contracts.')
        if self.end_date:
            date.fromisoformat(self.end_date)


def quarterly_legs(prices, end_date, quarters):
    """Roll on the last observed proxy trading day BEFORE each third Friday.

    This is an explicit model convention, not the historical Eurex roll calendar.
    Each selected leg holds the next quarterly maturity; quantity stays fixed.
    """
    end = pd.Timestamp(end_date)
    boundaries = []
    for year in range(prices.index[0].year, end.year+1):
        for month in (3, 6, 9, 12):
            first = pd.Timestamp(year, month, 1)
            expiry = first + pd.Timedelta(days=(4-first.weekday()) % 7 + 14)
            if expiry > end:
                continue
            before = prices.index[prices.index < expiry]
            if len(before) and (expiry-before[-1]).days <= 7:
                boundaries.append((before[-1], expiry))
    if len(boundaries) < quarters+1:
        raise DataError('The saved history does not cover that many complete quarterly contracts.')
    chosen = boundaries[-quarters-1:]
    return pd.DataFrame([dict(start=chosen[i][0], end=chosen[i+1][0], expiry=chosen[i+1][1]) for i in range(quarters)])


def historical_projection(position, comparison, settings, prices, rates):
    if position.instrument != 'FMWN' or not position.quote_date or position.reference_price is None:
        raise DataError('Apply a dated FMWN settlement before reconstructing historical contracts.')
    for series in (prices, rates):
        if not isinstance(series.index, pd.DatetimeIndex) or series.empty or series.index.has_duplicates or not series.index.is_monotonic_increasing or not np.isfinite(series).all():
            raise DataError('Historical series must contain ordered, unique, finite daily observations.')
    if prices.le(0).any() or not rates.between(-.1, .2).all():
        raise DataError('Invalid historical price or rate.')
    anchor_day = pd.Timestamp(position.quote_date)
    expiry_day = pd.Timestamp(position.contract_expiry)
    cutoff = pd.Timestamp(settings.end_date or position.quote_date)
    if cutoff > anchor_day:
        raise DataError('The historical end date cannot follow the saved settlement date.')
    anchor_prices = prices.loc[:anchor_day]
    if anchor_prices.empty or (anchor_day-anchor_prices.index[-1]).days > 7:
        raise DataError('Equity history does not cover the settlement anchor.')
    legs = quarterly_legs(prices.loc[:cutoff], cutoff, settings.quarters)
    selected = prices.loc[legs.iloc[0].start:legs.iloc[-1].end]
    if selected.index.to_series().diff().dt.days.dropna().gt(7).any():
        raise DataError('A gap in equity history would hide possible margin breaches.')

    def rate_at(day, *, known=False):
        # €STR is published on the following TARGET business day. For forward
        # reconstruction only an earlier reference date may inform that day's curve.
        available = rates.loc[rates.index < day] if known else rates.loc[:day]
        if available.empty or (day-available.index[-1]).days > 7:
            raise DataError('Historical €STR coverage is missing or stale.')
        return float(available.iloc[-1])

    def financing_at(day):
        if comparison.financing_source == 'manual':
            return comparison.financing_rate
        return financing_from_estr(rate_at(day, known=True), comparison.financing_spread)

    anchor_spot = position.reference_price / (1+financing_at(anchor_day)/360)**((expiry_day-anchor_day).days)

    def future_price(day, expiry):
        spot = anchor_spot * float(prices.loc[day]) / float(anchor_prices.iloc[-1])
        return spot * (1+financing_at(day)/360)**((expiry-day).days)

    # Carry-adjusted prices for two maturities on a roll day are independent.
    # The roll gap is NOT a profit/loss; close the old and open the new at its price.
    legs = legs.copy()
    legs['entry_points'] = [future_price(row.start, row.expiry) for row in legs.itertuples()]
    legs['exit_points'] = [future_price(row.end, row.expiry) for row in legs.itertuples()]
    multiplier = 100*position.contracts
    cost = abs(position.contracts)*(comparison.roll_fee_per_side + 100*comparison.roll_slippage_points)
    initial = position.margin*comparison.margin_stress
    floor = position.maintenance_margin*comparison.margin_stress
    margin_scale = lambda price: price/position.mark_price if comparison.scale_margin else 1.
    eligible = comparison.comparison_capital-cost >= initial*margin_scale(float(legs.iloc[0].entry_points))
    balance = comparison.comparison_capital-cost if eligible else comparison.comparison_capital
    savings = comparison.comparison_capital
    active = eligible and bool(position.contracts)
    status = 'Held through selected quarters' if active else 'No futures position' if eligible else 'Below initial margin + entry cost'
    exit_day = None
    leg_index = 0
    previous_price = float(legs.iloc[0].entry_points)
    last_day = selected.index[0]
    rows = []
    for day in selected.index:
        if day != last_day:
            for cash_day in pd.date_range(last_day, day-pd.Timedelta(days=1), freq='D'):
                benchmark = rate_at(cash_day) if comparison.benchmark_source == 'ecb' else comparison.savings_rate
                savings *= 1+benchmark/360
            balance += max(balance, 0)*(cash_growth(comparison.cash_rate, (day-last_day).days/365.25)-1)
            if active:
                leg = legs.iloc[leg_index]
                current_price = future_price(day, leg.expiry)
                balance += multiplier*(current_price-previous_price)
                previous_price = current_price
        if active and balance <= floor*margin_scale(previous_price):
            balance -= cost
            active, exit_day, status = False, day, 'Maintenance-floor exit'
        if active and day == legs.iloc[leg_index].end:
            balance -= cost  # Close the old contract, including final exit.
            if leg_index == len(legs)-1:
                active = False
            elif balance-cost < initial*margin_scale(float(legs.iloc[leg_index+1].entry_points)):
                active, exit_day, status = False, day, 'Insufficient opening margin at roll'
            else:
                leg_index += 1
                balance -= cost  # Open the next maturity, no roll-gap P&L.
                previous_price = float(legs.iloc[leg_index].entry_points)
        rows.append(dict(date=day, futures=balance if eligible else float('nan'), cash=savings,
                         index=comparison.comparison_capital*float(prices.loc[day]/selected.iloc[0])))
        last_day = day
    return pd.DataFrame(rows), legs, dict(status=status, exit_date=exit_day, eligible=eligible,
        anchor_proxy_date=anchor_prices.index[-1].date().isoformat())
