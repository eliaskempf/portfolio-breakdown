"""Finite-reserve funding replay. Pure calculations; no portfolio writes or I/O.

Historical index moves stress today's integer contract position. Rates and
margin rules are scenario assumptions. Transfer arrivals follow the margin
check; re-entry affects the next interval. This is not a broker backtest.
"""
from dataclasses import dataclass
import math

import numpy as np
import pandas as pd

from portfolio_app.futures import CONTRACTS
from portfolio_app.futures_comparison import cash_growth
from portfolio_app.futures_pricing import carry_factor, execution_cost, quarterly_maturities
from portfolio_app.holdings import DataError

MODEL_VERSION = 'contract-funding-v1'


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True)
class FundingPolicy:
    amount: float
    mode: str = 'target'
    proportional: bool = False
    frequency: str = 'monthly'
    trigger: float | None = .5
    delay: int = 3

    def __post_init__(self):
        if not finite_number(self.amount) or not 0 <= self.amount <= 1e9:
            raise DataError('Funding target or buffer must be finite and nonnegative.')
        if self.mode not in ('target', 'buffer') or self.frequency not in ('monthly', 'quarterly'):
            raise DataError('Invalid funding target mode or reset schedule.')
        if type(self.proportional) is not bool:
            raise DataError('Notional scaling must be boolean.')
        if type(self.delay) is not int or not 1 <= self.delay <= 21:
            raise DataError('Transfer delay must be 1–21 observations; zero-lag transfers are unsupported.')
        if self.trigger is not None and (not finite_number(self.trigger) or not 0 <= self.trigger <= 1):
            raise DataError('Early funding trigger must be between zero and one, or disabled.')


@dataclass(frozen=True)
class FundingStudy:
    reserve: float
    baseline: FundingPolicy
    candidates: tuple[FundingPolicy, ...]
    years: int = 1
    step: int = 1
    low_vol_only: bool = False
    vol_threshold: float = .15
    volatility_margin: bool = False

    def __post_init__(self):
        if not finite_number(self.reserve) or not 0 < self.reserve <= 1e9:
            raise DataError('Enter a positive total reserve.')
        if not isinstance(self.baseline, FundingPolicy) or not self.candidates or len(self.candidates) > 12:
            raise DataError('Choose one baseline and 1–12 candidate policies.')
        if any(not isinstance(p, FundingPolicy) or p.amount > self.reserve for p in (*self.candidates, self.baseline)):
            raise DataError('Each target or buffer must fit within the total reserve.')
        if type(self.years) is not int or self.years not in (1, 5) or type(self.step) is not int or self.step not in (1, 21):
            raise DataError('Choose one or five years and daily or 21-observation starts.')
        if any(type(v) is not bool for v in (self.low_vol_only, self.volatility_margin)):
            raise DataError('Cohort and margin selections must be boolean.')
        if not finite_number(self.vol_threshold) or not 0 < self.vol_threshold <= 1:
            raise DataError('Opening volatility threshold must be positive and at most 100%.')


@dataclass(frozen=True)
class ReplayPath:
    source_dates: pd.DatetimeIndex
    dates: pd.DatetimeIndex
    spot: np.ndarray
    settlement: np.ndarray
    opening: np.ndarray
    initial: np.ndarray
    maintenance: np.ndarray
    roll: np.ndarray
    cost: float
    multiplier: float
    contracts: int
    mark: float


def validate_prices(prices):
    if (not isinstance(prices, pd.Series) or not isinstance(prices.index, pd.DatetimeIndex)
            or len(prices) < 2 or prices.index.tz is not None or prices.index.hasnans
            or prices.index.has_duplicates or not prices.index.is_monotonic_increasing
            or not prices.index.equals(prices.index.normalize())
            or not np.isfinite(prices.to_numpy(dtype=float)).all() or prices.le(0).any()):
        raise DataError('History requires ordered, unique daily dates and finite positive index levels.')
    if prices.index.to_series().diff().dt.days.dropna().gt(7).any():
        raise DataError('History has a gap longer than seven days; replay could hide funding events.')


def trailing_volatility(prices):
    validate_prices(prices)
    # Full trailing window only. No invented first return or volatility fallback.
    return prices.pct_change().rolling(20, min_periods=20).std(ddof=1) * math.sqrt(260)


def prepare_replay(position, settings, prices, *, volatility=None, volatility_margin=False):
    validate_prices(prices)
    if position.instrument != 'FMWN' or not position.quote_date or not position.contract_expiry:
        raise DataError('Apply a dated FMWN contract before running a funding study.')
    start = pd.Timestamp(settings.start_date or position.quote_date)
    expiry = pd.Timestamp(position.contract_expiry)
    if start >= expiry:
        raise DataError('The simulation start must precede the saved contract expiry.')
    dates = pd.DatetimeIndex(start + (prices.index - prices.index[0]))
    maturities = quarterly_maturities(expiry, dates[-1])
    roll_indices = {}
    for old, new in zip(maturities, maturities[1:]):
        if old > dates[-1]:
            break
        idx = int(dates.searchsorted(old, side='left') - 1)
        if idx <= 0:
            raise DataError('The first contract is too near expiry for these observations; select the next contract.')
        roll_indices[idx] = new
    spot = position.mark_price / carry_factor(settings.financing_rate, (expiry-start).days) * (prices.to_numpy()/prices.iloc[0])
    settlement, opening = np.empty(len(dates)), np.empty(len(dates))
    rolled = np.zeros(len(dates), dtype=bool)
    for i, day in enumerate(dates):
        settlement[i] = spot[i] * carry_factor(settings.financing_rate, (expiry-day).days)
        if i in roll_indices:
            expiry = roll_indices[i]
            rolled[i] = True
        opening[i] = spot[i] * carry_factor(settings.financing_rate, (expiry-day).days)
    stress = np.ones(len(dates))
    if volatility_margin:
        if volatility is None:
            raise DataError('Volatility-linked margin requires trailing history before the first observation.')
        vol = volatility.reindex(prices.index).to_numpy()
        if not np.isfinite(vol).all() or (vol < 0).any():
            raise DataError('Volatility-linked margin needs 20 prior returns; no fallback is supplied.')
        stress = np.clip(vol / .15, 1., 3.)
    scale = opening / position.mark_price if settings.scale_margin else np.ones(len(dates))
    stress *= settings.margin_stress
    return ReplayPath(prices.index, dates, spot, settlement, opening,
        position.margin * scale * stress, position.maintenance_margin * scale * stress,
        rolled, execution_cost(position, settings), CONTRACTS[position.instrument][1], position.contracts, position.mark_price)


def replay_policy(path: ReplayPath, settings, reserve: float, policy: FundingPolicy, *, record=False):
    """Return a reconciled summary and optional close-event ledger for one path."""
    if not finite_number(reserve) or reserve <= 0 or policy.amount > reserve:
        raise DataError('Funding target or buffer must fit a positive finite reserve.')
    def target(i):
        scale = path.opening[i]/path.mark if policy.proportional else 1.
        return policy.amount*scale + (path.maintenance[i] if policy.mode == 'buffer' else 0.)

    opening_cash = max(target(0), path.initial[0] + path.cost)
    eligible = opening_cash <= reserve
    broker = opening_cash - path.cost if eligible else reserve
    bank = reserve - opening_cash if eligible else 0.
    held = eligible and bool(path.contracts)
    transit, due = 0., -1
    market, carry, bank_interest, broker_interest = 0., 0., 0., 0.
    costs = path.cost if eligible else 0.
    liquidations = reentries = roll_failures = topups = missed = 0
    shortfall = False
    min_bank, max_broker = bank, opening_cash if eligible else broker
    peak, drawdown, cash_area = reserve, -costs, 0.
    rows = []
    dates = path.dates
    day_counts = np.diff(dates.values).astype('timedelta64[D]').astype(int)
    monthly = dates.month.to_numpy()
    quarters = dates.quarter.to_numpy()
    bank_factors = {d: cash_growth(settings.savings_rate, d/365.25)-1 for d in set(day_counts)}
    broker_factors = {d: cash_growth(settings.cash_rate, d/365.25)-1 for d in set(day_counts)}
    def log(i, **events):
        if record:
            rows.append(dict(source_date=path.source_dates[i], date=dates[i], broker=broker, bank=bank,
                transit=transit, wealth=broker+bank+transit, held=held,
                notional=path.contracts*path.multiplier*path.opening[i] if held else 0.,
                initial_margin=path.initial[i], maintenance=path.maintenance[i], headroom=broker-path.maintenance[i],
                target=target(i), transfer_due_observation=due if due >= 0 else None,
                transfer_due_date=dates[due] if 0 <= due < len(dates) else None,
                settlement_price=path.settlement[i], opening_price=path.opening[i],
                market_pnl=market, carry_pnl=carry, bank_interest=bank_interest,
                broker_interest=broker_interest, costs=costs, **events))
    log(0, maintenance_checked=path.maintenance[0], entry_cost=costs, exit_cost=0., received=0., dispatched=0., withdrawal=0.,
        liquidation=False, reentry=False, roll=False, roll_failure=False,
        opening_broker=opening_cash if eligible else broker, opening_bank=bank,
        opening_held=False, interval_market=0., interval_carry=0., interval_bank_interest=0., interval_broker_interest=0.)
    if eligible:
        for i in range(1, len(dates)):
            days = day_counts[i-1]
            before_broker, before_bank, before_held = broker, bank, held
            cash_area += broker*days
            bi, ki = max(broker, 0)*broker_factors[days], max(bank, 0)*bank_factors[days]
            broker += bi; bank += ki
            broker_interest += bi; bank_interest += ki
            mp = cp = 0.
            if held:
                mp = path.contracts*path.multiplier*(path.spot[i]-path.spot[i-1])
                total = path.contracts*path.multiplier*(path.settlement[i]-path.opening[i-1])
                cp = total-mp  # Exact residual carry/basis component, not another charge.
                broker += total; market += mp; carry += cp
            elif path.contracts:
                missed += 1
            # On a roll day check the old contract's margin before changing maturity.
            old_floor = path.maintenance[i]
            if settings.scale_margin:
                old_floor *= path.settlement[i]/path.opening[i]
            liquidated = held and broker < old_floor
            exit_cost = entry_cost = received = dispatched = withdrawn = 0.
            if liquidated:
                broker -= path.cost; costs += path.cost; exit_cost += path.cost
                liquidations += 1; held = False
            rolling_held = held and path.roll[i]
            if rolling_held:
                broker -= path.cost; costs += path.cost; exit_cost += path.cost; held = False
            if due >= 0 and i >= due:
                received = transit; broker += transit; transit = 0.; due = -1
            entered = failed_roll = False
            final = i == len(dates)-1
            if not final and not held and path.contracts:
                if broker >= path.initial[i]+path.cost:
                    broker -= path.cost; costs += path.cost; entry_cost += path.cost; held = True
                    if not rolling_held:
                        reentries += 1; entered = True
                elif rolling_held:
                    roll_failures += 1; failed_roll = True
            if final:
                if held:
                    broker -= path.cost; costs += path.cost; exit_cost += path.cost; held = False
            else:
                reset = (monthly[i] != monthly[i-1] if policy.frequency == 'monthly' else quarters[i] != quarters[i-1])
                raw_target = target(i)
                effective = max(raw_target, path.initial[i]+path.cost)
                if reset and held and due < 0 and broker > effective:
                    withdrawn = broker-effective; broker -= withdrawn; bank += withdrawn
                early = policy.trigger is not None and broker-path.maintenance[i] <= policy.trigger*max(raw_target-path.maintenance[i], 0.)
                if path.contracts and due < 0 and ((reset and broker < effective) or early or not held):
                    desired = max(effective-broker, 0.)
                    shortfall |= desired > max(bank, 0.)+1e-8
                    dispatched = min(desired, max(bank, 0.))
                    if dispatched > 1e-8:
                        bank -= dispatched; transit += dispatched; due = i+policy.delay; topups += 1
                    else:
                        dispatched = 0.
            wealth = broker+bank+transit
            peak = max(peak, wealth); drawdown = min(drawdown, wealth-peak)
            min_bank = min(min_bank, bank); max_broker = max(max_broker, broker)
            log(i, maintenance_checked=old_floor, entry_cost=entry_cost, exit_cost=exit_cost, received=received, dispatched=dispatched,
                withdrawal=withdrawn, liquidation=bool(liquidated), reentry=entered, roll=bool(path.roll[i]),
                roll_failure=failed_roll, opening_broker=before_broker, opening_bank=before_bank,
                opening_held=before_held, interval_market=mp, interval_carry=cp,
                interval_bank_interest=ki, interval_broker_interest=bi)
    wealth = broker+bank+transit
    expected = reserve+market+carry+bank_interest+broker_interest-costs
    if not math.isclose(wealth, expected, abs_tol=1e-6, rel_tol=1e-10):
        raise DataError('Funding replay failed its wealth reconciliation.')
    return dict(eligible=eligible, opening_cash=opening_cash, opening_uplift=max(0., opening_cash-target(0)),
        wealth=wealth, profit=wealth-reserve, market_pnl=market, carry_pnl=carry,
        bank_interest=bank_interest, broker_interest=broker_interest, costs=costs,
        liquidations=liquidations, reentries=reentries, roll_failures=roll_failures, topups=topups,
        missed_intervals=missed, reserve_shortfall=shortfall, min_bank=min_bank,
        max_broker=max_broker, average_broker=cash_area/(dates[-1]-dates[0]).days if eligible else np.nan,
        max_drawdown=drawdown, ending_transit=transit), pd.DataFrame(rows)
