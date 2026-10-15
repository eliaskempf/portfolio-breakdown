"""Read-only futures P&L and settlement presentation from the account journal."""
from collections import defaultdict

import pandas as pd

from portfolio_app.account_journal import effective_events, replay
from portfolio_app.derivative_types import ZERO


def pnl_breakdown(journal, members, valued):
    """Gross realized/unrealized trading P&L at current reporting FX.

    Realized belongs to FIFO-closed quantities; unrealized measures remaining
    lots from entry, including their daily settlements. Settlement basis is
    retained separately for cash accounting. Snapshots start at their recorded
    basis, so earlier performance remains unknown.
    """
    state = replay(journal)
    indexed = valued.set_index('position_id')
    rates = valued.attrs.get('account_fx_rates', {})
    realized, unrealized = 0., 0.
    for identity in members:
        balance = state.positions[identity]
        rate = rates.get(balance.contract.currency)
        # The ledger's realized_pnl is settlement/close cash, including pending
        # claims. Remove the settled movement attributable to still-open lots
        # to recover closed-trade performance without replaying FIFO matching.
        open_settled = sum((lot.quantity * balance.contract.multiplier *
                            (lot.basis-lot.entry_price) for lot in balance.lots), ZERO)
        closed_pnl = balance.realized_pnl-open_settled
        realized += (float(closed_pnl) * rate if rate is not None
                     else 0. if not closed_pnl else float('nan'))
        if balance.quantity:
            mark = indexed.loc[identity].get('mark_price') if identity in indexed.index else None
            unrealized += (float(balance.unsettled_pnl(mark)+open_settled) * rate
                           if pd.notna(mark) and rate is not None else float('nan'))
    return realized, unrealized


def settlement_history(journal, members):
    """Effective daily/final settlements across every linked expiry.

    Use the journal's derived postings, including receipts of deferred claims,
    instead of reconstructing gains from prices or today's contract quantity.
    Corrections retain their original claim identity.
    """
    state = replay(journal)
    events = effective_events(journal)
    roots = {}
    for event in journal.events:
        roots[event.id] = roots[event.replaces] if event.replaces else event.id
    receipts = {event.id: roots.get(event.reference, event.reference)
                for event in events if event.kind == 'receive'}
    amounts = defaultdict(lambda: ZERO)
    posted = {}
    for movement in state.cash_movements:
        root = receipts.get(movement.event_id, roots[movement.event_id])
        amounts[root] += movement.amount
        posted[root] = movement.date
    for root, claim in state.pending.items():
        amounts[root] += claim.amount
    quantities, rows = defaultdict(int), []
    for event in events:
        identity = event.position_id
        if identity not in members:
            continue
        if event.kind == 'snapshot':
            quantities[identity] = event.quantity
        elif event.kind == 'trade':
            quantities[identity] += event.quantity
        elif event.kind in ('settlement', 'final_settlement'):
            root = roots[event.id]
            contract = state.positions[identity].contract
            rows.append({'Date': event.date, 'Contract': contract.product,
                'Maturity': contract.expiry,
                'Type': 'Final settlement' if event.kind == 'final_settlement' else 'Daily settlement',
                'Contracts': quantities[identity], 'Settlement price': float(event.price),
                'Settlement P&L': float(amounts[root]), 'Currency': contract.currency,
                'Cash status': 'Pending' if root in state.pending else 'Posted',
                'Value date': event.value_date or event.date,
                'Posted on': posted.get(root, ''), 'Source': event.source, 'Note': event.note})
            if event.kind == 'final_settlement':
                quantities[identity] = 0
    return pd.DataFrame(rows)
