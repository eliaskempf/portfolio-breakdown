"""Read-only end-of-day portfolio statements from applied history and dated marks."""
from collections import defaultdict, OrderedDict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from portfolio_app.performance.gain_accounting import GainSummary, summarize_known_gain
from portfolio_app.performance.history_observations import HistoryScope
from portfolio_app.performance.history_view import _requires_full_valuation_scan
from portfolio_app.performance.personal_performance import (
    AmountResult, HistoryCalculation, _portfolio_bank_starts, calculate_history, confirmed_empty_before,
)


@dataclass(frozen=True)
class AccountComponent:
    label: str
    amount: Decimal | int | None
    unit: str


@dataclass(frozen=True)
class HistoricalHolding:
    identity: str
    kind: str
    quantity: Decimal | None
    value: AmountResult
    components: tuple[AccountComponent, ...] = ()


@dataclass(frozen=True)
class PortfolioSnapshot:
    stamp: str
    currency: str
    rows: tuple[HistoricalHolding, ...]
    value: AmountResult
    known_subtotal: Decimal | None
    gain: GainSummary
    return_start: str | None
    time_weighted: float | None
    return_reasons: tuple[str, ...]
    estimated_return: bool


def portfolio_snapshot(calculation: HistoryCalculation, scope: HistoryScope, stamp: str,
                       currency: str) -> PortfolioSnapshot:
    """Use exact selected-date endpoints, including verified market-closure carry."""
    date.fromisoformat(stamp)
    recent = calculation._get(('portfolio_snapshots',), OrderedDict)
    key = scope, stamp, currency
    if key not in recent:
        recent[key] = _statement(calculation, scope, stamp, currency)
        if len(recent) > 4:
            recent.popitem(last=False)
    recent.move_to_end(key)
    return recent[key]


def _account_components(state, accounts):
    """Account rows contribute equity once. Their native balances explain that row."""
    components = defaultdict(list)
    for (account, unit), amount in state.cash.items():
        if account in accounts:
            known = None if (account, unit) in state.unknown_cash else amount
            components[account].append(AccountComponent('Cash', known, unit))
    for (account, unit, _loan), amount in state.loans.items():
        if account in accounts and amount:
            components[account].append(AccountComponent('Debt', -amount, unit))
    for claim in state.pending.values():
        if claim.account_id in accounts and claim.amount:
            components[claim.account_id].append(AccountComponent('Outstanding payment', claim.amount, claim.currency))
    for position in state.positions.values():
        account = position.spec.account_id
        if account in accounts and position.quantity:
            contract = position.contract
            name = f'{contract.name or contract.product} · {contract.expiry}'
            components[account].append(AccountComponent(name, position.quantity, 'contracts'))
    return components


def _statement(calculation, scope, stamp, currency):
    journal = calculation.journal
    # The compact closing timeline includes other books' transfers and same-day events.
    states = calculation._security_timeline.closing_positions(scope.positions, stamp) if scope.positions else {}
    rows = []
    for identity in scope.positions:
        state = states.get(identity)
        quantity = state.quantity if state and state.boundary and not state.has_issues else None
        book = calculation._books.get(identity)
        if quantity == 0 or (book and confirmed_empty_before(book, stamp, events=calculation._events[identity])):
            continue
        value = calculation.value(HistoryScope((identity,)), stamp, currency)
        rows.append(HistoricalHolding(identity, 'Security', quantity, value))

    # Linked cash lives here once. An account row is net equity including its
    # loans, outstanding payments and unsettled futures P&L.
    bank_starts = _portfolio_bank_starts(journal, scope, calculation)
    accounts = set(scope.accounts)
    components = _account_components(calculation.accounts(stamp), accounts) if accounts else {}
    for identity in scope.accounts:
        if stamp < bank_starts.get(identity, '') or stamp < calculation._account_inceptions.get(identity, ''):
            continue
        value = calculation.value(HistoryScope(accounts=(identity,)), stamp, currency)
        rows.append(HistoricalHolding(identity, 'Account equity', None, value, tuple(components.get(identity, ()))))

    value = calculation.value(scope, stamp, currency)
    gain = summarize_known_gain(journal, scope, calculation.observations, stamp, currency, calculation=calculation)
    dates = calculation.event_dates(scope)
    start = dates[0] if dates and dates[0] <= stamp else None
    twr, notes, estimated = None, ('No dated opening for the selected interval.',), False
    if start and start < stamp:
        recorded = calculation.observation_dates(scope, currency)
        # The chart uses the same sign-eligibility check. Nonnegative ledgers
        # need only inspect explicitly supplied negative valuations.
        negative = (None if _requires_full_valuation_scan(calculation, scope, recorded, stamp, currency)
                    else recorded.negative_equity_dates)
        period = calculate_history(journal, scope, calculation.observations, start, stamp, currency,
                                   calculation=calculation, annualized=False, negative_dates=negative)
        twr, notes, estimated = period.time_weighted, period.time_weighted_reasons, period.estimated
    elif start == stamp:
        notes = ('A return needs an interval after the opening date.',)
    supported = [row.value.amount for row in rows if row.value.amount is not None]
    subtotal = sum(supported, Decimal(0)) if supported else None
    return PortfolioSnapshot(stamp, currency, tuple(rows), value, subtotal, gain, start, twr, notes, estimated)
