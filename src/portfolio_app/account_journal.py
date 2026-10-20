"""Actual native-currency account facts and deterministic futures accounting.

The journal is deliberately separate from legacy futures simulations. One signed
cash balance is canonical for each account/currency; debt is only an explicit
loan. Settlement moves P&L into cash (or a pending claim) and resets the clearing
basis. Notional, margin and collateral never create portfolio wealth.

Dates and sequence define economic ordering; insertion order breaks ties. Cash
postings use Decimal cents, while prices and lot bases retain full precision.
Corrections append a replacement event or a void; original facts remain saved.
"""
from dataclasses import asdict, dataclass, replace
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Iterable

from portfolio_app.derivative_types import (
    ContractSpec, ZERO, currency, decimal, identifier, iso_date, money,
)
from portfolio_app.holdings import DataError
from portfolio_app.storage import save_document
from portfolio_app.security_history import SecurityBook, CapitalAllocation, validate_books
from portfolio_app.transaction_csv import ImportReceipt

FILE = 'account-journal.json'
KINDS = frozenset(('cash_open', 'cash_adjust', 'cash_flow', 'snapshot', 'trade', 'settlement',
    'final_settlement', 'receive', 'fee', 'tax', 'interest', 'transfer', 'fx',
    'debt_open', 'borrow', 'repay', 'reserve', 'release', 'void', 'security_cash', 'security_unknown'))


@dataclass(frozen=True)
class Account:
    id: str
    name: str
    kind: str = 'broker'

    def __post_init__(self):
        identifier(self.id); identifier(self.name, 'Account name')
        if self.kind not in ('broker', 'bank', 'other'):
            raise DataError('Account kind must be broker, bank or other.')


@dataclass(frozen=True)
class PositionSpec:
    id: str
    account_id: str
    contract_id: str
    bucket_id: str = ''
    proxy_position_id: str = ''
    investment_id: str = ''

    def __post_init__(self):
        for name in ('id', 'account_id', 'contract_id'):
            identifier(getattr(self, name), name)
        if not all(isinstance(value, str) for value in (self.bucket_id, self.proxy_position_id, self.investment_id)):
            raise DataError('Category and proxy IDs must be text.')


@dataclass(frozen=True)
class InvestmentSpec:
    """Presentation ownership of capital; never an additional account balance.

    Reference capital is fixed at the recorded start. Allocated capital is that
    principal plus subsequent net allocations, excluding trading gains.
    """
    id: str
    name: str
    account_id: str
    currency: str
    reference_capital: Decimal
    allocated_capital: Decimal

    def __post_init__(self):
        for field in ('id', 'name', 'account_id'):
            identifier(getattr(self, field), field)
        currency(self.currency)
        for field in ('reference_capital', 'allocated_capital'):
            value = money(getattr(self, field))
            if value < 0:
                raise DataError('Allocated and reference capital must be nonnegative.')
            object.__setattr__(self, field, value)


@dataclass(frozen=True)
class CashLink:
    account_id: str
    currency: str
    position_id: str

    def __post_init__(self):
        identifier(self.account_id); currency(self.currency); identifier(self.position_id)


@dataclass(frozen=True)
class HoldingLink:
    position_id: str
    account_id: str

    def __post_init__(self):
        identifier(self.position_id); identifier(self.account_id)


@dataclass(frozen=True)
class MarginRequirement:
    id: str
    account_id: str
    currency: str
    initial: Decimal
    maintenance: Decimal
    as_of: str
    source: str = 'manual'
    position_id: str = ''

    def __post_init__(self):
        identifier(self.id); identifier(self.account_id); currency(self.currency)
        iso_date(self.as_of); identifier(self.source, 'Margin source')
        initial, maintenance = money(self.initial), money(self.maintenance)
        if not ZERO <= maintenance <= initial:
            raise DataError('Margin requires 0 <= maintenance <= initial.')
        object.__setattr__(self, 'initial', initial)
        object.__setattr__(self, 'maintenance', maintenance)


@dataclass(frozen=True)
class Event:
    id: str
    date: str
    kind: str
    account_id: str = ''
    currency: str = ''
    amount: Decimal = ZERO
    position_id: str = ''
    quantity: int = 0
    price: Decimal | None = None
    other_account_id: str = ''
    other_currency: str = ''
    other_amount: Decimal | None = None
    reference: str = ''
    value_date: str = ''
    source: str = 'manual'
    note: str = ''
    group_id: str = ''
    sequence: int = 0
    replaces: str = ''
    collateral_id: str = ''
    holding_id: str = ''
    haircut: Decimal = ZERO

    def __post_init__(self):
        identifier(self.id); iso_date(self.date)
        if self.date > date.today().isoformat():
            raise DataError('Actual journal events cannot be future dated.')
        if self.kind not in KINDS:
            raise DataError('Unknown journal event kind.')
        if self.currency:
            currency(self.currency)
        if self.other_currency:
            currency(self.other_currency)
        if type(self.quantity) is not int or abs(self.quantity) > 1_000_000:
            raise DataError('Contract quantity must be a signed whole number within one million.')
        if type(self.sequence) is not int or self.sequence < 0:
            raise DataError('Event sequence must be a nonnegative integer.')
        # A security reservation is a quantity of shares/units, not cash.
        convert = decimal if self.kind == 'reserve' and self.holding_id else money
        object.__setattr__(self, 'amount', convert(self.amount))
        if self.other_amount is not None:
            object.__setattr__(self, 'other_amount', money(self.other_amount))
        if self.price is not None:
            value = decimal(self.price, 'Price')
            if value <= 0:
                raise DataError('Index futures prices must be positive.')
            object.__setattr__(self, 'price', value)
        value = decimal(self.haircut, 'Haircut')
        if not ZERO <= value <= 1:
            raise DataError('Collateral haircut must be between zero and one.')
        object.__setattr__(self, 'haircut', value)
        iso_date(self.value_date, 'Cash value date', optional=True)
        if self.value_date and self.value_date < self.date:
            raise DataError('Cash value date cannot precede its economic event.')
        for name in ('account_id', 'position_id', 'other_account_id', 'reference',
                     'source', 'note', 'group_id', 'replaces', 'collateral_id', 'holding_id'):
            if not isinstance(getattr(self, name), str):
                raise DataError(f'{name} must be text.')
        identifier(self.source, 'Event source')


@dataclass(frozen=True)
class Journal:
    accounts: tuple[Account, ...] = ()
    contracts: tuple[ContractSpec, ...] = ()
    positions: tuple[PositionSpec, ...] = ()
    events: tuple[Event, ...] = ()
    cash_links: tuple[CashLink, ...] = ()
    holding_links: tuple[HoldingLink, ...] = ()
    margin_requirements: tuple[MarginRequirement, ...] = ()
    adopted_liability_ids: tuple[str, ...] = ()
    version: int = 1
    investments: tuple[InvestmentSpec, ...] = ()
    securities: tuple[SecurityBook, ...] = ()
    capital_history: tuple[CapitalAllocation, ...] = ()
    transaction_imports: tuple[ImportReceipt, ...] = ()

    def __post_init__(self):
        if type(self.version) is not int or self.version not in (1, 2, 3, 4):
            raise DataError('Unsupported account journal version.')
        object.__setattr__(self, 'transaction_imports', tuple(self.transaction_imports))
        if self.transaction_imports and self.version < 3:
            raise DataError('Transaction imports require journal version 3.')
        seen_imports = set()
        for item in self.transaction_imports:
            if not isinstance(item, ImportReceipt) or item.id in seen_imports or (item.replaces and item.replaces not in seen_imports):
                raise DataError('Invalid import identity or correction lineage.')
            seen_imports.add(item.id)
        object.__setattr__(self, 'securities', tuple(self.securities))
        object.__setattr__(self, 'capital_history', tuple(self.capital_history))
        seen_allocations = set()
        for item in self.capital_history:
            if not isinstance(item, CapitalAllocation) or item.id in seen_allocations or item.investment_id not in {i.id for i in self.investments}:
                raise DataError('Invalid dated investment allocation.')
            if item.replaces and item.replaces not in seen_allocations:
                raise DataError('Allocation correction requires retained original evidence.')
            seen_allocations.add(item.id)
        if any(not isinstance(b, SecurityBook) for b in self.securities):
            raise DataError('Invalid securities history.')
        if (self.securities or self.capital_history) and self.version < 2:
            raise DataError('Securities history requires journal version 2.')
        if self.version < 3 and any(b.custody_id or any(e.kind in ('swap','reward','asset_fee') or e.asset_charges or e.timestamp_utc or e.valuation_currency or e.received_quantity for e in b.records) for b in self.securities):
            raise DataError('Non-cash transaction movements require journal version 3.')
        if self.version < 4 and (any(r.facts.get('schema_version') == '2' for r in self.transaction_imports)
                or any(e.kind in ('checkpoint','inventory_in','inventory_out') or e.observed_at
                       or e.flow_classification or e.valuation_quality != 'recorded' or e.valuation_source
                       or any(p.get('quality') == 'estimated' for p in e.costs)
                       for b in self.securities for e in b.records)):
            raise DataError('Inventory and estimated import evidence require journal version 4.')
        if any(e.source.startswith('transaction_csv_v') and e.id not in seen_imports for b in self.securities for e in b.records):
            raise DataError('An imported transaction requires retained source evidence.')
        if any(e.kind in ('security_cash', 'security_unknown') for e in self.events):
            raise DataError('Security cash postings are derived; do not save duplicate account events.')
        adopted = tuple(self.adopted_liability_ids)
        for value in adopted:
            identifier(value, 'Adopted liability ID')
        if len(set(adopted)) != len(adopted):
            raise DataError('Adopted liability IDs must be unique.')
        object.__setattr__(self, 'adopted_liability_ids', adopted)
        for name, cls in (('accounts', Account), ('contracts', ContractSpec),
                          ('positions', PositionSpec), ('events', Event), ('cash_links', CashLink),
                          ('holding_links', HoldingLink), ('margin_requirements', MarginRequirement),
                          ('investments', InvestmentSpec)):
            values = tuple(getattr(self, name))
            if any(not isinstance(item, cls) for item in values):
                raise DataError(f'Invalid {name} records.')
            object.__setattr__(self, name, values)
        for name in ('accounts', 'contracts', 'positions', 'events', 'margin_requirements', 'investments'):
            ids = [item.id for item in getattr(self, name)]
            if len(set(ids)) != len(ids):
                raise DataError(f'Duplicate {name} IDs.')
        accounts = {item.id for item in self.accounts}
        validate_books(self.securities, accounts)
        contracts = {item.id for item in self.contracts}
        positions = {item.id: item for item in self.positions}
        for item in self.positions:
            if item.account_id not in accounts or item.contract_id not in contracts:
                raise DataError('Position refers to an unknown account or contract.')
        investments = {item.id: item for item in self.investments}
        if set(investments).intersection(positions):
            raise DataError('Investment and contract position IDs must be distinct.')
        for item in self.investments:
            if item.account_id not in accounts:
                raise DataError('Investment refers to an unknown account.')
        contract_specs = {item.id: item for item in self.contracts}
        investment_contracts = {}
        for item in self.positions:
            if not item.investment_id:
                continue
            owner = investments.get(item.investment_id)
            contract = contract_specs[item.contract_id]
            if owner is None or (owner.account_id, owner.currency) != (item.account_id, contract.currency):
                raise DataError('Investment requires positions in its account and settlement currency.')
            signature = (contract.exchange, contract.product, item.bucket_id, item.proxy_position_id)
            if investment_contracts.setdefault(owner.id, signature) != signature:
                raise DataError('An investment must retain one product, category and index proxy across its contracts.')
        for name, keys in (('cash_links', [(item.account_id, item.currency) for item in self.cash_links]),
                           ('cash_links', [item.position_id for item in self.cash_links]),
                           ('holding_links', [item.position_id for item in self.holding_links])):
            if len(keys) != len(set(keys)):
                raise DataError(f'Duplicate {name}; cash and holdings must have one owner.')
        for item in (*self.cash_links, *self.holding_links, *self.margin_requirements):
            if item.account_id not in accounts:
                raise DataError('Account binding refers to an unknown account.')
        holdings = {item.position_id: item.account_id for item in self.holding_links}
        for book in self.securities:
            if book.position_id in {link.position_id for link in self.cash_links}:
                raise DataError('Cash holdings must remain owned by the account journal.')
            if book.account_id and book.position_id in holdings and holdings[book.position_id] != book.account_id:
                raise DataError('Security history and collateral binding disagree about account ownership.')
        for link in self.cash_links:
            if link.position_id in holdings and holdings[link.position_id] != link.account_id:
                raise DataError('Cash and holding links disagree about account ownership.')
        for item in self.margin_requirements:
            if item.position_id and (item.position_id not in positions or positions[item.position_id].account_id != item.account_id):
                raise DataError('Margin requirement refers to an incompatible position.')


@dataclass(frozen=True)
class Lot:
    event_id: str
    quantity: int
    basis: Decimal
    entry_price: Decimal
    entry_date: str


@dataclass(frozen=True)
class PositionBalance:
    spec: PositionSpec
    contract: ContractSpec
    quantity: int = 0
    lots: tuple[Lot, ...] = ()
    last_price: Decimal | None = None
    realized_pnl: Decimal = ZERO
    fees: Decimal = ZERO
    taxes: Decimal = ZERO
    history_complete: bool = True

    def unsettled_pnl(self, mark):
        mark = decimal(mark, 'Mark')
        if mark <= 0:
            raise DataError('Mark must be positive.')
        return sum((lot.quantity * self.contract.multiplier * (mark-lot.basis) for lot in self.lots), ZERO)

    def notional(self, mark):
        return self.quantity * self.contract.multiplier * decimal(mark, 'Mark')


@dataclass(frozen=True)
class PendingCash:
    id: str
    account_id: str
    currency: str
    amount: Decimal
    position_id: str
    due_date: str
    kind: str


@dataclass(frozen=True)
class Reservation:
    id: str
    account_id: str
    currency: str
    amount: Decimal
    holding_id: str = ''
    haircut: Decimal = ZERO


@dataclass(frozen=True)
class CashMovement:
    event_id: str
    date: str
    account_id: str
    currency: str
    amount: Decimal
    kind: str
    position_id: str = ''


@dataclass(frozen=True)
class JournalState:
    cash: dict[tuple[str, str], Decimal]
    loans: dict[tuple[str, str, str], Decimal]
    positions: dict[str, PositionBalance]
    pending: dict[str, PendingCash]
    reservations: dict[str, Reservation]
    cash_movements: tuple[CashMovement, ...]
    unknown_cash: frozenset[tuple[str, str]] = frozenset()

    def available_cash(self, account_id, denomination):
        if (account_id, denomination) in self.unknown_cash:
            raise DataError('Cash is incomplete; supply missing transaction amounts first.')
        reserved = sum((item.amount for item in self.reservations.values()
                        if item.account_id == account_id and item.currency == denomination and not item.holding_id), ZERO)
        return self.cash.get((account_id, denomination), ZERO) - reserved


def effective_events(journal):
    """Resolve append-only replacement chains without changing retained facts."""
    seen, replaced, order = {}, set(), {}
    for index, event in enumerate(journal.events):
        if event.replaces:
            if event.replaces not in seen or event.replaces in replaced:
                raise DataError('Correction must replace one existing, active event.')
            replaced.add(event.replaces)
        if event.kind == 'void' and not event.replaces:
            raise DataError('A void must identify the event it replaces.')
        order[event.id] = order[event.replaces] if event.replaces else index
        seen[event.id] = event
    active = [(order[event.id], event) for event in journal.events
              if event.id not in replaced and event.kind != 'void']
    return tuple(event for _, event in sorted(active, key=lambda item: (item[1].date, item[1].sequence, item[0])))


def replay(journal: Journal, *, as_of: str | None = None) -> JournalState:
    """Pure replay. Pending receipts require an explicit receive event.

    `as_of` is economic history with currently known corrections, not a view of
    what information had been entered at that time.
    """
    if not isinstance(journal, Journal):
        raise DataError('A validated Journal is required.')
    if as_of is not None:
        iso_date(as_of)
    accounts = {item.id for item in journal.accounts}
    contracts = {item.id: item for item in journal.contracts}
    positions = {spec.id: PositionBalance(spec, contracts[spec.contract_id]) for spec in journal.positions}
    cash, loans, pending, reservations, movements = {}, {}, {}, {}, []
    opened_cash, started_positions, settled_dates = set(), set(), set()
    from portfolio_app.security_history import cash_postings
    postings = cash_postings(journal)
    if len({e.id for e in postings}) != len(postings) or {e.id for e in postings}.intersection(e.id for e in journal.events):
        raise DataError('Account event ID collides with a securities posting.')
    events = tuple(sorted((*effective_events(journal), *postings), key=lambda e: (e.date, e.sequence)))
    unknown_cash = set()
    roots = {e.id: e.id for e in postings}
    for event in journal.events:
        roots[event.id] = roots[event.replaces] if event.replaces else event.id

    def post(event, account, denomination, amount, *, kind=None, position_id=None):
        if account not in accounts:
            raise DataError('Cash posting refers to an unknown account.')
        currency(denomination)
        amount = money(amount)
        key = (account, denomination)
        cash[key] = cash.get(key, ZERO) + amount
        movements.append(CashMovement(event.id, event.date, account, denomination, amount,
                                      kind or event.kind, position_id if position_id is not None else event.position_id or event.holding_id))

    def pay_or_pending(event, account, denomination, amount):
        if account not in accounts:
            raise DataError('Cash posting refers to an unknown account.')
        currency(denomination)
        amount = money(amount)
        if event.value_date and event.value_date > event.date:
            root = roots[event.id]
            pending[root] = PendingCash(root, account, denomination, amount,
                event.position_id, event.value_date, event.kind)
        else:
            post(event, account, denomination, amount)

    for event in events:
        if as_of and event.date > as_of:
            continue
        if event.account_id and event.account_id not in accounts:
            raise DataError('Event refers to an unknown account.')
        if event.other_account_id and event.other_account_id not in accounts:
            raise DataError('Event refers to an unknown destination account.')
        p = positions.get(event.position_id)
        if event.position_id and p is None:
            raise DataError('Event refers to an unknown contract position.')
        if p and event.account_id and event.account_id != p.spec.account_id:
            raise DataError('Event account differs from its position account.')
        if event.kind in ('snapshot', 'trade', 'settlement', 'final_settlement'):
            if p is None or event.price is None:
                raise DataError('Contract event requires a position and price.')
            if event.currency and event.currency != p.contract.currency:
                raise DataError('Contract event currency differs from settlement currency.')
            if event.amount or event.other_amount is not None:
                raise DataError('Contract cash is derived from price; record fees or adjustments separately.')
            if event.kind in ('snapshot', 'trade'):
                if not event.quantity:
                    raise DataError('A snapshot or trade needs nonzero signed contracts.')
                if event.kind == 'trade' and event.date > (p.contract.last_trade_date or p.contract.expiry):
                    raise DataError('Cannot trade after the contract last trading date.')
                if event.kind == 'trade' and event.price % p.contract.tick:
                    raise DataError('Execution price must align with the contract tick.')
                if event.kind == 'snapshot':
                    if p.spec.id in started_positions:
                        raise DataError('A snapshot must be the first event for its position.')
                    positions[p.spec.id] = replace(p, quantity=event.quantity,
                        lots=(Lot(event.id, event.quantity, event.price, event.price, event.date),),
                        last_price=event.price, history_complete=False)
                else:
                    lots, remaining, pnl = list(p.lots), event.quantity, ZERO
                    while lots and remaining and (lots[0].quantity > 0) != (remaining > 0):
                        lot = lots[0]
                        closed = min(abs(remaining), abs(lot.quantity))
                        signed_closed = closed if lot.quantity > 0 else -closed
                        pnl += signed_closed * p.contract.multiplier * (event.price-lot.basis)
                        remaining += signed_closed
                        left = lot.quantity-signed_closed
                        if left:
                            lots[0] = replace(lot, quantity=left)
                        else:
                            lots.pop(0)
                    if remaining:
                        lots.append(Lot(event.id, remaining, event.price, event.price, event.date))
                    # Backfills can reduce a close's residual claim to zero.
                    # Retain its identity so an existing receipt still replays.
                    deferred_close = remaining != event.quantity and event.value_date and event.value_date > event.date
                    if pnl or deferred_close:
                        pay_or_pending(event, p.spec.account_id, p.contract.currency, pnl)
                    positions[p.spec.id] = replace(p, quantity=sum(lot.quantity for lot in lots),
                        lots=tuple(lots), last_price=event.price, realized_pnl=p.realized_pnl+money(pnl))
                started_positions.add(p.spec.id)
            else:
                if not p.quantity:
                    raise DataError('Cannot settle a position with no open contracts.')
                key = (p.spec.id, event.date)
                if key in settled_dates:
                    raise DataError('Only one settlement per position/day; correct the existing event.')
                settled_dates.add(key)
                pnl = money(p.unsettled_pnl(event.price))
                pay_or_pending(event, p.spec.account_id, p.contract.currency, pnl)
                final = event.kind == 'final_settlement'
                if final and event.date < (p.contract.final_settlement_date or p.contract.expiry):
                    raise DataError('Final settlement cannot precede the contract settlement date.')
                positions[p.spec.id] = replace(p, quantity=0 if final else p.quantity,
                    lots=() if final else tuple(replace(lot, basis=event.price) for lot in p.lots),
                    last_price=event.price, realized_pnl=p.realized_pnl+pnl)
            continue
        if event.kind == 'security_unknown':
            unknown_cash.add((event.account_id, event.currency))
            cash.setdefault((event.account_id, event.currency), ZERO)
            continue
        if event.kind == 'security_cash':
            pay_or_pending(event, event.account_id, event.currency, event.amount)
            continue
        if event.kind == 'receive':
            reference = roots.get(event.reference, event.reference)
            item = pending.get(reference)
            if item is None or event.date < item.due_date:
                raise DataError('Receipt requires an outstanding claim on or after its value date.')
            if event.account_id and event.account_id != item.account_id or event.currency and event.currency != item.currency:
                raise DataError('Receipt account/currency differs from its pending claim.')
            post(event, item.account_id, item.currency, item.amount, kind=item.kind, position_id=item.position_id)
            del pending[reference]
            continue
        if event.kind in ('fee', 'tax', 'interest'):
            account = event.account_id or (p.spec.account_id if p else '')
            denomination = event.currency or (p.contract.currency if p else '')
            if event.kind != 'interest' and event.amount < 0:
                raise DataError('Fee and manual tax amounts must be nonnegative.')
            pay_or_pending(event, account, denomination, event.amount if event.kind == 'interest' else -event.amount)
            if p:
                # Attribution is native only. Cross-currency fees remain in cash
                # movements and must be converted explicitly by reporting code.
                if denomination == p.contract.currency and event.kind in ('fee', 'tax'):
                    field_name = 'fees' if event.kind == 'fee' else 'taxes'
                    positions[p.spec.id] = replace(p, **{field_name: getattr(p, field_name)+event.amount})
            continue
        if event.kind in ('reserve', 'release'):
            identifier(event.collateral_id, 'Collateral ID')
            if event.kind == 'release':
                if event.collateral_id not in reservations:
                    raise DataError('Cannot release an unknown reservation.')
                if event.account_id and reservations[event.collateral_id].account_id != event.account_id:
                    raise DataError('Reservation belongs to another account.')
                del reservations[event.collateral_id]
            else:
                if event.account_id not in accounts:
                    raise DataError('Reservation requires an account.')
                currency(event.currency)
                if event.amount < 0:
                    raise DataError('Collateral reservation must be nonnegative.')
                if event.holding_id:
                    bindings = {link.position_id: link.account_id for link in journal.holding_links}
                    if bindings.get(event.holding_id) != event.account_id:
                        raise DataError('Security collateral must reference a holding linked to this account.')
                else:
                    already = sum((item.amount for key, item in reservations.items()
                        if key != event.collateral_id and item.account_id == event.account_id
                        and item.currency == event.currency and not item.holding_id), ZERO)
                    if already+event.amount > max(ZERO, cash.get((event.account_id, event.currency), ZERO)):
                        raise DataError('Cash reservation exceeds owned cash.')
                reservations[event.collateral_id] = Reservation(event.collateral_id, event.account_id,
                    event.currency, event.amount, event.holding_id, event.haircut)
            continue
        if event.account_id not in accounts:
            raise DataError('Cash/debt event requires an account.')
        currency(event.currency)
        if event.kind in ('cash_open', 'cash_adjust', 'cash_flow'):
            if event.kind == 'cash_open':
                key = event.account_id, event.currency
                if key in opened_cash or key in cash:
                    raise DataError('Opening balance must be the first cash event for its account/currency.')
                opened_cash.add(key)
            post(event, event.account_id, event.currency, event.amount)
        elif event.kind == 'transfer':
            if event.amount <= 0 or event.other_account_id not in accounts or event.other_account_id == event.account_id:
                raise DataError('Transfer needs distinct accounts and a positive amount.')
            if event.other_currency and event.other_currency != event.currency:
                raise DataError('A currency change requires explicit FX legs.')
            if cash.get((event.account_id, event.currency), ZERO) < event.amount:
                raise DataError('Transfer exceeds available source cash; record borrowing explicitly if needed.')
            post(event, event.account_id, event.currency, -event.amount)
            pay_or_pending(event, event.other_account_id, event.currency, event.amount)
        elif event.kind == 'fx':
            destination = event.other_account_id or event.account_id
            currency(event.other_currency)
            if event.currency == event.other_currency or event.amount <= 0 or event.other_amount is None or event.other_amount <= 0:
                raise DataError('FX requires distinct currencies and two positive actual cash amounts.')
            if cash.get((event.account_id, event.currency), ZERO) < event.amount:
                raise DataError('FX exceeds source cash; record borrowing explicitly if needed.')
            post(event, event.account_id, event.currency, -event.amount)
            pay_or_pending(event, destination, event.other_currency, event.other_amount)
        elif event.kind in ('debt_open', 'borrow', 'repay'):
            identifier(event.reference, 'Loan ID')
            if event.amount < 0:
                raise DataError('Loan amounts must be nonnegative.')
            key = event.account_id, event.currency, event.reference
            balance = loans.get(key, ZERO)
            if event.kind == 'debt_open':
                if key in loans:
                    raise DataError('Loan opening balance already exists.')
                loans[key] = event.amount
            elif event.kind == 'borrow':
                loans[key] = balance+event.amount
                post(event, event.account_id, event.currency, event.amount)
            else:
                if event.amount > balance or event.amount > cash.get((event.account_id, event.currency), ZERO):
                    raise DataError('Repayment exceeds the recorded loan or available cash.')
                loans[key] = balance-event.amount
                post(event, event.account_id, event.currency, -event.amount)
        else:
            raise DataError('Unsupported journal event.')
    if as_of is None:
        for link in journal.cash_links:
            if (link.account_id, link.currency) not in opened_cash:
                raise DataError('Adopted cash needs an explicit opening balance, including zero.')
        for identity in journal.adopted_liability_ids:
            if not any(reference == 'legacy:'+identity for _, _, reference in loans):
                raise DataError('An adopted liability must retain its loan record; correct or undo the adoption explicitly.')
    return JournalState(cash, loans, positions, pending, reservations, tuple(movements), frozenset(unknown_cash))


def append_events(journal: Journal, events: Iterable[Event]) -> Journal:
    """An identical event ID is idempotent; differing payloads are rejected."""
    result, known = list(journal.events), {event.id: event for event in journal.events}
    for event in events:
        if not isinstance(event, Event):
            raise DataError('Append requires validated Event records.')
        if event.id in known:
            if known[event.id] != event:
                raise DataError('Event ID already exists with different content.')
            continue
        result.append(event); known[event.id] = event
    updated = replace(journal, events=tuple(result))
    replay(updated)
    return updated


def append_event(journal, event):
    return append_events(journal, (event,))


def add_account(journal, account):
    return _add(journal, 'accounts', account)


def add_contract(journal, contract):
    return _add(journal, 'contracts', contract)


def add_position(journal, position):
    return _add(journal, 'positions', position)


def _add(journal, field_name, item):
    existing = next((entry for entry in getattr(journal, field_name) if entry.id == item.id), None)
    if existing is not None:
        if existing != item:
            raise DataError(f'{field_name} ID already exists with different facts.')
        return journal
    return replace(journal, **{field_name: (*getattr(journal, field_name), item)})


def roll(journal, *, close: Event, opening: Event, charges: Iterable[Event] = ()):
    """Atomic two-leg roll; a price gap itself never creates cash P&L."""
    specs = {p.id: p for p in journal.positions}
    old, new = specs.get(close.position_id), specs.get(opening.position_id)
    if (close.kind != 'trade' or opening.kind != 'trade' or not close.group_id
            or close.group_id != opening.group_id or close.date != opening.date
            or old is None or new is None or old.account_id != new.account_id
            or old.contract_id == new.contract_id):
        raise DataError('Roll requires grouped same-date trades in two expiries in one account.')
    state = replay(journal, as_of=close.date)
    old_contract, new_contract = state.positions[old.id].contract, state.positions[new.id].contract
    if (old_contract.exchange, old_contract.product, old_contract.currency) != (new_contract.exchange, new_contract.product, new_contract.currency):
        raise DataError('A roll must keep the same product and settlement currency.')
    if old.investment_id != new.investment_id:
        raise DataError('A roll must retain the same investment identity.')
    current = state.positions[old.id].quantity
    if not current or close.quantity * current >= 0 or abs(close.quantity) > abs(current) or opening.quantity != -close.quantity:
        raise DataError('Roll closes an existing quantity and opens the same signed quantity.')
    return append_events(journal, (close, opening, *charges))


def journal_to_dict(journal):
    result = asdict(journal)
    if journal.version < 4:
        for book in result['securities']:
            for event in book['records']:
                for field in ('observed_at','flow_classification','valuation_quality','valuation_source'):
                    event.pop(field)
    if journal.version < 3:
        result.pop('transaction_imports')
        for book in result['securities']:
            book.pop('custody_id')
            for event in book['records']:
                for field in ('received_quantity','valuation_amount','valuation_currency','asset_charges','timestamp_utc','group_id'):
                    event.pop(field)
                for lot in event['lots']:
                    lot.pop('sequence')
    if journal.version == 1:
        result.pop('securities')
        result.pop('capital_history')
    return result


def journal_from_dict(raw):
    if not isinstance(raw, dict):
        raise DataError('Account journal must be an object.')
    fields = dict(raw)
    for name, cls in (('accounts', Account), ('contracts', ContractSpec), ('positions', PositionSpec),
                      ('events', Event), ('cash_links', CashLink), ('holding_links', HoldingLink),
                      ('margin_requirements', MarginRequirement), ('investments', InvestmentSpec)):
        fields[name] = tuple(cls(**item) for item in fields.get(name, ()))
    from portfolio_app.security_history import book_from_dict
    fields['securities'] = tuple(book_from_dict(item) for item in fields.get('securities', ()))
    fields['transaction_imports'] = tuple(ImportReceipt(**item) for item in fields.get('transaction_imports', ()))
    fields['capital_history'] = tuple(CapitalAllocation(**item) for item in fields.get('capital_history', ()))
    result = Journal(**fields)
    replay(result)
    return result


def load_journal(directory: Path):
    path = Path(directory)/FILE
    if not path.exists():
        return Journal(), None
    try:
        content = path.read_bytes()
        result = journal_from_dict(json.loads(content))
        return result, sha256(content).hexdigest()
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise DataError(f'Invalid account journal; existing file retained: {exc}') from exc


def save_journal(directory: Path, journal: Journal, expected_revision: str | None):
    from portfolio_app.workspace_lock import workspace_lock
    with workspace_lock(Path(directory)):
        state = replay(journal)
        if any(item.holding_id for item in state.reservations.values()):
            from portfolio_app.account_margin import validate_reservations
            from portfolio_app.positions import read_snapshot
            from portfolio_app.security_history import effective_holdings
            validate_reservations(journal, effective_holdings(read_snapshot(Path(directory)/'holdings.csv').holdings, journal.securities))
        content = json.dumps(journal_to_dict(journal), default=lambda value: str(value) if isinstance(value, Decimal) else value,
                             indent=2, sort_keys=True)+'\n'
        save_document(Path(directory)/FILE, content, expected_revision)
