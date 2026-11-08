"""Shared source-position valuation, independent of screen and exposure settings."""
from portfolio_app.performance.performance import position_performance
from portfolio_app.portfolio.valuation import value_holdings
from portfolio_app.market.fx import current_rate


def prepare_portfolio(holdings, prices, *, refresh=False, reporting_currency='EUR', historical=None):
    valued = value_holdings(holdings, prices, refresh=refresh, reporting_currency=reporting_currency)
    currencies = set(valued.acquisition_currency.dropna()) if 'acquisition_currency' in valued else set()
    rates = {}
    for currency in currencies - {'', reporting_currency}:
        fx = current_rate(prices, currency, reporting_currency, refresh=refresh)
        if fx.quote:
            rates[currency] = fx.quote.price
    return position_performance(valued, rates, reporting_currency=reporting_currency, historical=historical)
