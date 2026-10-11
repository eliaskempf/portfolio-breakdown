"""Currency-bearing derivative facts shared by journals and market adapters.

Amounts stay decimal in native units. A quoted price is an observation, never
permission to post a cash movement. No provider or UI dependencies live here.
"""
from dataclasses import dataclass
from datetime import date as calendar_date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN

from portfolio_app.holdings import DataError

CURRENCIES = ('EUR', 'USD', 'GBP')
ZERO = Decimal('0')


def decimal(value, name='Amount') -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise DataError(f'{name} must be a finite decimal number.')
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise DataError(f'{name} must be a finite decimal number.') from exc
    if not result.is_finite() or abs(result) > Decimal('1e24'):
        raise DataError(f'{name} must be finite and within the supported range.')
    return result


def money(value) -> Decimal:
    """Posted cash rounds once per event, half-even to the currency's cent."""
    return decimal(value).quantize(Decimal('.01'), rounding=ROUND_HALF_EVEN)


def currency(value):
    if value not in CURRENCIES:
        raise DataError('Currency must be EUR, USD or GBP.')
    return value


def iso_date(value, name='Date', *, optional=False):
    if optional and value == '':
        return value
    try:
        if not isinstance(value, str) or calendar_date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise DataError(f'{name} must be YYYY-MM-DD.') from exc
    return value


def identifier(value, name='ID'):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise DataError(f'{name} must be nonempty text without surrounding spaces.')
    return value


@dataclass(frozen=True)
class ContractSpec:
    product: str
    expiry: str
    currency: str
    multiplier: Decimal
    tick: Decimal
    exchange: str = 'EUREX'
    name: str = ''
    source: str = ''
    as_of: str = ''
    last_trade_date: str = ''
    final_settlement_date: str = ''
    return_convention: str = 'unknown'

    def __post_init__(self):
        identifier(self.product, 'Product'); identifier(self.exchange, 'Exchange')
        iso_date(self.expiry, 'Expiry'); currency(self.currency)
        for field in ('multiplier', 'tick'):
            value = decimal(getattr(self, field), field)
            if value <= 0:
                raise DataError(f'{field} must be positive.')
            object.__setattr__(self, field, value)
        for field in ('as_of', 'last_trade_date', 'final_settlement_date'):
            iso_date(getattr(self, field), field, optional=True)
        if self.final_settlement_date and self.last_trade_date and self.final_settlement_date < self.last_trade_date:
            raise DataError('Final settlement cannot precede last trading.')
        if self.return_convention not in ('price', 'gross', 'net', 'unknown'):
            raise DataError('Unknown underlying return convention.')
        if not all(isinstance(getattr(self, name), str) for name in ('name', 'source')):
            raise DataError('Contract name and provenance must be text.')

    @property
    def id(self):
        return f'{self.exchange}:{self.product}:{self.expiry}'


@dataclass(frozen=True)
class PriceObservation:
    contract_id: str
    date: str
    price: Decimal
    kind: str = 'daily_settlement'
    source: str = 'manual'
    observed_at: str = ''

    def __post_init__(self):
        identifier(self.contract_id, 'Contract ID'); iso_date(self.date)
        value = decimal(self.price, 'Price')
        if value <= 0:
            raise DataError('Index futures prices must be positive.')
        object.__setattr__(self, 'price', value)
        if self.kind not in ('daily_settlement', 'final_settlement', 'mark', 'manual'):
            raise DataError('Unknown observation kind.')
        identifier(self.source, 'Price source')
        if self.observed_at:
            try:
                datetime.fromisoformat(self.observed_at.replace('Z', '+00:00'))
            except (ValueError, AttributeError) as exc:
                raise DataError('Observation time must be an ISO timestamp.') from exc
