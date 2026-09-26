"""EUR valuation; unavailable prices are unknown, never zero or cost basis."""

import math
from datetime import datetime, timezone

import pandas as pd

from portfolio_app.prices import PriceService


def portfolio_weights(values: pd.Series) -> pd.Series:
    total = values.sum()
    return values / total if total > 0 else pd.Series(float("nan"), index=values.index)


def value_holdings(holdings: pd.DataFrame, prices: PriceService, *, refresh: bool = False) -> pd.DataFrame:
    result = holdings.copy()
    numeric_columns = ("current_price", "fx_to_eur", "current_value_eur", "price_age_hours", "fx_age_hours")
    text_columns = ("quote_currency", "price_status", "fx_status", "price_observed_at", "fx_observed_at", "valuation_note")
    for column in numeric_columns:
        result[column] = float("nan")
    for column in text_columns:
        result[column] = ""
    active = holdings.loc[holdings["shares"] > 0]
    if 'manual_price' in active:
        active = active.loc[active.manual_price.isna()]
    price_results = {ticker: prices.price(ticker, refresh=refresh) for ticker in active["ticker"].unique() if ticker}
    currencies = {item.quote.currency for item in price_results.values() if item.quote}
    fx_results = {currency: prices.fx(currency, refresh=refresh) for currency in currencies}
    for index, position in holdings.iterrows():
        if position["shares"] == 0:
            result.at[index, "current_value_eur"] = 0.
            result.at[index, "price_status"] = "not_held"
            result.at[index, "valuation_note"] = "Zero shares; target-only position."
            continue
        manual = position.get('manual_price', float('nan'))
        if pd.notna(manual) and manual != '':
            currency = position['manual_price_currency']
            fx = prices.fx(currency, refresh=refresh)
            result.at[index, 'current_price'] = float(manual)
            result.at[index, 'quote_currency'] = currency
            result.at[index, 'price_status'] = 'manual'
            observed = datetime.fromisoformat(position['manual_price_date']).replace(tzinfo=timezone.utc)
            result.at[index, 'price_observed_at'] = observed.isoformat()
            result.at[index, 'price_age_hours'] = max(0, (prices.now() - observed).total_seconds() / 3600)
            result.at[index, 'valuation_note'] = 'Manual unit price; update independently of confirmed quantity.'
            result.at[index, 'fx_status'] = fx.status
            if fx.quote is not None and fx.quote.currency == 'EUR':
                result.at[index, 'fx_to_eur'] = fx.quote.price
                result.at[index, 'fx_observed_at'] = fx.quote.observed_at.isoformat()
                result.at[index, 'fx_age_hours'] = max(0, (prices.now() - fx.quote.observed_at).total_seconds() / 3600)
                value = position['shares'] * float(manual) * fx.quote.price
                if math.isfinite(value):
                    result.at[index, 'current_value_eur'] = value
            else:
                result.at[index, 'valuation_note'] += ' Missing FX conversion.'
            continue
        if not position["ticker"]:
            result.at[index, "valuation_note"] = "Missing ticker"
            result.at[index, "price_status"] = "missing"
            continue
        price = price_results[position["ticker"]]
        result.at[index, "price_status"] = price.status
        if price.quote is None:
            result.at[index, "valuation_note"] = f"Missing price: {price.error}"
            continue
        quote = price.quote
        result.at[index, "current_price"] = quote.price
        result.at[index, "quote_currency"] = quote.currency
        result.at[index, "price_observed_at"] = quote.observed_at.isoformat()
        result.at[index, "price_age_hours"] = max(0, (prices.now() - quote.observed_at).total_seconds() / 3600)
        fx = fx_results[quote.currency]
        result.at[index, "fx_status"] = fx.status
        notes = [f"Price refresh failed: {price.error}"] if price.error else []
        if position.get('instrument_type') == 'crypto' and result.at[index, 'price_age_hours'] >= 24:
            notes.append('Crypto quote is at least 24 hours old; markets trade continuously.')
        if fx.quote is None:
            notes.append(f"Missing {quote.currency}/EUR exchange rate: {fx.error}")
        elif fx.quote.currency != "EUR":
            notes.append("FX quote must be expressed in EUR")
        else:
            rate = fx.quote.price
            result.at[index, "fx_to_eur"] = rate
            result.at[index, "fx_observed_at"] = fx.quote.observed_at.isoformat()
            result.at[index, "fx_age_hours"] = max(0, (prices.now() - fx.quote.observed_at).total_seconds() / 3600)
            value = position["shares"] * quote.price * rate
            if math.isfinite(value):
                result.at[index, "current_value_eur"] = value
            else:
                notes.append("Position value exceeds supported numeric range")
            if fx.error:
                notes.append(f"FX refresh failed: {fx.error}")
        result.at[index, "valuation_note"] = "; ".join(notes)
    result["portfolio_weight"] = portfolio_weights(result["current_value_eur"])
    return result
