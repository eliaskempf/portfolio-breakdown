"""Recorded security gain components, separate from flow-based scope totals.

FIFO costs include buy fees; sell/distribution charges reduce their proceeds.
Other expenses (including purchase taxes) are deducted separately. Transfers keep
lot cost but cross a performance scope at market value, so their allocation is
not inferred from this acquisition-cost breakdown.
"""
from dataclasses import dataclass, replace
from decimal import Decimal, localcontext

from portfolio_app.cost_basis import resolve_cost
from portfolio_app.history_observations import HistoryScope, ObservationFX
from portfolio_app.personal_performance import convert, value_at
from portfolio_app.security_history import replay_securities, selected_events

ZERO = Decimal(0)


@dataclass(frozen=True)
class SecurityGainComponents:
    remaining_cost: Decimal | None
    unrealized: Decimal | None
    realized: Decimal | None
    income: Decimal | None
    expenses: Decimal | None
    purchase_payments: Decimal | None
    sale_proceeds: Decimal | None
    reasons: tuple[str, ...]
    known_realized: Decimal | None = None
    known_income: Decimal | None = None
    known_expenses: Decimal | None = None


def security_gain_components(journal, identity, observations, end, target='EUR', *, current_value=None, calculation=None):
    if calculation is not None:
        recorded = calculation.security_gains(identity, end, target)
    else:
        state = replay_securities(journal.securities, as_of=end)
        book = next(b for b in journal.securities if b.position_id == identity)
        recorded = recorded_security_gains(book, state, observations, end, target)
    scope = HistoryScope((identity,))
    value = current_value if current_value is not None else (
        calculation.value(scope, end, target) if calculation else
        value_at(journal, scope, observations, end, target)).amount
    if recorded.remaining_cost is not None:
        return replace(recorded, unrealized=value-recorded.remaining_cost if value is not None else None)
    if value is None:
        return recorded
    state = calculation.securities(end) if calculation else replay_securities(journal.securities, as_of=end)
    balance = state.positions.get(identity)
    if balance is None or not balance.quantity:
        return recorded
    gains = []
    with localcontext() as context:
        context.prec = 60
        for lot in balance.lots:
            if any(Decimal(p['shares']) == 0 and p['amount'] == '' for p in lot.costs):
                continue
            known = [p for p in lot.costs if p['amount'] != '']
            units = sum((Decimal(p['shares']) for p in known), ZERO)
            resolved = resolve_cost(known, target, ObservationFX(observations)) if units else None
            if resolved is not None and resolved.exact_amount is not None:
                gains.append(value*units/balance.quantity-resolved.exact_amount)
    return replace(recorded, unrealized=sum(gains, ZERO), reasons=(*recorded.reasons,
                   'Unrealized gain covers only units with supplied acquisition costs')) if gains else recorded


def recorded_security_gains(book, state, observations, end, target):
    """Cost and cash facts change on ledger dates, independently of end-date prices."""
    identity = book.position_id
    balance = state.positions.get(identity)
    if balance is None or not balance.boundary:
        return SecurityGainComponents(*(None for _ in range(7)), ('No activated opening quantity',))
    reasons = []

    def cost(parts):
        if not parts:
            return ZERO
        resolved = resolve_cost(list(parts), target, ObservationFX(observations))
        if any(p.get('conversions',{}).get(target,{}).get('method') == 'estimate' for p in parts):
            reasons.append('Estimated cost conversion is not historical evidence')
            return None
        if resolved.estimated:
            reasons.append('Estimated acquisition cost retained from supplied evidence')
        if resolved.exact_amount is None:
            reasons.append(resolved.note)
        return resolved.exact_amount

    def total(values):
        return sum(values, ZERO) if all(v is not None for v in values) else None

    def supported(values):
        known = [v for v in values if v is not None]
        return sum(known, ZERO) if known or not values else None

    cash = [c for c in state.cash if c.position_id == identity]
    converted = {c.id:convert(c.amount,c.currency,target,c.date,observations) for c in cash}
    if any(value is None for value in converted.values()):
        reasons.append('Cash amount or dated FX is missing')
    events = [e for e in selected_events(book) if e.date <= end]
    disposed = {d.event_id:cost(tuple(p for lot in d.lots for p in lot.costs))
                for d in state.disposals if d.position_id == identity and d.kind in ('sell', 'swap')}
    # Preserve every matching event prefix, including IDs containing colons.
    # Group once rather than scanning all cash entries for each transaction.
    postings_by_event = {e.id: [] for e in events}
    for posting in cash:
        if posting.id in postings_by_event:
            postings_by_event[posting.id].append(posting)
        prefix = posting.id
        while ':' in prefix:
            prefix = prefix.rsplit(':', 1)[0]
            suffix = posting.id[len(prefix):]
            if prefix in postings_by_event and (suffix.startswith(':charge:') or suffix == ':unknown-charges'):
                postings_by_event[prefix].append(posting)
    buys, sales, realized, income, expenses = [], [], [], [], []
    for event in events:
        if event.kind in ('inventory_in','inventory_out') and event.flow_classification == 'unresolved':
            reasons.append('Inventory origin or purpose unresolved; gains are partial')
        if event.kind == 'checkpoint':
            reasons.append('Balance checkpoint establishes remaining holdings, not complete prior performance')
        if event.valuation_quality == 'estimated':
            reasons.append('Estimated monetary valuation retained from supplied evidence')
        postings = postings_by_event[event.id]
        net = total([converted[c.id] for c in postings])
        if event.kind == 'buy':
            buys.append(-net if net is not None else None)
            expenses.extend(-converted[c.id] if converted[c.id] is not None else None for c in postings if c.kind == 'tax')
        elif event.kind == 'sell':
            sales.append(net)
            basis = disposed.get(event.id)
            realized.append(net-basis if net is not None and basis is not None else None)
        elif event.kind == 'swap':
            proceeds = convert(event.valuation_amount, event.valuation_currency, target, event.date, observations)
            basis = disposed.get(event.id)
            realized.append(proceeds-basis if proceeds is not None and basis is not None else None)
            expenses.extend(-converted[c.id] if converted[c.id] is not None else None for c in postings)
            if proceeds is None:
                reasons.append('Swap valuation or dated FX is missing')
        elif event.kind == 'reward':
            reward = convert(event.valuation_amount, event.valuation_currency, target, event.date, observations)
            income.append(reward)
            expenses.extend(-converted[c.id] if converted[c.id] is not None else None for c in postings)
            if reward is None:
                reasons.append('Income valuation or dated FX is missing')
        elif event.kind == 'distribution':
            income.append(net)
        elif postings:
            expenses.append(-net if net is not None else None)
        if event.kind in ('transfer', 'transfer_in', 'transfer_out'):
            reasons.append('Transfers retain lot costs; scope returns use transfer-date market values')
    # A charge may be paid from another asset's book. Its carrying expense belongs
    # to the paying asset, regardless of which book owns the economic event.
    expenses.extend(cost(tuple(p for lot in disposal.lots for p in lot.costs))
                    for disposal in state.disposals if disposal.position_id == identity and disposal.kind == 'asset_fee')
    remaining = cost(balance.costs)
    return SecurityGainComponents(remaining,None,total(realized),total(income),total(expenses),total(buys),total(sales),
                                  tuple(dict.fromkeys(reasons)), supported(realized), supported(income), supported(expenses))
