"""Decimal inputs and linked cost calculations, independent of the UI."""
from decimal import Decimal, localcontext
import re

from portfolio_app.holdings import DataError


def decimal_input(value: object, label: str, *, optional: bool = False) -> Decimal | None:
    text = str(value if value is not None else '').strip().replace(',', '.')
    if not text:
        if optional:
            return None
        raise DataError(f'{label}: enter a quantity (zero is allowed).')
    if len(text) > 80 or not re.fullmatch(r'(?:\d+(?:\.\d*)?|\.\d+)', text):
        raise DataError(f'{label}: use a nonnegative decimal without thousands separators.')
    number = Decimal(text)
    if number > Decimal('1e18'):
        raise DataError(f'{label}: number is too large.')
    return number


def compact_decimal(value: object) -> str:
    if value is None or str(value).lower() in {'', 'nan'}:
        return ''
    text = format(Decimal(str(value)), 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def linked_costs(quantity: Decimal | None, amount: Decimal | None, source: str) -> tuple[Decimal | None, Decimal | None]:
    """Return average and total, retaining the explicitly entered cost."""
    if amount is None:
        return None, None
    with localcontext() as context:
        context.prec = 50
        if source == 'average':
            return amount, None if quantity is None else amount * quantity
        if source != 'total':
            raise ValueError('Unknown cost source')
        if quantity is None:
            return None, amount
        if quantity == 0:
            if amount:
                raise DataError('Enter a quantity greater than zero for a positive total buy-in.')
            return Decimal(0), amount
        return amount / quantity, amount
