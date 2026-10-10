"""Index futures teaser: signed exposure is separate from portfolio wealth.

The base portfolio is valued independently. Mock cumulative futures settlement
is added exactly once and never written into the base holdings/cash balances.
Prices may use a dated settlement or manual scenario; margins may use a dated
broker reference snapshot. Neither implies an executed trade.
"""
from dataclasses import dataclass, replace
from datetime import date
import math

import pandas as pd

from portfolio_app.holdings import DataError


def require_eur_reporting(valued: pd.DataFrame) -> None:
    """Reject mixed units before combining reporting values with EUR contracts.

    Futures capital, fees, liabilities and notionals remain explicitly EUR.
    A reporting-column rename is not an FX conversion. Main valuation supplies
    both row metadata and frame attrs; check every declaration that is present.
    """
    declared = valued.attrs.get('reporting_currency')
    if declared is None and 'reporting_currency' not in valued:
        raise DataError('EUR reporting currency metadata is required for leverage calculations.')
    if declared is not None and declared != 'EUR':
        raise DataError('Leverage calculations currently require EUR reporting currency.')
    if 'reporting_currency' in valued and (valued.reporting_currency.isna().any() or not valued.reporting_currency.eq('EUR').all()):
        raise DataError('Leverage calculations currently require EUR reporting currency for every position.')
    if 'current_value_reporting' not in valued:
        raise DataError('Leverage calculations require current reporting-currency valuations.')


CONTRACTS = {
    'FMWN': ('MSCI World', 100., 'https://www.eurex.com/ex-en/markets/idx/msci/regional/MSCI-World-Index-Futures-in-EUR-254542'),
    # Retain the identity of older saved simulations until explicitly replaced.
    'FMAE': ('MSCI ACWI', 100., 'https://www.eurex.com/ex-en/markets/idx/msci/regional/MSCI-ACWI-Index-Futures-254014'),
}


@dataclass(frozen=True)
class FuturePosition:
    contracts: int = 1  # Signed; zero disables the overlay.
    entry_price: float = 500.0
    mark_price: float = 500.0
    margin_per_contract: float = 5000.0
    collateral_budget: float = 0.0
    base_beta: float = 1.0
    futures_beta: float = 1.0
    instrument: str = 'FMWN'
    maintenance_per_contract: float | None = None
    quote_date: str = ''
    contract_expiry: str = ''  # Last trading day, not final cash-settlement day.
    reference_price: float | None = None
    price_source: str = ''
    proxy_position_id: str = ''  # Explicit companion ETF and approximate index holdings.
    bucket_id: str = ''  # Stable strategic leaf; blank stays Unassigned.
    entry_date: str = ''
    entry_reference_price: float | None = None
    entry_price_source: str = ''
    entry_convention: str = ''  # A simulated execution convention, never an actual fill.
    reference_capital_eur: float | None = None  # Opt-in dedicated capital, never inferred from margin.
    net_funding_eur: float = 0.  # Extra deposits minus withdrawals since inception.
    closed_pnl_eur: float = 0.  # Prior contracts, before fees.
    fees_eur: float = 0.  # Lifetime transaction fees; cash earns no interest.

    def __post_init__(self):
        for name in ('reference_capital_eur', 'net_funding_eur', 'closed_pnl_eur', 'fees_eur'):
            value = getattr(self, name)
            if name == 'reference_capital_eur' and value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise DataError(f'{name} must be a finite number.')
        if self.reference_capital_eur is not None and self.reference_capital_eur <= 0:
            raise DataError('Reference capital must be positive.')
        if self.fees_eur < 0:
            raise DataError('Transaction fees must be nonnegative.')
        if self.reference_capital_eur is None and any((self.net_funding_eur, self.closed_pnl_eur, self.fees_eur)):
            raise DataError('Lifetime funding and gains require reference capital.')
        if not isinstance(self.proxy_position_id, str):
            raise DataError('The companion ETF must use a stable position ID.')
        if not isinstance(self.bucket_id, str):
            raise DataError('Futures category must be a stable bucket ID.')
        if self.instrument not in CONTRACTS:
            raise DataError('Unsupported futures instrument.')
        try:
            for stamp in (self.quote_date, self.contract_expiry, self.entry_date):
                if stamp:
                    date.fromisoformat(stamp)
            if not all(isinstance(value, str) for value in (self.entry_price_source, self.entry_convention)):
                raise ValueError('Entry provenance must be text.')
            if self.entry_date:
                if not self.quote_date or self.entry_date > self.quote_date:
                    raise ValueError('The entry date must not follow the mark date.')
                if not self.entry_price_source or self.entry_reference_price is None:
                    raise ValueError('A dated entry requires its reference price and source.')
            elif self.entry_reference_price is not None or self.entry_price_source:
                raise ValueError('Entry provenance requires an entry date.')
            if self.entry_reference_price is not None and (isinstance(self.entry_reference_price, bool) or not math.isfinite(self.entry_reference_price) or self.entry_reference_price <= 0):
                raise ValueError('Invalid entry reference price.')
            if bool(self.quote_date) != bool(self.contract_expiry):
                raise ValueError('Both quote date and last trading day are required.')
            if self.quote_date and self.reference_price is None:
                raise ValueError('A dated quote requires its reference price.')
            if self.quote_date and self.quote_date >= self.contract_expiry:
                raise ValueError('The quote must precede the last trading day.')
            if self.reference_price is not None and (not math.isfinite(self.reference_price) or self.reference_price <= 0):
                raise ValueError('Invalid reference price.')
        except (ValueError, TypeError) as exc:
            raise DataError(f'Invalid futures price reference: {exc}') from exc
        if type(self.contracts) is not int or abs(self.contracts) > 10000:
            raise DataError('Contracts must be a whole number between -10,000 and 10,000.')
        for name in ('entry_price', 'mark_price', 'margin_per_contract', 'collateral_budget', 'base_beta', 'futures_beta'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise DataError(f'{name} must be a finite number.')
        if self.entry_price <= 0 or self.mark_price <= 0:
            raise DataError('Futures prices must be positive.')
        if self.margin_per_contract < 0 or self.collateral_budget < 0:
            raise DataError('Margin and collateral must be nonnegative.')
        if self.maintenance_per_contract is not None and (not math.isfinite(self.maintenance_per_contract) or not 0 <= self.maintenance_per_contract <= self.margin_per_contract):
            raise DataError('Maintenance margin must be between zero and initial margin.')
        if not -3 <= self.base_beta <= 3 or not -3 <= self.futures_beta <= 3:
            raise DataError('Teaser beta assumptions must be between -3 and 3.')
        if not all(math.isfinite(value) for value in (self.notional, self.pnl, self.margin, self.lifetime_gain, self.capital_value)):
            raise DataError('Futures position exceeds the supported numeric range.')

    @property
    def lifetime_gain(self) -> float:
        return self.closed_pnl_eur + self.pnl - self.fees_eur

    @property
    def capital_value(self) -> float:
        return (self.reference_capital_eur or 0.) + self.net_funding_eur + self.lifetime_gain

    @property
    def reference_return_pct(self) -> float | None:
        return 100 * self.lifetime_gain / self.reference_capital_eur if self.reference_capital_eur else None

    @property
    def notional(self) -> float:
        return self.contracts * CONTRACTS[self.instrument][1] * self.mark_price

    @property
    def pnl(self) -> float:
        return self.contracts * CONTRACTS[self.instrument][1] * (self.mark_price - self.entry_price)

    @property
    def margin(self) -> float:
        return abs(self.contracts) * self.margin_per_contract

    @property
    def maintenance_margin(self) -> float:
        return abs(self.contracts) * (self.margin_per_contract if self.maintenance_per_contract is None else self.maintenance_per_contract)


@dataclass(frozen=True)
class FuturesSummary:
    base_value: float | None
    base_exposure: float | None
    equity: float | None
    pnl: float
    notional: float
    gross_multiple: float | None
    net_multiple: float | None
    beta: float | None
    missing_prices: int


def close_contract(position: FuturePosition) -> FuturePosition:
    """Carry active P&L into lifetime history; retain the dedicated cash balance."""
    if position.reference_capital_eur is None:
        raise DataError('Closing with retained cash requires reference capital.')
    return replace(position, contracts=0, closed_pnl_eur=position.closed_pnl_eur + position.pnl)


def funded_strategy_positions(valued, position, allocation):
    """Overview-only capital row, never a broker holding or optimizer purchase."""
    if position is None or position.reference_capital_eur is None:
        return valued
    require_eur_reporting(valued)
    row = dict(position_id='future:overlay', id=position.instrument, name=f'{position.instrument} future',
        instrument_type='future_strategy', shares=1., current_value_reporting=position.capital_value,
        bucket_id=position.bucket_id if position.bucket_id in allocation.leaves() else '',
        cost_basis=position.reference_capital_eur, cost_basis_reporting=position.reference_capital_eur,
        unrealized_gain_reporting=position.lifetime_gain, cost_estimated=False,
        unrealized_gain=position.lifetime_gain, return_pct=position.reference_return_pct,
        acquisition_currency='EUR', reporting_currency='EUR', within_bucket_target=float('nan'),
        performance_note='Lifetime gain / fixed reference capital; not time-weighted return.')
    result = pd.concat([valued, pd.DataFrame([row])], ignore_index=True)
    result.attrs = dict(valued.attrs, reporting_currency='EUR')
    return result


def summarize_future(valued: pd.DataFrame, position: FuturePosition) -> FuturesSummary:
    require_eur_reporting(valued)
    held = valued.loc[valued.shares.gt(0)]
    values = pd.to_numeric(held.current_value_reporting, errors='coerce')
    valid = values.map(lambda value: math.isfinite(value) and value >= 0)
    missing = int((~valid).sum())
    base = float(values.sum()) if not missing else None
    # No ETF look-through here: each funded position is counted once. Cash is
    # excluded from the market-notional numerator, but remains in equity.
    cash = held.get('instrument_type', pd.Series('', index=held.index)).eq('cash')
    exposure = float(values.loc[~cash].sum()) if not missing else None
    equity = base + position.capital_value if base is not None else None
    meaningful = equity is not None and equity > 0
    return FuturesSummary(
        base, exposure, equity, position.lifetime_gain, position.notional,
        (exposure + abs(position.notional)) / equity if meaningful else None,
        (exposure + position.notional) / equity if meaningful else None,
        (base * position.base_beta + position.notional * position.futures_beta) / equity if meaningful else None,
        missing,
    )


def futures_scenarios(summary: FuturesSummary, position: FuturePosition,
                      moves: list[float], *, move_base: bool = False,
                      assumptions=None, portfolio_result=None) -> pd.DataFrame:
    """Shocks start at the current mark, keeping contracts fixed (no rebalancing).

With move_base, a benchmark shock drives base and futures via assumed betas.
Otherwise the shock is directly to the futures price and the base stays flat.
"""
    rows = []
    debt = portfolio_result.liabilities if portfolio_result is not None else 0.
    current_equity = portfolio_result.net_equity if portfolio_result is not None else summary.equity
    for move in moves:
        if not math.isfinite(move):
            raise DataError('Scenario moves must be finite.')
        future_move = move * position.futures_beta if move_base else move
        price = position.mark_price * (1 + future_move)
        if price < 0:
            raise DataError('This scenario implies a negative index futures price.')
        additional = position.notional * future_move
        cumulative = position.lifetime_gain + additional
        budget = assumptions.comparison_capital if assumptions is not None else position.collateral_budget
        floor = position.maintenance_margin
        if assumptions is not None:
            floor *= assumptions.margin_stress
            if assumptions.scale_margin:
                floor *= price/position.mark_price
        base_change = summary.base_value * position.base_beta * move if move_base and summary.base_value is not None else 0.
        funding = (position.reference_capital_eur or 0.) + position.net_funding_eur
        without = summary.base_value + base_change + funding - debt if summary.base_value is not None and debt is not None else None
        equity = without + cumulative if without is not None else None
        delta = base_change + additional if current_equity is not None else None
        rows.append({
            'Market move (%)': move * 100,
            'Futures price (points)': price,
            'Additional futures P&L (EUR)': additional,
            'Cumulative futures P&L (EUR)': cumulative,
            'Without futures (EUR)': without,
            'With futures (EUR)': equity,
            'Change from current equity (EUR)': delta,
            'Change from current equity (%)': 100 * delta / current_equity if current_equity is not None and current_equity > 0 else None,
            'Illustrative collateral shortfall (EUR)': max(0., floor - budget - cumulative),
        })
    return pd.DataFrame(rows)
