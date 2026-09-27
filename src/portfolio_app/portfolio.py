"""Shared source-position valuation, independent of screen and exposure settings."""
from portfolio_app.performance import position_performance
from portfolio_app.valuation import value_holdings


def prepare_portfolio(holdings, prices, *, refresh=False):
    valued = value_holdings(holdings, prices, refresh=refresh)
    currencies = set(valued.get("acquisition_currency", []).dropna()) if "acquisition_currency" in valued else set()
    rates = {}
    for currency in currencies - {"", "EUR"}:
        matching = valued.loc[valued.quote_currency == currency, "fx_to_eur"].dropna()
        if not matching.empty:
            rates[currency] = float(matching.iloc[0])
        elif ((valued.acquisition_currency == currency) & (valued.quote_currency != currency)).any():
            quote = prices.fx(currency, refresh=refresh).quote
            if quote is not None and quote.currency == "EUR":
                rates[currency] = quote.price
    return position_performance(valued, rates)
