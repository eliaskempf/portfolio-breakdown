"""Prospective same-capital futures/cash experiments, independent of the UI.

Fixed contracts; daily GBM price observations; cash settlement; close on the
first observed maintenance-floor touch. Closing does not cap losses at zero.
This is an illustrative model, not a calibrated liquidation probability.
"""
from dataclasses import dataclass
from datetime import date
import math

import numpy as np
import pandas as pd

from portfolio_app.futures import FuturePosition
from portfolio_app.holdings import DataError


@dataclass(frozen=True)
class ComparisonSettings:
    months: int = 12
    savings_rate: float = .03  # Explicit hypothetical fallback if no ECB observation.
    cash_rate: float = 0.0
    volatility: float = .20
    drift: float = 0.0  # Futures price drift, not a stock-index total-return forecast.
    fees_per_contract: float = 0.0
    margin_stress: float = 1.0
    capital_min: float = 5000.0
    capital_max: float = 50000.0
    levels: int = 10
    start_date: str = ''
    until_expiry: bool = True
    horizon_days: int | None = None
    comparison_capital: float = 10000.0
    rolling: bool = False
    rolling_years: int = 5
    index_return: float = 0.0
    financing_rate: float = .03
    roll_fee_per_side: float = 0.0
    roll_slippage_points: float = 0.0
    scale_margin: bool = True
    historical_drift: bool = True
    financing_source: str = 'ecb'
    benchmark_source: str = 'ecb'
    financing_spread: float = .005  # Editable approximation: 50 basis points over €STR.

    def __post_init__(self):
        if self.financing_source not in ('ecb', 'manual'):
            raise DataError('Choose ECB €STR + spread or manual financing.')
        if not math.isfinite(self.financing_spread) or not -.1 <= self.financing_spread <= .1:
            raise DataError('Financing spread must be between -10 and 10 percentage points.')
        if self.benchmark_source not in ('ecb', 'manual'):
            raise DataError('Choose ECB or manual cash benchmark.')
        if self.start_date:
            try:
                date.fromisoformat(self.start_date)
            except (TypeError, ValueError) as exc:
                raise DataError('Invalid comparison start date.') from exc
        if type(self.rolling_years) is not int or not 1 <= self.rolling_years <= 30:
            raise DataError('Choose 1–30 rolling years.')
        if type(self.rolling) is not bool or type(self.scale_margin) is not bool or type(self.historical_drift) is not bool:
            raise DataError('Rolling and margin scaling must be boolean.')
        if not all(math.isfinite(v) for v in (self.index_return, self.financing_rate, self.roll_fee_per_side, self.roll_slippage_points)):
            raise DataError('Rolling assumptions must be finite.')
        if not -.5 <= self.index_return <= .5 or not -.1 <= self.financing_rate <= .2 or min(self.roll_fee_per_side, self.roll_slippage_points) < 0:
            raise DataError('Invalid rolling return, financing rate or execution costs.')
        if self.horizon_days is not None and (type(self.horizon_days) is not int or not 1 <= self.horizon_days <= (10960 if self.rolling else 1830)):
            raise DataError('Invalid comparison horizon (maximum 30 rolling years or five single-mode years).')
        if type(self.months) is not int or not 1 <= self.months <= 60 or type(self.levels) is not int or not 2 <= self.levels <= 20:
            raise DataError('Use 1–60 months and 2–20 collateral levels.')
        for field in ('savings_rate', 'cash_rate', 'volatility', 'drift', 'fees_per_contract', 'margin_stress', 'capital_min', 'capital_max'):
            if not math.isfinite(getattr(self, field)):
                raise DataError('Comparison assumptions must be finite.')
        if not -.1 <= self.savings_rate <= .2 or not -.1 <= self.cash_rate <= .2:
            raise DataError('Cash rates must be between -10% and 20%.')
        if not 0 <= self.volatility <= 1 or not -.5 <= self.drift <= .5:
            raise DataError('Use 0–100% volatility and -50–50% futures drift.')
        if self.fees_per_contract < 0 or not 1 <= self.margin_stress <= 3:
            raise DataError('Fees must be nonnegative; margin stress must be 1–3×.')
        if not 0 < self.capital_min < self.capital_max <= 1e9:
            raise DataError('Collateral range must be positive, increasing, and at most €1 billion.')
        if not math.isfinite(self.comparison_capital) or not 0 < self.comparison_capital <= 1e9:
            raise DataError('Comparison cash must be positive and at most €1 billion.')

    @property
    def years(self):
        return self.horizon_days / 365.25 if self.horizon_days is not None else self.months / 12


def financing_from_estr(estr_rate, spread):
    """Shared nominal ACT/360 proxy, applied once and only to futures carry."""
    rate = estr_rate + spread
    if not math.isfinite(rate) or not -.1 <= rate <= .2:
        raise DataError('Combined €STR + financing spread must be between -10% and 20%.')
    return rate


def comparison_dates(settings, position):
    """Calendar dates define duration; never simulate an expired single contract."""
    start = date.fromisoformat(settings.start_date or position.quote_date or date.today().isoformat())
    expiry = date.fromisoformat(position.contract_expiry) if position.contract_expiry else None
    if settings.rolling:
        end = (pd.Timestamp(start) + pd.DateOffset(years=settings.rolling_years)).date()
    elif settings.until_expiry and expiry:
        end = expiry
    else:
        end = (pd.Timestamp(start) + pd.DateOffset(months=settings.months)).date()
    if end <= start or (expiry and start >= expiry):
        raise DataError('The simulation start must precede the contract last trading day. Refresh the contract reference.')
    return start, end


def cash_growth(rate, years):
    """Constant nominal overnight rate, daily ACT/360 accrual over model years."""
    return (1 + rate / 360) ** (365.25 * years)


def futures_price_drift(index_return, financing_rate):
    """Index GBM growth less carry decay, matching the quarterly pricing model.

    F(t,T) = I(t) * (1+r/360)**days_to_T, so its annual GBM drift is
    mu_index - 365.25*log(1+r/360). Financing is deducted exactly once.
    """
    return index_return - 365.25*math.log1p(financing_rate/360)


def collateral_grid(position, settings, *, opening_cost_per_contract=None):
    """Keep broad range anchors, adding dense samples near the opening margin.

    The configured min/max remain hard bounds. Up to 41 extra observations span
    the first two opening-requirement-sized cash buffers. This resolves the
    steep part of the curve without spending most samples on its flat tail.
    Every returned point is simulated on the same paths, never interpolated.
    """
    anchors = np.linspace(settings.capital_min, settings.capital_max, settings.levels)
    per_contract = (settings.roll_fee_per_side + 100*settings.roll_slippage_points
                    if settings.rolling else settings.fees_per_contract)
    if opening_cost_per_contract is not None:
        per_contract = opening_cost_per_contract
    opening = position.margin*settings.margin_stress + abs(position.contracts)*per_contract
    if not position.contracts or opening <= 0 or opening > settings.capital_max:
        return anchors
    lower = max(settings.capital_min, opening)
    upper = min(settings.capital_max, lower + 2*opening)
    near_margin = np.linspace(lower, upper, 41)
    selected = [settings.comparison_capital] if settings.capital_min <= settings.comparison_capital <= settings.capital_max else []
    return np.unique(np.r_[anchors, near_margin, selected])


def settled_steps(position, settings, ratios, capitals):
    """One accounting path for terminal sensitivity and the time-series chart."""
    capitals = np.asarray(capitals, dtype=float)
    costs = abs(position.contracts) * settings.fees_per_contract
    eligible = capitals - costs >= position.margin * settings.margin_stress
    balances = np.repeat((capitals - costs)[:, None], ratios.shape[1], axis=1)
    active = np.broadcast_to(eligible[:, None] & (position.contracts != 0), balances.shape).copy()
    touched = active & (balances <= position.maintenance_margin * settings.margin_stress)
    active &= ~touched
    yield balances, touched
    factor = cash_growth(settings.cash_rate, settings.years / (len(ratios) - 1)) - 1
    for previous, current in zip(ratios[:-1], ratios[1:]):
        balances += np.maximum(balances, 0) * factor
        balances += active * (position.notional * (current - previous))
        breach = active & (balances <= position.maintenance_margin * settings.margin_stress)
        touched |= breach
        active &= ~breach
        yield balances, touched


def evaluate_paths(position: FuturePosition, settings: ComparisonSettings, ratios: np.ndarray,
                   capitals=None) -> pd.DataFrame:
    """ratios[time, path] start at 1. Shared paths compare collateral fairly.

    This starts a NEW trade at today's mock mark: prior mock P&L is excluded.
    All assumed execution/roll costs are reserved upfront. Cash interest accrues
    on positive balances, including after model exit. No fresh cash is injected.
    """
    ratios = np.asarray(ratios, dtype=float)
    if ratios.ndim != 2 or min(ratios.shape) < 1 or len(ratios) < 2 or not np.isfinite(ratios).all() or (ratios <= 0).any() or not np.allclose(ratios[0], 1):
        raise DataError('Simulation paths must be finite positive prices starting at one.')
    capitals = np.asarray(capitals if capitals is not None else collateral_grid(position, settings), dtype=float)
    if capitals.ndim != 1 or not np.isfinite(capitals).all() or (capitals < 0).any():
        raise DataError('Collateral amounts must be finite and nonnegative.')
    costs = abs(position.contracts) * settings.fees_per_contract
    initial = position.margin * settings.margin_stress
    floor = position.maintenance_margin * settings.margin_stress
    eligible = capitals - costs >= initial
    for balances, touched in settled_steps(position, settings, ratios, capitals):
        pass
    rows = []
    for index, capital in enumerate(capitals):
        budget = capital - costs
        room = max(0., budget - floor)
        distance = room / abs(position.notional) if position.notional else None
        barrier = position.mark_price * (1 - distance if position.contracts > 0 else 1 + distance) if distance is not None else None
        reachable = barrier is not None and barrier > 0
        savings = capital * cash_growth(settings.savings_rate, settings.years)
        available = bool(eligible[index])
        probability = float(touched[index].mean()) if available else None
        n = ratios.shape[1]
        # Wilson interval measures Monte Carlo sampling error only, not model uncertainty.
        if available and position.contracts:
            z = 1.96
            center = (probability + z*z/(2*n)) / (1 + z*z/n)
            spread = z * math.sqrt(probability*(1-probability)/n + z*z/(4*n*n)) / (1 + z*z/n)
            low, high = max(0., center-spread), min(1., center+spread)
        else:
            low = high = probability
        quantiles = np.quantile(balances[index], [.05, .5, .95]) if available else [None]*3
        rows.append({
            'Collateral (EUR)': capital,
            'Opening status': 'Funded' if available else 'Below initial margin + costs',
            'Notional / collateral (x)': abs(position.notional)/capital if capital > 0 else None,
            'Initial loss buffer (EUR)': room if available else None,
            'Adverse move to floor (%)': 100*distance if available and reachable else None,
            'Initial floor price (points)': barrier if available and reachable else None,
            'Margin breach (%)': 100*probability if available else None,
            'Sampling low (%)': 100*low if available else None,
            'Sampling high (%)': 100*high if available else None,
            'Cash benchmark end value (EUR)': savings,
            'Cash benchmark gain (EUR)': savings-capital,
            'Futures p05 end value (EUR)': quantiles[0],
            'Futures median end value (EUR)': quantiles[1],
            'Futures p95 end value (EUR)': quantiles[2],
            'Futures median gain (EUR)': quantiles[1]-capital if available else None,
            'Futures beat cash (%)': 100*float((balances[index] > savings).mean()) if available else None,
        })
    return pd.DataFrame(rows)


def simulated_ratios(settings, *, paths=5000, seed=104729):
    if not 1 <= paths <= 20000:
        raise DataError('Use 1–20,000 simulation paths.')
    steps = max(1, round(252*settings.years))
    dt = settings.years/steps
    rng = np.random.default_rng(seed)
    increments = (settings.drift - settings.volatility**2/2)*dt + settings.volatility*math.sqrt(dt)*rng.standard_normal((steps, paths))
    return np.vstack([np.ones(paths), np.exp(np.cumsum(increments, axis=0))])


def simulate_comparison(position, settings, *, paths=5000, seed=104729):
    return evaluate_paths(position, settings, simulated_ratios(settings, paths=paths, seed=seed))


def simulate_trajectory(position, settings, *, paths=5000, seed=104729):
    ratios = simulated_ratios(settings, paths=paths, seed=seed)
    capital = settings.comparison_capital
    eligible = capital - abs(position.contracts)*settings.fees_per_contract >= position.margin*settings.margin_stress
    rows = []
    for index, (balances, touched) in enumerate(settled_steps(position, settings, ratios, np.array([capital]))):
        years = settings.years*index/(len(ratios)-1)
        low, median, high = np.quantile(balances[0], [.05, .5, .95]) if eligible else [float('nan')]*3
        rows.append(dict(day=round(365.25*years), low=low, median=median, high=high,
                         cash=capital*cash_growth(settings.savings_rate, years),
                         breach=100*float(touched.mean()) if eligible else float('nan')))
    return pd.DataFrame(rows)
