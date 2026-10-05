"""Original cost components and target-specific conversion evidence.

A component describes currently held quantity, not a tax lot or disposal ledger.
All persisted decimals remain strings. A summary fingerprint detects external edits.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import math

from portfolio_app.holdings import DataError
from portfolio_app.fx import valid_currency, current_rate

FIELD = 'cost_basis_details'


def decimal(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def fingerprint(row):
    return [str(decimal(row.get(k))) if decimal(row.get(k)) is not None else '' for k in ('shares', 'acquisition_price')] + [row.get('acquisition_currency') or '']


def same_summary(a, b):
    return all((decimal(x) == decimal(y) if i < 2 else x == y) for i, (x, y) in enumerate(zip(a, b)))


def component(shares, amount, currency, day='', conversions=None):
    return dict(shares=str(shares), amount='' if amount is None else str(amount), currency=currency or '',
                date=day or '', conversions=conversions or {})


def aggregate_component(row):
    quantity, price = decimal(row.get('shares')), decimal(row.get('acquisition_price'))
    return component(quantity or 0, quantity * price if quantity is not None and price is not None else None,
                     row.get('acquisition_currency', ''))


def validate_components(parts):
    if not isinstance(parts, list) or not parts:
        raise DataError('Cost records must contain at least one component.')
    for part in parts:
        if not isinstance(part, dict) or decimal(part.get('shares')) is None:
            raise DataError('Invalid cost quantity.')
        if part.get('amount') != '' and decimal(part.get('amount')) is None:
            raise DataError('Invalid original cost.')
        if part.get('currency') and not valid_currency(part['currency']):
            raise DataError('Invalid original cost currency.')
        day = part.get('date', '')
        if day:
            try:
                if date.fromisoformat(day) > date.today():
                    raise ValueError
            except (ValueError, TypeError):
                raise DataError('Purchase date must be YYYY-MM-DD, no later than today.') from None
        if not isinstance(part.get('conversions', {}), dict):
            raise DataError('Invalid cost conversions.')
        for target, conversion in part.get('conversions', {}).items():
            if (not valid_currency(target) or not isinstance(conversion, dict)
                    or decimal(conversion.get('amount')) is None
                    or conversion.get('method') not in {'supplied_cost', 'supplied_rate', 'historical', 'estimate'}):
                raise DataError('Invalid converted cost.')
            if 'rate' in conversion and (decimal(conversion['rate']) is None or decimal(conversion['rate']) <= 0):
                raise DataError('Exchange rate must be finite and positive.')


def encode_components(parts, row):
    validate_components(parts)
    return json.dumps(dict(version=1, summary=fingerprint(row), components=parts), separators=(',', ':'), allow_nan=False)


def read_details(value):
    try:
        raw = json.loads(value)
        if not isinstance(raw, dict) or raw.get('version') != 1 or len(raw.get('summary', [])) != 3:
            raise ValueError
        validate_components(raw.get('components'))
        quantity = decimal(raw['summary'][0])
        actual = sum(decimal(part['shares']) for part in raw['components'])
        if quantity is None or abs(actual - quantity) > max(Decimal('1e-12'), quantity * Decimal('1e-12')):
            raise ValueError
        return raw
    except (ValueError, TypeError, KeyError) as exc:
        raise DataError('Stored cost details are invalid. Restore them from a backup.') from exc


def active_components(row):
    value = row.get(FIELD, '')
    if isinstance(value, str) and value:
        raw = read_details(value)
        if same_summary(raw['summary'], fingerprint(row)):
            return deepcopy(raw['components'])
        # External balance/cost edit: the summary supersedes old purchase evidence.
        return [aggregate_component(row)]
    if not row.get('balance_replaced_at') and row.get('purchase_history'):
        parts = _legacy_components(row)
        if parts is not None:
            return parts
    return [aggregate_component(row)]


def _legacy_components(row):
    from portfolio_app.purchases import read_purchase_history
    try:
        batches = read_purchase_history(row['purchase_history'])
        parts, previous = [], None
        for batch in batches:
            opening = batch.get('opening', {})
            if batch['mode'] == 'reconcile':
                parts = []
            elif previous is None:
                if decimal(opening.get('shares', '0')):
                    parts = [aggregate_component(opening)]
            elif not same_summary(fingerprint(opening), previous):
                return None
            for purchase in batch['purchases']:
                shares, price, fees = (decimal(purchase.get(k)) for k in ('shares', 'price', 'fees'))
                if shares is None or fees is None:
                    return None
                parts.append(component(shares, None if price is None else shares * price + fees,
                                       batch['currency'], purchase['date']))
            previous = fingerprint(dict(shares=batch.get('result_shares'), acquisition_price=batch.get('result_average'),
                                        acquisition_currency=batch['currency'] if batch.get('result_average') else ''))
        if previous is not None and same_summary(previous, fingerprint(row)) and sum(decimal(p['shares']) for p in parts) == decimal(row['shares']):
            return parts
    except (DataError, KeyError, TypeError):
        pass
    return None


def supplied_conversion(part, target, *, rate=None, amount=None):
    if amount is not None:
        converted, method = decimal(amount), 'supplied_cost'
    else:
        original, ratio = decimal(part['amount']), decimal(rate)
        if original is None or ratio is None or ratio <= 0:
            raise DataError('Supply an original cost and a positive exchange rate.')
        converted, method = original * ratio, 'supplied_rate'
    if converted is None or not valid_currency(target):
        raise DataError('Supply a finite nonnegative converted cost and a currency.')
    record = dict(amount=str(converted), method=method, saved_at=datetime.now(timezone.utc).isoformat())
    if method == 'supplied_rate':
        record['rate'] = str(rate)
    part.setdefault('conversions', {})[target] = record


@dataclass(frozen=True)
class ResolvedCost:
    amount: float | None
    estimated: bool = False
    note: str = ''


def resolve_cost(parts, target, historical=None):
    total, estimated, notes = Decimal(0), False, []
    for part in parts:
        amount = decimal(part['amount'])
        saved = part.get('conversions', {}).get(target)
        if saved:
            total += decimal(saved['amount'])
            estimated |= saved['method'] == 'estimate'
            notes.append('Estimated using latest FX' if saved['method'] == 'estimate' else
                         f"Historical FX ({saved.get('observed_on', '')})" if saved['method'] == 'historical' else 'Supplied conversion')
        elif amount is None:
            return ResolvedCost(None, note='Missing average buy-in or purchase cost; excluded from gains')
        elif not part['currency']:
            return ResolvedCost(None, note='Missing buy-in currency; excluded from gains')
        elif part['currency'] == target or amount == 0:
            total += amount
        elif part['date'] and historical is not None:
            conversion = historical.get(part['currency'], target, part['date'])
            if conversion.rate is None:
                return ResolvedCost(None, note=conversion.note + ' Excluded from gains.')
            total += amount * Decimal(str(conversion.rate))
            notes.append(f'Historical market FX ({conversion.observed_on})')
        else:
            return ResolvedCost(None, note='Missing purchase date or converted cost; excluded from gains')
    value = float(total)
    return ResolvedCost(value if math.isfinite(value) else None, estimated,
                        '; '.join(dict.fromkeys(notes)) if math.isfinite(value) else 'Cost exceeds supported numeric range')


def estimate_missing(parts, target, prices, historical=None):
    result = deepcopy(parts)
    for part in result:
        if resolve_cost([part], target, historical).amount is not None:
            continue
        amount = decimal(part['amount'])
        if amount is None or not valid_currency(part['currency']):
            raise DataError('Enter the original cost and currency before estimating.')
        rate = current_rate(prices, part['currency'], target)
        if rate.quote is None or rate.status == 'stale':
            raise DataError('A current FX lookup is required before confirming an estimate. Refresh prices and retry.')
        part.setdefault('conversions', {})[target] = dict(amount=str(amount * Decimal(str(rate.quote.price))),
            rate=str(rate.quote.price), observed_on=rate.quote.observed_at.isoformat(), method='estimate',
            saved_at=datetime.now(timezone.utc).isoformat())
    return result


def freeze_historical(parts, target, historical):
    """Persist successful historical lookups alongside user-entered source records."""
    result = deepcopy(parts)
    if historical is None:
        return result
    for part in result:
        amount = decimal(part['amount'])
        if amount is not None and part['currency'] != target and part['date'] and target not in part['conversions']:
            fx = historical.get(part['currency'], target, part['date'])
            if fx.rate is not None:
                part['conversions'][target] = dict(amount=str(amount * Decimal(str(fx.rate))), rate=str(fx.rate),
                    observed_on=fx.observed_on, source=fx.source, method='historical')
    return result
