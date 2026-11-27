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

from portfolio_app.portfolio.csv_summary import source_for

from portfolio_app.portfolio.holdings import DataError
from portfolio_app.market.fx import valid_currency, current_rate

FIELD = 'cost_basis_details'


def decimal(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def fingerprint(row):
    numbers = (decimal(row.get(key)) for key in ('shares', 'acquisition_price'))
    currency = row.get('acquisition_currency')
    return [str(value) if value is not None else '' for value in numbers] + [currency.strip().upper() if isinstance(currency, str) else '']


def same_summary(a, b):
    """Exact decimal equality, including large integers and the absence of a price."""
    if len(a) != 3 or len(b) != 3 or a[2] != b[2]:
        return False
    for left, right in zip(a[:2], b[:2]):
        if left in ('', None) or right in ('', None):
            if left not in ('', None) or right not in ('', None):
                return False
        elif (number := decimal(left)) is None or number != decimal(right):
            return False
    return True


def _live_source(row, live):
    source = source_for(row)
    if source is None:
        return None
    # A purchase form works on exact text; analytical rows carry floats. An
    # in-memory edit from one numeric interpretation to another isn't evidence.
    if isinstance(row.get('shares'), str) and isinstance(row.get('acquisition_price', ''), str):
        raw = (row['shares'].strip(), row.get('acquisition_price', '').strip(), live[2])
        return source if raw == source.tokens or same_summary(raw, source.tokens) else None
    return source if same_summary(live, source.parsed) else None


def stored_snapshot(row, *, csv_values=False):
    """Mark only exact CSV decimals we can prove, or values about to be written verbatim."""
    live = fingerprint(row)
    source = _live_source(row, live)
    if source is not None:
        quantity, price, currency = source.tokens
    elif csv_values:
        quantity, price, currency = row.get('shares'), row.get('acquisition_price'), live[2]
    else:
        return live, False
    shares = decimal(quantity)
    unit_price = decimal(price)
    if shares is None or (price not in ('', None) and unit_price is None):
        # The shipped readers also accept certain non-Decimal spellings. The
        # loader's actual numeric values remain valid unmarked evidence.
        if source is not None:
            quantity, price, currency = source.parsed
            live = fingerprint(dict(shares=quantity, acquisition_price=price, acquisition_currency=currency))
        return live, False
    return [str(shares), str(unit_price) if unit_price is not None else '', currency], True


def matches_snapshot(row, saved, *, history=False, source_exact=False):
    live = fingerprint(row)
    source = _live_source(row, live)
    if source_exact:
        # A plain detached float cannot prove the original decimal, even when
        # its string representation happens to equal the stored exact number.
        return source is not None and same_summary(source.tokens, saved)
    if source is None:
        # A detached row can prove direct equality. Superseded source metadata
        # cannot validate a different live row, even if an older parser would.
        return (not history or source_for(row) is None) and same_summary(live, saved)
    if same_summary(live, saved) or same_summary(source.tokens, saved):
        return True
    # Old records contain only the numerical tuple produced by the old parser.
    # Distinct source spellings that produced that same tuple are indistinguishable.
    return any(same_summary(candidate, saved) for candidate in source.parser_snapshots)


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
        if part.get('quality', 'recorded') not in {'recorded', 'estimated'}:
            raise DataError('Invalid acquisition-cost quality.')
        if 'source' in part and not isinstance(part['source'], str):
            raise DataError('Acquisition-cost source must be text.')
        if part.get('quality') == 'estimated' and (not part.get('source') or part.get('amount') == ''):
            raise DataError('Estimated acquisition cost requires an amount and source.')
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


def encode_components(parts, row, *, csv_values=False):
    validate_components(parts)
    summary, exact = stored_snapshot(row, csv_values=csv_values)
    document = dict(version=1, summary=summary, components=parts)
    if exact:
        document['source_exact'] = True
    return json.dumps(document, separators=(',', ':'), allow_nan=False)


def read_details(value):
    """Decode and validate a fresh, caller-owned cost document."""
    try:
        raw = json.loads(value)
        if not isinstance(raw, dict) or raw.get('version') != 1 or len(raw.get('summary', [])) != 3 or type(raw.get('source_exact', False)) is not bool:
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
        if matches_snapshot(row, raw['summary'], source_exact=raw.get('source_exact', False)):
            return raw['components']
        # External balance/cost edit: the summary supersedes old purchase evidence.
        return [aggregate_component(row)]
    if not row.get('balance_replaced_at') and row.get('purchase_history'):
        parts = _legacy_components(row)
        if parts is not None:
            return parts
    return [aggregate_component(row)]


def _legacy_components(row):
    from portfolio_app.portfolio.purchases import read_purchase_history
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
        if (previous is not None and matches_snapshot(row, previous)
                and sum(decimal(p['shares']) for p in parts) == decimal(previous[0])):
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
    exact_amount: Decimal | None = None
    reason_kind: str = 'transaction'


def uses_latest_fx_estimate(part, target):
    return part.get('conversions', {}).get(target, {}).get('method') == 'estimate'


def resolve_cost(parts, target, historical=None):
    total, estimated, notes = Decimal(0), False, []
    first_pending = None
    for part in parts:
        if part.get('quality') == 'estimated':
            estimated = True
            notes.append('Estimated acquisition cost: '+part['source'])
        amount = decimal(part['amount'])
        saved = part.get('conversions', {}).get(target)
        if saved:
            total += decimal(saved['amount'])
            estimated |= saved['method'] == 'estimate'
            notes.append('Estimated using latest FX' if saved['method'] == 'estimate' else
                         f"Historical FX ({saved.get('observed_on', '')})" if saved['method'] == 'historical' else 'Supplied conversion')
        elif amount is None:
            return first_pending or ResolvedCost(None, note='Missing average buy-in or purchase cost; excluded from gains')
        elif not part['currency']:
            return first_pending or ResolvedCost(None, note='Missing buy-in currency; excluded from gains')
        elif part['currency'] == target or amount == 0:
            total += amount
        elif part['date'] and historical is not None:
            try:
                conversion = historical.get(part['currency'], target, part['date'])
            except DataError:
                if first_pending is not None:
                    return first_pending
                raise
            if conversion.rate is None:
                issue = first_pending or ResolvedCost(None, note=conversion.note + ' Excluded from gains.', reason_kind='fx')
                if not getattr(conversion, 'pending', False):
                    return issue
                # Keep the first unresolved cost visible while discovering the remaining jobs.
                first_pending = issue
                continue
            total += amount * Decimal(str(conversion.rate))
            notes.append(f'Historical market FX ({conversion.observed_on})')
        else:
            return first_pending or ResolvedCost(None, note='Missing purchase date or converted cost; excluded from gains')
    if first_pending is not None:
        return first_pending
    value = float(total)
    return ResolvedCost(value if math.isfinite(value) else None, estimated,
                        '; '.join(dict.fromkeys(notes)) if math.isfinite(value) else 'Cost exceeds supported numeric range',
                        total if math.isfinite(value) else None)


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
