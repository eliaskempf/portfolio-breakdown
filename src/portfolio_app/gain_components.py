"""Recorded security gain components, separate from flow-based scope totals.

FIFO costs include buy fees; sell/distribution charges reduce their proceeds.
Other expenses (including purchase taxes) are deducted separately. Transfers keep
lot cost but cross a performance scope at market value, so their allocation is
not inferred from this acquisition-cost breakdown.
"""
from dataclasses import dataclass
from decimal import Decimal

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


def security_gain_components(journal, identity, observations, end, target='EUR', *, current_value=None, calculation=None):
    state = calculation.securities(end) if calculation else replay_securities(journal.securities, as_of=end)
    book = next(b for b in journal.securities if b.position_id == identity)
    balance = state.positions.get(identity)
    if balance is None or not balance.boundary:
        return SecurityGainComponents(*(None for _ in range(7)), ('No activated opening quantity',))
    reasons = []

    def cost(parts):
        if not parts:
            return ZERO
        resolved = resolve_cost(list(parts), target, ObservationFX(observations))
        if resolved.estimated:
            reasons.append('Estimated cost conversion is not historical evidence')
            return None
        if resolved.exact_amount is None:
            reasons.append(resolved.note)
        return resolved.exact_amount

    def total(values):
        return sum(values, ZERO) if all(v is not None for v in values) else None

    cash = [c for c in state.cash if c.position_id == identity]
    converted = {c.id:convert(c.amount,c.currency,target,c.date,observations) for c in cash}
    if any(value is None for value in converted.values()):
        reasons.append('Cash amount or dated FX is missing')
    events = [e for e in selected_events(book) if e.date <= end]
    disposed = {d.event_id:cost(tuple(p for lot in d.lots for p in lot.costs))
                for d in state.disposals if d.position_id == identity and d.kind == 'sell'}
    buys, sales, realized, income, expenses = [], [], [], [], []
    for event in events:
        postings = [c for c in cash if c.id == event.id or c.id.startswith(event.id+':charge:')
                    or c.id == event.id+':unknown-charges']
        net = total([converted[c.id] for c in postings])
        if event.kind == 'buy':
            buys.append(-net if net is not None else None)
            expenses.extend(-converted[c.id] if converted[c.id] is not None else None for c in postings if c.kind == 'tax')
        elif event.kind == 'sell':
            sales.append(net)
            basis = disposed.get(event.id)
            realized.append(net-basis if net is not None and basis is not None else None)
        elif event.kind == 'distribution':
            income.append(net)
        elif postings:
            expenses.append(-net if net is not None else None)
        if event.kind in ('transfer', 'transfer_in', 'transfer_out'):
            reasons.append('Transfers retain lot costs; scope returns use transfer-date market values')
    remaining = cost(balance.costs)
    scope = HistoryScope((identity,))
    value = current_value if current_value is not None else (
        calculation.value(scope, end, target) if calculation else
        value_at(journal, scope, observations, end, target)).amount
    unrealized = value-remaining if value is not None and remaining is not None else None
    return SecurityGainComponents(remaining,unrealized,total(realized),total(income),total(expenses),total(buys),total(sales),
                                  tuple(dict.fromkeys(reasons)))
