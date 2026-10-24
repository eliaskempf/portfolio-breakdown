"""Optional securities evidence and Decimal FIFO replay, independent of the UI.

Records are retained once. Only explicitly selected records affect balances;
review receipts retain previous selections. An opening is a boundary, not a buy.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from decimal import Decimal, localcontext
from hashlib import sha256
import json

from portfolio_app.derivative_types import decimal, identifier, iso_date
from portfolio_app.holdings import DataError

ZERO = Decimal(0)
KINDS = {'opening', 'buy', 'sell', 'distribution', 'split', 'transfer', 'fee', 'tax', 'cost', 'note', 'void', 'transfer_in', 'transfer_out'}


def _exact_sum(values):
    """Sum finite recorded decimals without rounding a quantity/remainder."""
    values = tuple(values)
    if not values:
        return ZERO
    with localcontext() as context:
        context.prec = max(context.prec,
            max(value.adjusted() for value in values)
            - min(value.as_tuple().exponent for value in values) + len(str(len(values))) + 2)
        return sum(values, ZERO)


def _difference(total, part):
    return _exact_sum((total, part.copy_negate()))


def number(value, label, *, optional=False, positive=False):
    if value is None and optional:
        return None
    result = decimal(value, label)
    if result < 0 or (positive and result == 0):
        raise DataError(f'{label} must be {"positive" if positive else "nonnegative"}.')
    return result


def day(value, *, optional=False):
    iso_date(value, optional=optional)
    if value and value > date.today().isoformat():
        raise DataError('History cannot be future dated.')
    return value


def denomination(value, *, optional=False):
    import re
    if optional and value == '':
        return
    if not isinstance(value, str) or not re.fullmatch('[A-Z]{3}', value):
        raise DataError('Use a three-letter currency.')


@dataclass(frozen=True)
class Charge:
    amount: Decimal | None
    currency: str
    date: str = ''
    kind: str = 'fee'
    external: bool = False

    def __post_init__(self):
        object.__setattr__(self, 'amount', number(self.amount, 'Charge', optional=True))
        denomination(self.currency); day(self.date, optional=True)
        if self.kind not in ('fee', 'tax') or type(self.external) is not bool:
            raise DataError('Invalid charge kind or funding source.')


@dataclass(frozen=True)
class SecurityLot:
    id: str
    quantity: Decimal
    costs: tuple[dict, ...] = ()
    exact: bool = True
    date: str = ''

    def __post_init__(self):
        from portfolio_app.cost_basis import validate_components
        identifier(self.id)
        object.__setattr__(self, 'quantity', number(self.quantity, 'Lot quantity', positive=True))
        object.__setattr__(self, 'costs', tuple(deepcopy(self.costs)))
        if self.costs:
            validate_components(list(self.costs))
            if _exact_sum(Decimal(p['shares']) for p in self.costs) != self.quantity:
                raise DataError('Purchase cost quantities do not match the recorded shares.')
        day(self.date, optional=True)
        if type(self.exact) is not bool:
            raise DataError('Invalid lot certainty.')


@dataclass(frozen=True)
class SecurityEvent:
    id: str
    kind: str
    date: str = ''
    sequence: int = 0
    quantity: Decimal = ZERO
    price: Decimal | None = None
    amount: Decimal | None = None
    currency: str = ''
    charges: tuple[Charge, ...] = ()
    charges_known: bool = False
    lots: tuple[SecurityLot, ...] = ()
    costs: tuple[dict, ...] = ()
    other_position_id: str = ''
    value_date: str = ''
    replaces: str = ''
    source: str = 'manual'
    note: str = ''

    def __post_init__(self):
        identifier(self.id); day(self.date, optional=True); iso_date(self.value_date, optional=True)
        if self.kind not in KINDS or type(self.sequence) is not int or self.sequence < 0:
            raise DataError('Invalid security event kind or order.')
        for name in ('quantity', 'price', 'amount'):
            object.__setattr__(self, name, number(getattr(self, name), name, optional=name != 'quantity'))
        denomination(self.currency, optional=True)
        for name, cls in (('charges', Charge), ('lots', SecurityLot)):
            values = tuple(getattr(self, name))
            if any(not isinstance(item, cls) for item in values):
                raise DataError(f'Invalid {name}.')
            object.__setattr__(self, name, values)
        if type(self.charges_known) is not bool:
            raise DataError('Confirm whether charges are complete.')
        if self.value_date and (not self.date or self.value_date < self.date):
            raise DataError('Settlement date must be on or after the trade date.')
        if self.price is not None and self.amount is not None and self.kind in ('buy', 'sell'):
            if self.quantity * self.price != self.amount:
                raise DataError('Gross total must equal quantity times price; enter fees separately.')
        if self.costs:
            from portfolio_app.cost_basis import validate_components
            validate_components(list(self.costs))
        object.__setattr__(self, 'costs', tuple(deepcopy(self.costs)))
        for name in ('other_position_id', 'replaces', 'source', 'note'):
            if not isinstance(getattr(self, name), str):
                raise DataError(f'{name} must be text.')

    @property
    def gross(self):
        return self.amount if self.amount is not None else self.quantity * self.price if self.price is not None else None


@dataclass(frozen=True)
class HistoryReview:
    id: str
    active_ids: tuple[str, ...]
    date: str
    resolution: str
    complete_from: str = ''
    complete_through: str = ''
    facts_fingerprint: str = ''

    def __post_init__(self):
        identifier(self.id); day(self.date)
        day(self.complete_from, optional=True); day(self.complete_through, optional=True)
        object.__setattr__(self, 'active_ids', tuple(self.active_ids))
        if self.resolution not in ('accept', 'boundary', 'record'):
            raise DataError('Unknown history resolution.')
        if bool(self.complete_from) != bool(self.complete_through) or self.complete_from > self.complete_through:
            raise DataError('Completeness requires a valid reviewed interval.')


@dataclass(frozen=True)
class SecurityBook:
    position_id: str
    instrument_id: str
    source_summary: tuple[str, ...]
    account_id: str = ''
    records: tuple[SecurityEvent, ...] = ()
    active_ids: tuple[str, ...] = ()
    reviews: tuple[HistoryReview, ...] = ()
    cash_from: str = ''

    def __post_init__(self):
        identifier(self.position_id); identifier(self.instrument_id)
        object.__setattr__(self, 'source_summary', tuple(self.source_summary))
        if len(self.source_summary) != 3 or any(not isinstance(x, str) for x in self.source_summary):
            raise DataError('History requires the original holding fingerprint.')
        for name, cls in (('records', SecurityEvent), ('reviews', HistoryReview)):
            values = tuple(getattr(self, name))
            if any(not isinstance(item, cls) for item in values):
                raise DataError(f'Invalid history {name}.')
            object.__setattr__(self, name, values)
        object.__setattr__(self, 'active_ids', tuple(self.active_ids))
        ids = [e.id for e in self.records]
        if len(set(ids)) != len(ids) or len(set(self.active_ids)) != len(self.active_ids) or set(self.active_ids) - set(ids):
            raise DataError('Duplicate or missing security record identity.')
        seen = set()
        for event in self.records:
            if event.replaces and event.replaces not in seen:
                raise DataError('Correction must refer to an earlier retained record.')
            seen.add(event.id)
        if any(set(r.active_ids) - set(ids) for r in self.reviews):
            raise DataError('Review refers to missing evidence.')
        day(self.cash_from, optional=True)
        if self.account_id:
            identifier(self.account_id)

    @property
    def active(self):
        return bool(self.active_ids)

    @property
    def interval(self):
        if self.reviews and self.reviews[-1].active_ids == self.active_ids:
            r = self.reviews[-1]
            return r.complete_from, r.complete_through
        return '', ''


def book_from_dict(raw):
    fields = dict(raw)
    fields['records'] = tuple(event_from_dict(e) for e in raw.get('records', ()))
    fields['reviews'] = tuple(HistoryReview(**r) for r in raw.get('reviews', ()))
    return SecurityBook(**fields)


def event_from_dict(raw):
    fields = dict(raw)
    fields['charges'] = tuple(Charge(**c) for c in raw.get('charges', ()))
    fields['lots'] = tuple(SecurityLot(**lot) for lot in raw.get('lots', ()))
    return SecurityEvent(**fields)


def current_records(book):
    """Latest retained versions, excluding removed entries and internal boundaries."""
    replaced = {event.replaces for event in book.records if event.replaces}
    return tuple(event for event in book.records if event.id not in replaced
                 and event.kind != 'void' and event.source != 'confirmed_zero_start')


def selected_events(book):
    selected = set(book.active_ids)
    records = [e for e in book.records if e.id in selected]
    replaced = {e.replaces for e in records if e.replaces}
    if replaced.intersection(selected):
        raise DataError('Select a correction or its original, not both.')
    # Python's stable sort retains the original record order for ties. Looking
    # each event up in the full tuple made every historical replay quadratic.
    return tuple(sorted((e for e in records if e.kind != 'void'), key=lambda e: (e.date, e.sequence)))


def append_evidence(book, records):
    known = {e.id: e for e in book.records}
    result = list(book.records)
    for record in records:
        if record.id in known:
            if record != known[record.id]:
                raise DataError('Record identity already has different facts.')
            continue
        result.append(record); known[record.id] = record
    return replace(book, records=tuple(result))


@dataclass(frozen=True)
class SecurityBalance:
    quantity: Decimal = ZERO
    lots: tuple[SecurityLot, ...] = ()
    boundary: str = ''
    issues: tuple[str, ...] = ()

    @property
    def costs(self):
        return tuple(p for lot in self.lots for p in lot.costs)


@dataclass(frozen=True)
class SecurityCash:
    id: str
    position_id: str
    account_id: str
    date: str
    sequence: int
    currency: str
    amount: Decimal | None
    kind: str
    value_date: str = ''
    external: bool = False


@dataclass(frozen=True)
class SecuritiesState:
    positions: dict[str, SecurityBalance]
    cash: tuple[SecurityCash, ...]
    disposals: tuple['SecurityDisposal', ...] = ()


@dataclass(frozen=True)
class SecurityDisposal:
    event_id: str
    position_id: str
    date: str
    kind: str
    lots: tuple[SecurityLot, ...]


def _proportion(amount, quantity, total):
    # Multiply exactly before dividing. Computing quantity / total first loses
    # exact results such as 30 * (1 / 3). Only a nonterminating quotient rounds.
    with localcontext() as context:
        context.prec = max(context.prec, len(amount.as_tuple().digits) + len(quantity.as_tuple().digits))
        product = amount * quantity
    return product / total


def _partition_lot(lot, quantity):
    """Split once; derive the remainder instead of independently scaling it."""
    if quantity == lot.quantity:
        return lot, None
    taken, remaining = deepcopy(lot.costs), deepcopy(lot.costs)
    positive = [i for i, part in enumerate(lot.costs) if Decimal(part['shares'])]
    assigned = ZERO
    for i, (source, used, rest) in enumerate(zip(lot.costs, taken, remaining)):
        units = Decimal(source['shares'])
        # Fees carry zero shares. Give the last quantity-bearing component the
        # exact remainder so component shares total the requested disposal.
        used_units = (_difference(quantity, assigned) if i == positive[-1]
                      else _proportion(units, quantity, lot.quantity)) if units else ZERO
        assigned = _exact_sum((assigned, used_units))
        used['shares'], rest['shares'] = str(used_units), str(_difference(units, used_units))
        if not lot.exact:
            # A partial sale of an aggregate opening still cannot invent FIFO
            # costs or converted costs that were never supplied per lot.
            used['amount'] = rest['amount'] = ''
            used['conversions'], rest['conversions'] = {}, {}
            continue
        if source['amount'] != '':
            amount = Decimal(source['amount'])
            part = _proportion(amount, quantity, lot.quantity)
            used['amount'], rest['amount'] = str(part), str(_difference(amount, part))
        for currency, conversion in source.get('conversions', {}).items():
            amount = Decimal(conversion['amount'])
            part = _proportion(amount, quantity, lot.quantity)
            used['conversions'][currency]['amount'] = str(part)
            rest['conversions'][currency]['amount'] = str(_difference(amount, part))
    return (replace(lot, quantity=quantity, costs=taken),
            replace(lot, quantity=_difference(lot.quantity, quantity), costs=remaining))


def _consume(lots, quantity):
    remaining, moved = list(lots), []
    while quantity:
        if not remaining:
            raise DataError('Sale or transfer exceeds the recorded quantity. Supply missing history.')
        lot = remaining.pop(0)
        take = min(quantity, lot.quantity)
        used, rest = _partition_lot(lot, take)
        moved.append(used)
        if rest is not None:
            remaining.insert(0, rest)
        quantity -= take
    return tuple(remaining), tuple(moved)


def replay_securities(books, *, as_of=None, sequence=None):
    """Replay activated facts; unknown money never substitutes for zero."""
    from portfolio_app.cost_basis import component
    books = tuple(books)
    owners = {b.position_id: b for b in books}
    states = {b.position_id: SecurityBalance() for b in books if b.active}
    events = [(b, e) for b in books for e in selected_events(b)]
    events.sort(key=lambda pair: (pair[1].date, pair[1].sequence, pair[0].position_id))
    cash, disposals = [], []
    with localcontext() as context:
        context.prec = 50
        for book, e in events:
            if not e.date:
                raise DataError('An undated record can be saved as evidence but cannot be activated.')
            if as_of and (e.date > as_of or (e.date == as_of and sequence is not None and e.sequence > sequence)):
                continue
            state = states[book.position_id]
            if e.kind != 'opening' and not state.boundary:
                raise DataError('Start history with an explicit opening snapshot, including zero.')
            lots, issues = state.lots, list(state.issues)
            if e.kind == 'opening':
                if not e.quantity and e.amount:
                    raise DataError('Positive acquisition cost requires positive opening quantity.')
                if e.lots:
                    if sum((lot.quantity for lot in e.lots), ZERO) != e.quantity:
                        raise DataError('Opening lots must total the snapshot quantity.')
                    if any(not lot.date or lot.date > e.date for lot in e.lots):
                        raise DataError('Opening lots cannot be acquired after the snapshot.')
                    lots = tuple(sorted(e.lots, key=lambda lot: lot.date))
                else:
                    costs = e.costs or (component(e.quantity, e.amount, e.currency),)
                    lots = (SecurityLot(e.id, e.quantity, costs, False),) if e.quantity else ()
                states[book.position_id] = SecurityBalance(e.quantity, lots, e.date)
                continue
            if e.kind in ('buy', 'sell', 'split', 'transfer', 'transfer_in', 'transfer_out') and not e.quantity:
                raise DataError('Trade, split and transfer quantities must be positive.')
            if e.kind == 'transfer_in':
                if e.lots:
                    if sum((lot.quantity for lot in e.lots), ZERO) != e.quantity:
                        raise DataError('Transferred lots must equal transferred units.')
                    incoming = e.lots
                else:
                    incoming = (SecurityLot(e.id, e.quantity, e.costs or (component(e.quantity, None, e.currency),), False),)
                lots = tuple(sorted((*lots, *incoming), key=lambda lot: lot.date))
            elif e.kind == 'buy':
                costs = [component(e.quantity, e.gross, e.currency, e.date)]
                # Cost components partition quantity. Additional charges carry zero units.
                costs.extend(component(0, c.amount, c.currency, c.date or e.date) for c in e.charges if c.kind == 'fee')
                if not e.charges_known:
                    costs.append(component(0, None, e.currency, e.date))
                lots = (*lots, SecurityLot(e.id, e.quantity, tuple(costs), True, e.date))
            elif e.kind in ('sell', 'transfer', 'transfer_out'):
                lots, moved = _consume(lots, e.quantity)
                disposals.append(SecurityDisposal(e.id, book.position_id, e.date, e.kind, moved))
                if e.kind == 'transfer':
                    destination = owners.get(e.other_position_id)
                    other = states.get(e.other_position_id)
                    if destination is None or other is None or not other.boundary or destination.instrument_id != book.instrument_id or destination == book:
                        raise DataError('A transfer needs another activated holding of the same instrument with an opening snapshot.')
                    states[destination.position_id] = replace(other, quantity=other.quantity+e.quantity,
                        lots=tuple(sorted((*other.lots, *moved), key=lambda lot: lot.date)))
            elif e.kind == 'split':
                lots = tuple(replace(lot, quantity=lot.quantity * e.quantity,
                    costs=tuple({**deepcopy(p), 'shares': str(Decimal(p['shares']) * e.quantity)} for p in lot.costs)) for lot in lots)
            elif e.kind == 'cost':
                if not state.quantity and e.amount:
                    raise DataError('Positive remaining cost requires a positive holding.')
                costs = e.costs or (component(state.quantity, e.amount, e.currency),)
                lots = (SecurityLot(e.id, state.quantity, costs, False),) if state.quantity else ()
            elif e.kind == 'note':
                issues.append(e.note or 'Unresolved historical event')
            quantity = sum((lot.quantity for lot in lots), ZERO)
            states[book.position_id] = replace(state, quantity=quantity, lots=tuple(lots), issues=tuple(issues))
            if e.kind in ('buy', 'sell', 'distribution', 'fee', 'tax'):
                denomination(e.currency)
                gross = e.gross if e.kind in ('buy', 'sell') else e.amount
                sign = -1 if e.kind in ('buy', 'fee', 'tax') else 1
                cash.append(SecurityCash(e.id, book.position_id, book.account_id, e.date, e.sequence,
                    e.currency, None if gross is None else sign*gross, e.kind, e.value_date))
            for i, c in enumerate(e.charges):
                cash.append(SecurityCash(f'{e.id}:charge:{i}', book.position_id, book.account_id,
                    c.date or e.date, e.sequence, c.currency, None if c.amount is None else -c.amount,
                    c.kind, external=c.external))
            if e.kind in ('buy', 'sell', 'distribution') and not e.charges_known:
                cash.append(SecurityCash(f'{e.id}:unknown-charges', book.position_id, book.account_id,
                    e.date, e.sequence, e.currency, None, 'fee'))
    if as_of:
        cash = [c for c in cash if c.date <= as_of]
    return SecuritiesState(states, tuple(cash), tuple(disposals))


@dataclass(frozen=True)
class Reconciliation:
    recorded_quantity: Decimal
    reconstructed_quantity: Decimal
    recorded_cost: Decimal | None
    reconstructed_cost: Decimal | None
    currency: str
    quantity_matches: bool
    cost_matches: bool | None


def reconcile(book, row, *, other_books=(), target='EUR', historical=None):
    from portfolio_app.cost_basis import active_components, resolve_cost
    state = replay_securities((*other_books, book)).positions[book.position_id]
    original = Decimal(str(row['shares']))
    tolerance = min(Decimal('1e-9'), abs(original)*Decimal('1e-9'))
    old = resolve_cost(active_components(row), target, historical)
    new = resolve_cost(list(state.costs), target, historical) if state.costs else None
    old_cost = Decimal(str(old.amount)) if old.amount is not None else None
    new_cost = Decimal(str(new.amount)) if new and new.amount is not None else ZERO if not state.quantity else None
    return Reconciliation(original, state.quantity, old_cost, new_cost, target,
        abs(state.quantity-original) <= tolerance,
        abs(old_cost-new_cost) <= Decimal('.01') if old_cost is not None and new_cost is not None else None)


def validate_books(books, accounts):
    event_ids = [e.id for b in books for e in b.records]
    if len(event_ids) != len(set(event_ids)):
        raise DataError('Security event IDs must be unique across positions.')
    ids = [b.position_id for b in books]
    if len(ids) != len(set(ids)):
        raise DataError('A holding can have only one history owner.')
    for book in books:
        if book.account_id and book.account_id not in accounts:
            raise DataError('Security history refers to an unknown account.')
    replay_securities(books)


def effective_holdings(holdings, books):
    """Materialize managed balances without writing competing CSV summaries."""
    from portfolio_app.cost_basis import fingerprint, same_summary, encode_components, component
    result = holdings.copy(deep=True)
    states = replay_securities(books).positions
    for book in books:
        matches = result.index[result.position_id == book.position_id]
        if not len(matches) and (not book.active or not states[book.position_id].quantity):
            # Retained evidence and fully closed histories still contribute to
            # performance without manufacturing another current holding row.
            continue
        if len(matches) != 1:
            raise DataError('A history-linked holding is missing. Restore or reconcile its identity.')
        index = matches[0]
        row = result.loc[index]
        if not book.active:
            continue
        if row.id != book.instrument_id or not same_summary(fingerprint(row), book.source_summary):
            raise DataError('A history-linked holding changed outside History. Restore its source snapshot before continuing.')
        state = states[book.position_id]
        costs = state.costs or (component(0, 0, ''),)
        currencies = {p['currency'] for p in costs if p['amount'] != '0'}
        known = len(currencies) == 1 and all(p['amount'] != '' for p in costs)
        amount = sum((Decimal(p['amount']) for p in costs), ZERO) if known else None
        values = {'shares': float(state.quantity), 'acquisition_price': float(amount/state.quantity) if amount is not None and state.quantity else float('nan'),
            'acquisition_currency': next(iter(currencies)) if known else ''}
        for key, value in values.items():
            result.at[index, key] = value
        result.at[index, 'cost_basis_details'] = encode_components(list(costs), {**row.to_dict(), **values})
    return result


def assert_security_editable(path, position_ids, fields=None):
    from portfolio_app.account_journal import load_journal
    if fields is not None and not set(fields).intersection({'shares', 'acquisition_price', 'acquisition_currency', 'cost_basis_details', 'purchase_history'}):
        return
    books = load_journal(path.parent)[0].securities
    if set(position_ids).intersection(b.position_id for b in books if b.active):
        raise DataError('This holding is maintained in History. Use Fix history or Replace balance there.')


def history_fingerprint(journal):
    from portfolio_app.account_journal import journal_to_dict
    return sha256(json.dumps(journal_to_dict(journal), default=str, sort_keys=True).encode()).hexdigest()


def cash_postings(journal, *, security_cash=None):
    """Derived postings are never persisted as duplicate transactions.

    An existing cash opening already contains earlier payments. Its end-of-day
    boundary excludes earlier securities postings, not the retained evidence.
    """
    from portfolio_app.account_journal import Event, effective_events
    openings = {(e.account_id, e.currency): e.date for e in effective_events(journal) if e.kind == 'cash_open'}
    result = []
    roots = {}
    for book in journal.securities:
        for event in book.records:
            roots[event.id] = roots.get(event.replaces, event.id)
    def posting_id(identity):
        # Find the longest event-ID prefix without scanning every retained record.
        # IDs may themselves contain colons (including imported statement IDs).
        key = identity
        while key not in roots and ':' in key:
            key = key.rsplit(':', 1)[0]
        key = key if key in roots else identity
        return 'security:'+roots.get(key, key)+identity[len(key):]
    if security_cash is None:
        security_cash = replay_securities(journal.securities).cash
    for c in security_cash:
        if not c.account_id or c.external:
            continue
        opening = openings.get((c.account_id, c.currency))
        if not opening:
            raise DataError(f'Supply an opening {c.currency} cash balance before activating linked transactions.')
        if c.date <= opening and (not c.value_date or c.value_date == c.date):
            continue
        result.append(Event(id=posting_id(c.id), date=c.date, sequence=c.sequence,
            kind='security_unknown' if c.amount is None else 'security_cash', account_id=c.account_id,
            currency=c.currency, amount=c.amount or ZERO, holding_id=c.position_id,
            source='security_history', note=c.kind, value_date=c.value_date))
    return tuple(result)


@dataclass(frozen=True)
class CapitalAllocation:
    id: str
    investment_id: str
    date: str
    amount: Decimal
    replaces: str = ''

    def __post_init__(self):
        identifier(self.id); identifier(self.investment_id); day(self.date)
        object.__setattr__(self, 'amount', number(self.amount, 'Allocated capital'))


def allocated_capital_at(journal, investment_id, stamp):
    replaced = {e.replaces for e in journal.capital_history if e.replaces}
    eligible = [e for e in journal.capital_history if e.investment_id == investment_id and e.date <= stamp and e.id not in replaced]
    return max(eligible, key=lambda e: (e.date, journal.capital_history.index(e))).amount if eligible else None


def duplicate_candidates(book):
    """Economic matches need acknowledgment, not automatic removal."""
    from dataclasses import asdict
    seen, duplicates = {}, []
    for event in selected_events(book):
        if event.kind not in ('buy', 'sell', 'distribution', 'fee', 'tax', 'transfer', 'transfer_in', 'transfer_out'):
            continue
        facts = asdict(event)
        for key in ('id', 'sequence', 'source', 'replaces', 'note'):
            facts.pop(key)
        signature = json.dumps(facts, default=str, sort_keys=True)
        if signature in seen:
            duplicates.append((seen[signature], event.id))
        seen[signature] = event.id
    return tuple(duplicates)


def accounting_fingerprint(journal):
    """Bind completeness to facts, without making review receipts self-referential."""
    from portfolio_app.account_journal import journal_to_dict
    raw = journal_to_dict(journal)
    for book in raw.get('securities', ()):
        book.pop('reviews', None)
    return sha256(json.dumps(raw, default=str, sort_keys=True).encode()).hexdigest()


def security_history_fingerprint(journal, position_id):
    """Bind a position review to its facts and transitive transfer dependencies.

    Retained/pending evidence participates, so an unapplied correction still
    requires review. Unrelated positions and review receipts do not invalidate it.
    """
    members = {position_id}
    while True:
        previous = set(members)
        for book in journal.securities:
            for event in book.records:
                if event.kind == 'transfer' and event.other_position_id and (
                        book.position_id in members or event.other_position_id in members):
                    members.update((book.position_id, event.other_position_id))
        if members == previous:
            break
    facts = []
    for book in sorted(journal.securities, key=lambda b: b.position_id):
        if book.position_id in members:
            raw = asdict(book)
            raw.pop('reviews', None)
            facts.append(raw)
    return sha256(json.dumps({'security_scope_v1': position_id, 'books': facts},
                             default=str, sort_keys=True).encode()).hexdigest()
