"""Original cost components and target-specific conversion evidence.

A component describes currently held quantity, not a tax lot or disposal ledger.
All persisted decimals remain strings. A summary fingerprint detects external edits.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, DecimalException, InvalidOperation, localcontext
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


def _csv_decimal(value):
    number = decimal(value)
    if number is None and isinstance(value, str) and value:
        from portfolio_app.portfolio.legacy_numeric import spaced_exponent
        recovered = spaced_exponent(value)
        if recovered is not None:
            return decimal(recovered[0])
    return number


def fingerprint(row):
    numbers = (decimal(row.get(key)) for key in ('shares', 'acquisition_price'))
    currency = row.get('acquisition_currency')
    return [str(value) if value is not None else '' for value in numbers] + [currency.strip().upper() if isinstance(currency, str) else '']


def same_summary(a, b, *, csv_source=False):
    """Exact decimal equality, including large integers and the absence of a price."""
    if len(a) != 3 or len(b) != 3 or a[2] != b[2]:
        return False
    read_source = _csv_decimal if csv_source else decimal
    for left, right in zip(a[:2], b[:2]):
        if left in ('', None) or right in ('', None):
            if left not in ('', None) or right not in ('', None):
                return False
        elif (number := read_source(left)) is None or number != decimal(right):
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
        return source if raw == source.tokens or same_summary(source.tokens, raw, csv_source=True) else None
    return source if same_summary(live, source.parsed) else None


def opening_cost_row(row):
    """Give editors and transactions exact Decimal-compatible starting amounts."""
    from portfolio_app.portfolio.csv_summary import CostRow

    summary, _ = stored_snapshot(row, csv_values=True)
    opening = CostRow(dict(row), _live_source(row, fingerprint(row)))
    for field, value in zip(('shares', 'acquisition_price'), summary[:2]):
        if field in opening:
            opening[field] = value
    return opening


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
    shares = _csv_decimal(quantity)
    unit_price = _csv_decimal(price)
    if shares is None or (price not in ('', None) and unit_price is None):
        # The shipped readers also accept certain non-Decimal spellings. The
        # loader's actual numeric values remain valid unmarked evidence.
        if source is not None:
            quantity, price, currency = source.parsed
            live = fingerprint(dict(shares=quantity, acquisition_price=price, acquisition_currency=currency))
        return live, False
    return [str(shares), str(unit_price) if unit_price is not None else '', currency], True


def matches_snapshot(row, saved, *, source_exact=False):
    live = fingerprint(row)
    source = _live_source(row, live)
    if source_exact:
        # A plain detached float cannot prove the original decimal, even when
        # its string representation happens to equal the stored exact number.
        return source is not None and same_summary(source.tokens, saved, csv_source=True)
    if source is None:
        # A detached row can prove direct equality. Superseded source metadata
        # cannot validate a different live row, even if an older parser would.
        return same_summary(live, saved)
    if same_summary(live, saved) or same_summary(source.tokens, saved, csv_source=True):
        return True
    # Old records contain only the numerical tuple produced by the old parser.
    # Distinct source spellings that produced that same tuple are indistinguishable.
    return any(same_summary(candidate, saved) for candidate in source.parser_snapshots)


def component(shares, amount, currency, day='', conversions=None):
    return dict(shares=str(shares), amount='' if amount is None else str(amount), currency=currency or '',
                date=day or '', conversions=conversions or {})


def _exact_product(quantity, price):
    with localcontext() as context:
        context.prec = max(context.prec, len(quantity.as_tuple().digits) + len(price.as_tuple().digits))
        return quantity * price


def aggregate_component(row):
    quantity, price = decimal(row.get('shares')), decimal(row.get('acquisition_price'))
    amount = _exact_product(quantity, price) if quantity is not None and price is not None else None
    return component(quantity or 0, amount, row.get('acquisition_currency', ''))


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


class ReconciledPurchases(list):
    """Original purchase components and the balance confirmed for their prefix."""
    def __init__(self, parts, count, balance):
        super().__init__(parts)
        self.count_at_reconciliation = count
        self.confirmed_balance = balance


def _read_reconciliation(parts, record):
    count, balance = record['component_count'], decimal(record['confirmed_shares'])
    if type(count) is not int or not 1 <= count <= len(parts) or balance is None or balance <= 0:
        raise ValueError('Invalid purchase reconciliation.')
    with localcontext() as context:
        context.prec = 50
        purchased = sum((decimal(part['shares']) for part in parts[:count]), Decimal(0))
        # Only this explicitly confirmed prefix used the v1 writer's allowance.
        if abs(purchased - balance) > min(Decimal('1e-9'), balance * Decimal('1e-9')):
            raise ValueError('Purchase records exceed the confirmed balance allowance.')
        total = balance + sum((decimal(part['shares']) for part in parts[count:]), Decimal(0))
    return ReconciledPurchases(parts, count, balance), total


def encode_components(parts, row, *, csv_values=False):
    validate_components(parts)
    summary, exact = stored_snapshot(row, csv_values=csv_values)
    document = dict(version=1, summary=summary, components=parts)
    if isinstance(parts, ReconciledPurchases):
        document['legacy_reconciliation'] = dict(component_count=parts.count_at_reconciliation,
                                                  confirmed_shares=str(parts.confirmed_balance))
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
        if 'legacy_reconciliation' in raw:
            raw['components'], actual = _read_reconciliation(raw['components'], raw['legacy_reconciliation'])
        else:
            actual = sum(decimal(part['shares']) for part in raw['components'])
        if quantity is None or abs(actual - quantity) > max(Decimal('1e-12'), quantity * Decimal('1e-12')):
            raise ValueError
        return raw
    except (ValueError, TypeError, KeyError, DecimalException) as exc:
        raise DataError('Stored cost details are invalid. Restore them from a backup.') from exc


def active_components(row):
    value = row.get(FIELD, '')
    if isinstance(value, str) and value:
        raw = read_details(value)
        if matches_snapshot(row, raw['summary'], source_exact=raw.get('source_exact', False)):
            return _document_components(row, raw)
        # External balance/cost edit: the summary supersedes old purchase evidence.
        return [aggregate_component(row)]
    if not row.get('balance_replaced_at') and row.get('purchase_history'):
        parts = _legacy_components(row)
        if parts is not None:
            return parts
    return [aggregate_component(row)]


def _document_components(row, document):
    parts = document['components']
    if isinstance(parts, ReconciledPurchases):
        return parts
    if document.get('source_exact'):
        # A reviewed aggregate can coexist with older, superseded purchase batches.
        aggregate = _csv_aggregate_costs(row, document)
        if aggregate is not None:
            if row.get('purchase_history'):
                purchases = _legacy_components(row, document=document)
                if purchases is not None:
                    return purchases
            return aggregate
        if not row.get('purchase_history'):
            return parts
    source = _live_source(row, fingerprint(row))
    if source is None:
        return parts
    quantity = _csv_decimal(source.tokens[0])
    if quantity is None:
        return parts
    from portfolio_app.ledger.security_history import _exact_sum, allocate_opening_units
    recorded = decimal(parts[0]['shares']) if len(parts) == 1 else _exact_sum(decimal(part['shares']) for part in parts)
    if row.get('purchase_history'):
        reviewed_fx = not document.get('source_exact') and any(
            fx['method'] == 'estimate' for part in parts for fx in part.get('conversions', {}).values())
        weights_or_source_differ = recorded != quantity or (
            not document.get('source_exact') and not same_summary(source.tokens, document['summary'], csv_source=True))
        if weights_or_source_differ or reviewed_fx:
            # A currency review could rewrite even a dated batch's exact summary
            # with parsed floats. Its final batch still identifies the original CSV.
            recovered = _legacy_components(row, document=document, reviewed_fx=reviewed_fx,
                                           skip_matching_result=not weights_or_source_differ)
            if recovered is not None:
                return recovered
    if recorded == quantity:
        return parts
    # Former currency edits wrote the parsed aggregate and its component
    # weights. Bind those weights only to a proven, complete parser output.
    if (not document.get('source_exact') and recorded > 0 and quantity > 0
            and recorded == decimal(document['summary'][0])
            and _aggregate_amount_matches(parts, document['summary'])
            and (same_summary(source.parsed, document['summary'])
                 or any(same_summary(candidate, document['summary']) for candidate in source.parser_snapshots))):
        return allocate_opening_units(parts, len(parts), quantity)
    return parts


def _csv_aggregate_costs(row, document):
    """Former currency reviews and later edits retained the parsed aggregate's units and cost."""
    parts = document['components']
    if len(parts) != 1:
        return None
    part, = parts
    if (set(part) != {'shares', 'amount', 'currency', 'date', 'conversions'}
            or not part['amount']):
        return None
    units, price, currency = document['summary']
    if not price or not currency or part['currency'] != currency:
        return None
    exact_quantity, recorded_quantity = decimal(units), decimal(part['shares'])
    amount, exact_amount = decimal(part['amount']), _exact_product(exact_quantity, decimal(price))
    if not amount or (recorded_quantity, amount) == (exact_quantity, exact_amount):
        return None
    # matches_snapshot has already bound this marked document to the unchanged source.
    source = source_for(row)
    from portfolio_app.market.prices import parse_time
    from portfolio_app.portfolio.portfolio_settings import REPORTING_CURRENCIES
    with localcontext() as context:
        context.prec = 28  # The amount accepted at review used this Decimal context.
        writer_fields = {
            'estimate': {'amount', 'method', 'rate', 'observed_on', 'saved_at'},
            'historical': {'amount', 'method', 'rate', 'observed_on', 'source'},
            'supplied_rate': {'amount', 'method', 'rate', 'saved_at'},
            'supplied_cost': {'amount', 'method', 'saved_at'},
        }
        for target, fx in part['conversions'].items():
            method = fx['method']
            if (target not in REPORTING_CURRENCIES or target == currency
                    or set(fx) != writer_fields.get(method)):
                return None
            # A supplied total is the user's independent input. Rate-derived
            # amounts must match the writer's product in the recorded units.
            if method != 'supplied_cost' and decimal(fx['amount']) != amount * decimal(fx['rate']):
                return None
            try:
                if method == 'historical':
                    # HistoricalFX accepts the purchase day or its latest observation
                    # within the preceding seven days.
                    day = date.fromisoformat(part['date'])
                    observed = date.fromisoformat(fx['observed_on'])
                    if (not isinstance(fx['source'], str) or not fx['source'].strip()
                            or not 0 <= (day - observed).days <= 7):
                        return None
                else:
                    parse_time(fx['saved_at'])
                    if method == 'estimate':
                        parse_time(fx['observed_on'])
            except (TypeError, ValueError, OverflowError):
                return None

        def matches_parsed(candidate):
            quantity, unit_price, denomination = candidate
            parsed_price = decimal(unit_price)
            # Candidate releases used both 28-digit and exact native products.
            return (denomination == currency and recorded_quantity == decimal(quantity)
                    and parsed_price is not None and amount in
                    (recorded_quantity * parsed_price, _exact_product(recorded_quantity, parsed_price)))

        if matches_parsed(source.parsed) or any(matches_parsed(c) for c in source.parser_snapshots):
            # Keep every confirmed target amount, quote, and timestamp verbatim.
            return [{**part, 'shares': str(exact_quantity), 'amount': str(exact_amount)}]
    return None


def _aggregate_amount_matches(parts, summary):
    units, price, currency = summary
    if not currency or any(part['currency'] != currency for part in parts):
        return False
    if price == '':
        return all(part['amount'] == '' for part in parts)
    if any(part['amount'] == '' for part in parts):
        return False
    from portfolio_app.ledger.security_history import _exact_sum
    with localcontext() as context:
        context.prec = 28  # Stored aggregates used the ordinary Decimal writer.
        expected = decimal(units) * decimal(price)
    return _exact_sum(decimal(part['amount']) for part in parts) == expected


def same_purchase_costs(expected, saved, rates):
    if len(expected) != len(saved):
        return False
    for original, part, rate in zip(expected, saved, rates, strict=True):
        if (decimal(original['shares']) != decimal(part['shares'])
                or decimal(original['amount']) != decimal(part['amount'])
                or (original['currency'], original['date']) != (part['currency'], part['date'])):
            return False
        if rate:
            with localcontext() as context:
                context.prec = 28  # Original v2 conversion writer's Decimal precision.
                amount = decimal(original['amount'])
                converted = None if amount is None else amount * decimal(rate)
            if not any(c['method'] == 'supplied_rate' and decimal(c.get('rate')) == decimal(rate)
                       and decimal(c['amount']) == converted for c in part.get('conversions', {}).values()):
                return False
    return True


def _batch_snapshot(batch):
    average = batch['result_average']
    return (batch['result_shares'], average, batch['currency'] if average else '')


def _review_parser_matches(row, previous, summary):
    from portfolio_app.portfolio.legacy_numeric import legacy_fingerprint

    source = source_for(row)
    for fused in (False, True):
        candidate = legacy_fingerprint(*previous, integer_shares=source.integer_shares,
                                       integer_price=source.integer_price, fused_digits=fused)
        if candidate is not None and same_summary(candidate, summary):
            return True
    return False


def _legacy_components(row, *, document=None, reviewed_fx=False, skip_matching_result=False):
    from portfolio_app.portfolio.purchases import Purchase, read_purchase_history, summarize_purchases
    try:
        batches = read_purchase_history(row['purchase_history'])
        if skip_matching_result and batches and matches_snapshot(row, _batch_snapshot(batches[-1]), source_exact=True):
            return None
        parts, rates, previous = [], [], None
        with localcontext() as context:
            # Serialized purchase results used the writer's 50-digit arithmetic.
            context.prec = 50
            for batch in batches:
                opening = batch.get('opening', {})
                if not isinstance(opening, dict):
                    return None
                mode, currency = batch['mode'], batch['currency']
                if mode == 'reconcile':
                    parts, rates = [], []
                    if not opening:
                        # Without a recorded opening, the lines must account for
                        # the entire balance exactly; no allowance can be inferred.
                        mode = 'add'
                elif previous is not None and not same_summary(fingerprint(opening), previous):
                    return None
                elif previous is None and decimal(opening.get('shares', '0')):
                    parts = [aggregate_component(opening)]
                    rates = [None]

                purchases = []
                for item in batch['purchases']:
                    shares, price, fees = (decimal(item[k]) for k in ('shares', 'price', 'fees'))
                    if not shares or fees is None or (item['price'] and price is None):
                        return None
                    purchases.append(Purchase(item['date'], shares, price, fees))
                    with localcontext() as cost_context:
                        if document is not None and batch['version'] == 2:
                            cost_context.prec = 28  # v2 stored monetary components outside the summary's 50-digit context.
                        amount = None if price is None else shares * price + fees
                    parts.append(component(shares, amount, currency, item['date']))
                    rates.append(item.get('fx_rate'))
                # v1 refused additions across currencies when an opening cost was known.
                if (batch['version'] == 1 and mode == 'add' and decimal(opening.get('shares'))
                        and opening.get('acquisition_price') and opening.get('acquisition_currency', '').upper() != currency):
                    return None
                result = summarize_purchases(purchases, currency, opening, mode=mode)
                previous = (str(result.total_shares), '' if result.average is None else str(result.average),
                            result.currency if result.average is not None else '')
                if not same_summary(previous, _batch_snapshot(batch)):
                    return None
                if mode == 'reconcile' and result.batch_shares != result.total_shares:
                    parts = ReconciledPurchases(parts, len(parts), result.total_shares)

        if document is not None:
            saved = document['components']
            if previous is None or not same_purchase_costs(parts, saved, rates):
                return None
            if not matches_snapshot(row, previous, source_exact=True):
                if not (same_summary(previous, document['summary']) or
                        reviewed_fx and _review_parser_matches(row, previous, document['summary'])):
                    return None
                # Verified purchase evidence identifies the original source even
                # when an external edit collides with its parsed float.
                return [aggregate_component(opening_cost_row(row))]
            if isinstance(parts, ReconciledPurchases):
                return ReconciledPurchases(saved, parts.count_at_reconciliation, parts.confirmed_balance)
            return None

        # The batch writer saved these exact result decimals to the CSV. A
        # matching rounded float cannot disprove a subsequent source edit.
        if previous is not None:
            if source_for(row) is None:
                # Existing API callers can also supply a detached numeric row.
                # In that form only direct decimal equality is evidence.
                if same_summary(fingerprint(row), previous):
                    return parts
            elif matches_snapshot(row, previous, source_exact=True):
                return parts
    except (DataError, DecimalException, KeyError, TypeError, AttributeError):
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
