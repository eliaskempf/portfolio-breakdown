"""Readable account history and validated append-only corrections."""
from dataclasses import replace
from collections import Counter, defaultdict
from datetime import date
from uuid import uuid4

from portfolio_app import account_journal as ledger
from portfolio_app.holdings import DataError


ACTIONS = {
    'cash_open': 'Opening cash', 'cash_adjust': 'Cash adjustment', 'cash_flow': 'Deposit / withdrawal',
    'snapshot': 'Opening position', 'trade': 'Trade', 'settlement': 'Daily settlement',
    'final_settlement': 'Final settlement', 'receive': 'Cash receipt', 'fee': 'Fee', 'tax': 'Tax payment',
    'interest': 'Interest', 'transfer': 'Transfer', 'fx': 'Currency conversion', 'debt_open': 'Opening debt',
    'borrow': 'Borrowing', 'repay': 'Repayment', 'reserve': 'Reserve collateral',
    'release': 'Release collateral', 'void': 'Reversal',
}
CONTRACT_KINDS = {'snapshot', 'trade', 'settlement', 'final_settlement'}
AMOUNT_KINDS = {'cash_open', 'cash_adjust', 'cash_flow', 'fee', 'tax', 'interest', 'transfer', 'fx', 'debt_open', 'borrow', 'repay'}


def entry_context(journal, event):
    """A reversal inherits its original's display scope, never its postings."""
    events = {e.id: e for e in journal.events}
    while event.kind == 'void' and event.replaces:
        event = events[event.replaces]
    positions = {p.id: p for p in journal.positions}
    if event.kind == 'receive' and event.reference in events:
        linked = events[event.reference]
        position = positions.get(linked.position_id)
        account = event.account_id or linked.account_id or (position.account_id if position else '')
        denomination = event.currency or linked.currency
    else:
        position = positions.get(event.position_id)
        account = event.account_id or (position.account_id if position else '')
        denomination = event.currency
    contract = next((c for c in journal.contracts if position and c.id == position.contract_id), None)
    investment = next((i for i in journal.investments if position and i.id == position.investment_id), None)
    name = ' · '.join(x for x in (investment.name if investment else '',
                                  f'{contract.product} {contract.expiry}' if contract else '') if x)
    return account, position.id if position else '', denomination or (contract.currency if contract else ''), name


def entry_label(journal, event):
    account, _, denomination, name = entry_context(journal, event)
    names = {a.id: a.name for a in journal.accounts}
    facts = f'{event.quantity:+d} @ {event.price}' if event.kind in {'snapshot', 'trade'} else (
        str(event.price) if event.kind in CONTRACT_KINDS else
        f'{event.amount} {denomination}' if event.kind in AMOUNT_KINDS else '')
    return ' · '.join(x for x in (event.date, ACTIONS.get(event.kind, event.kind), names.get(account, ''), name, facts, event.note) if x)


def activity_rows(journal):
    """Preserve unknown/not-applicable amounts; distinguish claims from cash."""
    state = ledger.replay(journal)
    replacements = {e.replaces: e for e in journal.events if e.replaces}
    roots = {}
    for event in journal.events:
        roots[event.id] = roots[event.replaces] if event.replaces else event.id
    events = {event.id: event for event in journal.events}
    receipts_by_root, posted = defaultdict(list), defaultdict(list)
    for movement in state.cash_movements:
        posted[movement.event_id, movement.account_id].append(movement.amount)
        event = events.get(movement.event_id)
        if event and event.kind == 'receive':
            receipts_by_root[roots.get(event.reference, event.reference)].append(movement)
    names = {a.id: a.name for a in journal.accounts}
    rows = []
    for _, event in sorted(enumerate(journal.events), key=lambda pair: (pair[1].date, pair[1].sequence, pair[0]), reverse=True):
        account, position, denomination, name = entry_context(journal, event)
        successor = replacements.get(event.id)
        status = ('Reversed' if successor.kind == 'void' else 'Corrected') if successor else (
            'Reversal' if event.kind == 'void' else 'Replacement' if event.replaces else 'Recorded')
        deferred = event.kind in CONTRACT_KINDS | {'fee', 'tax', 'interest', 'transfer', 'fx'} and event.value_date and event.value_date > event.date
        timing = f'Claim due {event.value_date}' if deferred else ''
        if event.kind == 'receive':
            timing = 'Receipt of recorded claim'
        if deferred and not successor:
            receipts = receipts_by_root[roots[event.id]]
            if receipts:
                timing += f' · received {receipts[-1].date}'
        amount = str(event.amount) if event.kind in AMOUNT_KINDS else None
        if event.kind in {'trade', 'settlement', 'final_settlement', 'receive'} and not successor:
            postings = posted[event.id, account]
            claim = state.pending.get(roots[event.id])
            if claim is not None:
                amount = str(claim.amount)
            elif postings:
                amount = str(sum(postings))
            elif deferred and receipts:
                amount = str(sum(m.amount for m in receipts))
        if event.kind == 'reserve' and not event.holding_id:
            amount = str(event.amount)
        rows.append({'entry_id': event.id, 'account_id': account, 'position_id': position,
            'Date': event.date, 'Account': names.get(account, ''), 'Investment / contract': name,
            'Action': ACTIONS.get(event.kind, event.kind), 'Amount': amount, 'Currency': denomination,
            'Quantity': str(event.amount) if event.kind == 'reserve' and event.holding_id else (
                str(event.quantity) if event.kind in {'snapshot', 'trade'} else None),
            'Price': str(event.price) if event.price is not None else None,
            'Status': status, 'Cash timing': timing})
    return rows


def correction_route(journal, event):
    if event.id not in {e.id for e in ledger.effective_events(journal)}:
        return 'This entry is already reversed or corrected. Select its replacement to make another correction.'
    if event.group_id:
        return 'This entry belongs to a linked transaction. Review both legs under Position details → Contract history and Roll contracts.'
    if event.kind in {'reserve', 'release'}:
        return 'Manage this reservation under Futures → Collateral.'
    if event.kind not in CONTRACT_KINDS | AMOUNT_KINDS | {'receive'}:
        return 'Review this entry in its source workflow.'
    return ''


def correct_entry(journal, identity, changes=None, *, reverse=False):
    """One replacement atomically cancels the original and supplies new facts.

    Keeping the replacement chain and economic sequence preserves claim identity
    and same-day ordering. Replay validates the entire resulting history.
    """
    event = next((e for e in journal.events if e.id == identity), None)
    if event is None:
        raise DataError('The selected entry no longer exists. Reload the journal.')
    if route := correction_route(journal, event):
        raise DataError(route)
    allowed = {'date', 'value_date', 'note', 'account_id', 'currency', 'position_id',
               'quantity', 'price', 'amount', 'other_account_id', 'other_currency', 'other_amount'}
    changes = changes or {}
    if set(changes) - allowed:
        raise DataError('Correction cannot change entry identity, type or provenance links.')
    replacement = ledger.Event(uuid4().hex, date.today().isoformat(), 'void', replaces=identity,
                               note='Reversed by user') if reverse else replace(
        event, **changes, id=uuid4().hex, replaces=identity, source='manual correction')
    return ledger.append_event(journal, replacement)


def related_entries(journal, event):
    """Readable review candidates when replay rejects an attempted correction."""
    account, position, _, _ = entry_context(journal, event)
    effective = ledger.effective_events(journal)
    index = next((i for i, item in enumerate(effective) if item.id == event.id), len(effective))
    return [e for e in effective[index+1:]
            if (e.reference == event.id or (position and e.position_id == position)
                 or (not position and entry_context(journal, e)[0] == account))]


def accounting_effects(original, candidate):
    """Compare native accounting balances, with claims separate from posted cash."""
    names = {a.id: a.name for a in candidate.accounts}
    contracts = {c.id: c for c in candidate.contracts}
    investments = {i.id: i.name for i in candidate.investments}
    labels = {p.id: ' · '.join(filter(None, (names[p.account_id], investments.get(p.investment_id, ''),
              contracts[p.contract_id].product + ' ' + contracts[p.contract_id].expiry))) for p in candidate.positions}
    counts = Counter(labels.values())
    labels = {pid: label + (f' · position {index}' if counts[label] > 1 else '')
              for index, (pid, label) in enumerate(labels.items(), 1)}
    def balances(journal):
        state = ledger.replay(journal)
        result = {('Cash', names[a], c): 'Unknown' if (a, c) in state.unknown_cash else value
                  for (a, c), value in state.cash.items()}
        for (account, currency, _), amount in state.loans.items():
            key = ('Debt', names[account], currency)
            result[key] = result.get(key, 0) + amount
        for pending in state.pending.values():
            key = ('Pending claims', names[pending.account_id], pending.currency)
            result[key] = result.get(key, 0) + pending.amount
        for position in state.positions.values():
            name = labels[position.spec.id]
            result['Contracts', name, 'contracts'] = position.quantity
            result['Clearing lots', name, position.contract.currency] = ', '.join(f'{lot.quantity:+d} @ {lot.basis}' for lot in position.lots)
        return result
    before, after = balances(original), balances(candidate)
    return [{'Balance': key[0], 'Account / contract': key[1], 'Unit': key[2],
             'Before': str(before.get(key, 0)), 'After': str(after.get(key, 0))}
            for key in sorted(before.keys() | after.keys()) if before.get(key, 0) != after.get(key, 0)]
