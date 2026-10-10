"""Application preparation and service selection, independent of Streamlit.

The caller owns navigation, refresh requests and recovery presentation. These
functions do not fetch optional fundamentals or historical risk data.
"""
from dataclasses import dataclass
from typing import NamedTuple

import pandas as pd

from portfolio_app.allocation import Allocation, analysis_targets
from portfolio_app.etf import FundSnapshot
from portfolio_app.futures import FuturePosition
from portfolio_app.futures_workspace import load_sandbox
from portfolio_app.leverage_settings import LeverageSettings, load_leverage_settings, requires_leverage
from portfolio_app.portfolio import prepare_portfolio
from portfolio_app.portfolio_exposure import calculate_portfolio_exposure
from portfolio_app.positions import HoldingsSnapshot
from portfolio_app.prices import PriceService, StaticProvider, UnavailableProvider


class PortfolioInputs(NamedTuple):
    snapshot: HoldingsSnapshot
    allocation: Allocation | None
    classifications: dict
    funds: list[FundSnapshot]


@dataclass(frozen=True)
class LeverageContext:
    sandbox: tuple[FuturePosition, str] | None
    settings: LeverageSettings
    supported: bool
    active: bool
    unsupported_records: bool

    @property
    def future(self):
        return self.sandbox[0] if self.sandbox else None


def load_leverage_context(directory, reporting_currency):
    sandbox = load_sandbox(directory)
    future = sandbox[0] if sandbox else None
    settings, _ = load_leverage_settings(directory, future=future)
    supported = reporting_currency == 'EUR'
    return LeverageContext(sandbox, settings, supported,
        supported and (settings.enabled or sandbox is not None),
        not supported and (settings.enabled or requires_leverage(settings, future)))


def select_price_service(directory, *, offline_demo=False, supplied=None, live_prices=None):
    if supplied is not None:
        return supplied
    if offline_demo:
        try:
            provider = StaticProvider(directory / 'demo_prices.json')
        except (OSError, ValueError):
            provider = UnavailableProvider()
        return PriceService(provider)
    if live_prices is None:
        from portfolio_app.market_data import prices_for
        live_prices = prices_for
    return live_prices(directory)


def historical_for(directory, *, demo=False):
    from portfolio_app.fx import HistoricalFX
    from portfolio_app.market_data import coordinator
    if demo:
        from portfolio_app.fx import DatedRate
        class OfflineFX:
            def get(self, source, target, day):
                # Explicitly invented history, never used for live portfolios.
                rates = {'EUR': 1., 'USD': .9, 'GBP': 1.2}
                if source not in rates or target not in rates:
                    return DatedRate(note='No synthetic history for this currency.')
                return DatedRate(rates[source] / rates[target], day, 'Synthetic demo FX')
        return OfflineFX()
    workspace = str(directory.resolve())
    service = HistoricalFX(directory=directory / '.cache' / 'purchase-fx')

    class BackgroundFX:
        def get(self, source, target, day):
            result, due = service.cached(source, target, day)
            if due:
                coordinator.request(workspace, f'purchase-fx-v1:{source}:{target}:{day}',
                                    lambda: service.get(source, target, day, refresh=True))
            return result
    return BackgroundFX()


@dataclass(frozen=True)
class ValuationStatus:
    estimated_costs: int
    missing_costs: int
    missing_prices: int
    stale_quotes: int
    problems: pd.DataFrame


@dataclass(frozen=True)
class PreparedPortfolio:
    valued: pd.DataFrame
    status: ValuationStatus


def prepare_application(inputs, prices, *, refresh=False, reporting_currency='EUR', historical=None):
    holdings = inputs.snapshot.holdings
    source = analysis_targets(holdings, inputs.allocation) if inputs.allocation else holdings
    valued = prepare_portfolio(source, prices, refresh=refresh,
                               reporting_currency=reporting_currency, historical=historical)
    stale = valued.price_status.isin(['cached fallback', 'stale']) | valued.fx_status.isin(['cached fallback', 'stale'])
    missing_cost = valued.shares.gt(0) & valued.unrealized_gain_reporting.isna()
    problems = valued.loc[valued.shares.gt(0) & (valued.unrealized_gain_reporting.isna() | stale),
                          ['name', 'performance_note', 'valuation_note']].copy()
    return PreparedPortfolio(valued, ValuationStatus(int(valued.cost_estimated.sum()),
        int(missing_cost.sum()), int(valued.current_value_reporting.isna().sum()), int(stale.sum()), problems))


def prepare_exposure(valued, funds, allocation, context):
    """Keep optional EUR exposure behind eligibility and the caller's lifecycle."""
    if not context.active:
        return None
    return calculate_portfolio_exposure(valued, funds, future=context.future, allocation=allocation,
        liabilities=context.settings.liabilities, fund_factors=context.settings.fund_factors)
