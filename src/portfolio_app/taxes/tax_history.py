"""Read-only acquisition-month projection. Cost corrections never reset ownership.

FIFO belongs to a depot/share class, not an allocation sleeve. All linked books
are read before filtering the output, so transfers retain acquisition dates.
"""
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from decimal import Decimal

from portfolio_app.ledger.security_history import selected_events
from portfolio_app.taxes.vorabpauschale import TaxLot, ZERO


@dataclass(frozen=True)
class TaxHolding:
    instrument: str
    depot: str
    positions: tuple[str, ...]
    lots: tuple[TaxLot, ...]
    issues: tuple[str, ...] = ()
    splits: tuple[tuple[str, Decimal], ...] = ()


def depot_key(book):
    return book.custody_id or book.account_id or 'position:'+book.position_id


def project_holdings(books, as_of, identities=None, depots=None):
    """Project effective evidence in one pass; inputs contain no current balances.

    Explicit mappings let the adapter identify equivalent listings by ISIN and
    map legacy account labels to depots without changing recorded journal facts.
    """
    books = tuple(b for b in books if b.active)
    identities, depots = identities or {}, depots or {}
    keys = {b.position_id: (identities.get(b.instrument_id, b.instrument_id),
                            depots.get(b.position_id, depot_key(b))) for b in books}
    positions, issues, splits = (defaultdict(list) for _ in range(3))
    lots = defaultdict(deque)
    openings = defaultdict(set)
    initialized, balances, split_seen = set(), {}, set()
    confirmed_empty_before = set()
    events = []
    for b in books:
        positions[keys[b.position_id]].append(b.position_id)
        selected = selected_events(b)
        if selected:
            first = selected[0]
            # This explicit confirmation means ownership began with the recorded
            # purchases. A later ordinary snapshot, even a zero one, says no such
            # thing about earlier years. Only use selected/applied evidence.
            if (first.kind == 'opening' and first.source == 'confirmed_zero_start'
                    and first.date > as_of and not first.quantity and not first.lots):
                confirmed_empty_before.add(b.position_id)
        for e in selected:
            if e.date <= as_of:
                events.append((b, e))
                if e.kind == 'opening':
                    openings[keys[b.position_id]].add(e.date)
    events.sort(key=lambda pair: (pair[1].date, pair[1].sequence, pair[0].position_id))

    def insert_lots(key, incoming):
        lots[key] = deque(sorted((*lots[key], *incoming), key=lambda lot: lot.acquired or ''))

    def consume(key, quantity):
        taken = []
        if lots[key] and not lots[key][0].acquired:
            issues[key].append('FIFO acquisition order unknown')
        # Unknown dates must not be silently placed after known new purchases.
        while quantity and lots[key]:
            lot = lots[key][0]
            part = min(quantity, lot.quantity)
            taken.append(replace(lot, quantity=part))
            quantity -= part
            if part == lot.quantity:
                lots[key].popleft()
            else:
                lots[key][0] = replace(lot, quantity=lot.quantity-part)
        if quantity:
            issues[key].append('Disposal exceeds recorded ownership')
        return taken

    for book, event in events:
        key = keys[book.position_id]
        kind, qty = event.kind, event.quantity
        if not event.date:
            issues[key].append('Undated ownership evidence')
            continue
        if event.asset_charges:
            issues[key].append('Asset-paid charges need tax review')
        if kind in ('opening', 'checkpoint'):
            if book.position_id in initialized:
                # Equal checkpoints are observations, not new acquisitions.
                if qty != balances.get(book.position_id, ZERO):
                    issues[key].append('Balance replacement needs acquisition lots')
                continue
            initialized.add(book.position_id)
            balances[book.position_id] = qty
            if event.lots:
                insert_lots(key, (TaxLot(l.quantity, l.date or None) for l in event.lots))
            elif qty:
                # A boundary proves ownership by that date, not acquisition on it.
                acquired = event.date if event.date[:4] < as_of[:4] or event.date.endswith('-01-01') else None
                insert_lots(key, (TaxLot(qty, acquired),))
        elif book.position_id not in initialized:
            issues[key].append('Opening ownership evidence missing')
        elif kind == 'buy':
            lots[key].append(TaxLot(qty, event.date))
            balances[book.position_id] += qty
        elif kind in ('transfer_in', 'inventory_in'):
            incoming = [TaxLot(l.quantity, l.date or None) for l in event.lots] if event.lots else [TaxLot(qty, event.date if event.date[:4] < as_of[:4] else None)]
            if any(l.acquired and l.acquired > event.date for l in incoming):
                issues[key].append('Transferred acquisition date is after the transfer')
            insert_lots(key, incoming)
            balances[book.position_id] += qty
            if kind == 'inventory_in':
                issues[key].append('Inventory movement needs tax review')
        elif kind in ('sell', 'transfer', 'transfer_out', 'inventory_out'):
            destination = keys.get(event.other_position_id)
            if kind == 'transfer' and destination == key:
                if event.other_position_id != book.position_id:
                    balances[book.position_id] -= qty
                    balances[event.other_position_id] = balances.get(event.other_position_id, ZERO)+qty
                continue
            moved = consume(key, qty)
            balances[book.position_id] -= qty
            if kind == 'transfer':
                if destination is None or destination[0] != key[0]:
                    issues[key].append('Transfer destination unresolved')
                else:
                    if event.other_position_id not in initialized:
                        issues[destination].append('Transfer destination opening missing')
                    insert_lots(destination, moved)
                    issues[destination].extend(issues[key])
                    balances[event.other_position_id] = balances.get(event.other_position_id, ZERO)+qty
            if kind == 'inventory_out':
                issues[key].append('Inventory movement needs tax review')
        elif kind == 'split':
            marker = key, event.date
            if marker not in split_seen:
                split_seen.add(marker)
                lots[key] = deque(replace(l, quantity=l.quantity*qty) for l in lots[key])
                splits[key].append((event.date, qty))
            elif dict(splits[key])[event.date] != qty:
                issues[key].append('Conflicting split ratios')
            balances[book.position_id] *= qty
        elif kind in ('cost', 'distribution', 'fee', 'tax'):
            pass  # These events cannot change acquisition dates.
        else:
            issues[key].append('Unsupported event requires tax review')
    for book in books:
        if book.position_id not in initialized:
            if book.position_id in confirmed_empty_before and not balances.get(book.position_id, ZERO):
                continue
            issues[keys[book.position_id]].append('Ownership at the calculation boundary is unknown')
    # A grouped depot needs compatible opening boundaries before shared FIFO.
    for key, members in positions.items():
        boundaries = openings[key]
        if len(boundaries) > 1 and len(members) > 1:
            issues[key].append('Grouped depot openings need a common boundary')
    return tuple(TaxHolding(instrument, depot, tuple(positions[key]), tuple(lots[key]),
                            tuple(dict.fromkeys(issues[key])), tuple(splits[key]))
                 for key in positions for instrument, depot in (key,))
