"""Monetary text formatting with explicit units and no session dependencies."""
import math


def currency_symbol(currency):
    return {'EUR': '€', 'USD': '$', 'GBP': '£'}[currency]


def format_money(value, currency, *, signed=False, missing='—'):
    """Missing amounts stay missing; signed performance text puts its sign first."""
    if value is None or not math.isfinite(value):
        return missing
    symbol = currency_symbol(currency)
    if signed:
        return f'{"-" if value < 0 else "+"}{symbol}{abs(value):,.2f}'
    return f'{symbol}{value:,.2f}'


def euros(value):
    """Fixed-EUR futures/leverage amounts, independent of reporting currency."""
    return format_money(value, 'EUR')
