"""Reporting-currency valuation; unavailable prices are unknown, never zero or cost basis."""

import math
from functools import cache
from datetime import datetime, timezone

import pandas as pd

from portfolio_app.market.prices import PriceService, QuoteSnapshot
from portfolio_app.market.fx import current_rate
from portfolio_app.portfolio.physical_assets import METALS, pricing_key, price_unit_factor


def portfolio_weights(values: pd.Series) -> pd.Series:
    total = values.sum()
    return values / total if total > 0 else pd.Series(float("nan"), index=values.index)


def value_holdings(holdings: pd.DataFrame, prices: PriceService, *, refresh: bool = False, reporting_currency: str = 'EUR') -> pd.DataFrame:
    result = holdings.copy()
    result['reporting_currency'] = reporting_currency
    result.attrs['reporting_currency'] = reporting_currency
    numeric_columns = ("current_price", "fx_to_reporting", "current_value_reporting", "price_age_hours", "fx_age_hours")
    text_columns = ("quote_currency", "price_status", "fx_status", "price_observed_at", "fx_observed_at", "valuation_note")
    positions = holdings.to_dict('records')
    active = (row for row in positions if row['shares'] > 0
              and (pd.isna(row.get('manual_price')) or row['manual_price'] == ''))
    keys = {pricing_key(row) for row in active} - {''}
    quotes = prices if isinstance(prices, QuoteSnapshot) else QuoteSnapshot(prices)
    price_results = {key: quotes.price(key, refresh=refresh) for key in keys}

    @cache
    def rate_for(currency):
        return current_rate(quotes, currency, reporting_currency, refresh=refresh)

    marks = []
    for position in positions:
        mark = {}
        marks.append(mark)
        if position["shares"] == 0:
            mark["current_value_reporting"] = 0.
            mark["price_status"] = "not_held"
            mark["valuation_note"] = "Zero shares; target-only position."
            continue
        manual = position.get('manual_price')
        if pd.notna(manual) and manual != '':
            currency = position['manual_price_currency']
            fx = rate_for(currency)
            mark['current_price'] = float(manual)
            mark['quote_currency'] = currency
            mark['price_status'] = 'manual'
            observed = datetime.fromisoformat(position['manual_price_date']).replace(tzinfo=timezone.utc)
            mark['price_observed_at'] = observed.isoformat()
            mark['price_age_hours'] = max(0, (prices.now() - observed).total_seconds() / 3600)
            mark['valuation_note'] = 'Manual unit price; update independently of confirmed quantity.'
            if position.get('import_source') == 'snapshot':
                mark['valuation_note'] = f"Imported snapshot unit price ({position['manual_price_date']}); switch explicitly to live pricing."
                if currency != reporting_currency:
                    mark['valuation_note'] += ' Uses available FX, not historical snapshot FX.'
            mark['fx_status'] = fx.status
            if fx.quote is not None and fx.quote.currency == reporting_currency:
                mark['fx_to_reporting'] = fx.quote.price
                mark['fx_observed_at'] = fx.quote.observed_at.isoformat()
                mark['fx_age_hours'] = max(0, (prices.now() - fx.quote.observed_at).total_seconds() / 3600)
                value = position['shares'] * float(manual) * fx.quote.price
                if math.isfinite(value):
                    mark['current_value_reporting'] = value
            else:
                mark['valuation_note'] += ' Missing FX conversion.'
            continue
        key = pricing_key(position)
        if not key:
            mark["valuation_note"] = ("Missing manual unit price" if position.get("instrument_type") == "physical" else "Missing ticker")
            mark["price_status"] = "missing"
            continue
        price = price_results[key]
        mark["price_status"] = price.status
        if price.quote is None:
            mark["valuation_note"] = f"Missing price: {price.error}"
            continue
        quote = price.quote
        unit_price = quote.price * price_unit_factor(position)
        if not math.isfinite(unit_price):
            mark['valuation_note'] = 'Converted unit price exceeds supported numeric range.'
            continue
        mark["current_price"] = unit_price
        mark["quote_currency"] = quote.currency
        mark["price_observed_at"] = quote.observed_at.isoformat()
        mark["price_age_hours"] = max(0, (prices.now() - quote.observed_at).total_seconds() / 3600)
        fx = rate_for(quote.currency)
        mark["fx_status"] = fx.status
        notes = [f"Price refresh failed: {price.error}"] if price.error else []
        if metal := METALS.get(position.get('price_source')):
            notes.append(f"{metal.name} spot (Gold API), per {position['quantity_unit']} of fine {metal.name.lower()}.")
            if mark['price_age_hours'] >= 24:
                notes.append(f'{metal.name} spot quote is at least 24 hours old.')
        if position.get('instrument_type') == 'crypto' and mark['price_age_hours'] >= 24:
            notes.append('Crypto quote is at least 24 hours old; markets trade continuously.')
        if fx.quote is None:
            notes.append(f"Missing {quote.currency}/{reporting_currency} exchange rate: {fx.error}")
        elif fx.quote.currency != reporting_currency:
            notes.append(f"FX quote must be expressed in {reporting_currency}")
        else:
            rate = fx.quote.price
            mark["fx_to_reporting"] = rate
            mark["fx_observed_at"] = fx.quote.observed_at.isoformat()
            mark["fx_age_hours"] = max(0, (prices.now() - fx.quote.observed_at).total_seconds() / 3600)
            value = position["shares"] * unit_price * rate
            if math.isfinite(value):
                mark["current_value_reporting"] = value
            else:
                notes.append("Position value exceeds supported numeric range")
            if fx.error:
                notes.append(f"FX refresh failed: {fx.error}")
        mark["valuation_note"] = "; ".join(notes)
    for column in numeric_columns:
        result[column] = pd.Series([mark.get(column, math.nan) for mark in marks], index=result.index, dtype=float)
    for column in text_columns:
        result[column] = pd.Series([mark.get(column, '') for mark in marks], index=result.index, dtype=str)
    result["portfolio_weight"] = portfolio_weights(result["current_value_reporting"])
    return result
