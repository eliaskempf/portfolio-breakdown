"""Shared source-position valuation, independent of screen and exposure settings."""
from portfolio_app.performance.performance import position_performance
from portfolio_app.portfolio.valuation import value_holdings
from portfolio_app.market.fx import current_rate
from portfolio_app.market.prices import QuoteSnapshot


def _foreign_cost_currencies(valued, reporting_currency):
    if 'acquisition_currency' not in valued or 'acquisition_price' not in valued:
        return set()
    currencies = valued.acquisition_currency
    foreign = {c for c in currencies.unique() if isinstance(c, str) and c and c != reporting_currency}
    if foreign:
        held_cost = valued.shares.ne(0) & valued.acquisition_price.notna()
        foreign.intersection_update(currencies[held_cost].unique())
    return foreign


def prepare_portfolio(holdings, prices, *, refresh=False, reporting_currency='EUR', historical=None, cost_cache=None):
    quotes = prices if isinstance(prices, QuoteSnapshot) else QuoteSnapshot(prices)
    valued = value_holdings(holdings, quotes, refresh=refresh, reporting_currency=reporting_currency)

    # Discover acquisition FX alongside outstanding price requests so a cold
    # background valuation can publish both in the same batch.
    rates = {}
    for currency in _foreign_cost_currencies(valued, reporting_currency):
        fx = current_rate(quotes, currency, reporting_currency, refresh=refresh)
        if fx.quote is not None:
            rates[currency] = fx.quote.price
    return position_performance(valued, rates, reporting_currency=reporting_currency, historical=historical,
                                cost_cache=cost_cache)
