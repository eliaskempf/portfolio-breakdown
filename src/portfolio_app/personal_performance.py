"""Historical equity, external flows and metric-specific coverage.

All observations are supplied explicitly. No current prices, current ETF weights,
latest FX or interpolation enter this module.
"""
from bisect import bisect_right
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
import math

from portfolio_app.account_journal import effective_events, replay
from portfolio_app.history_observations import HistoryScope, index_observations, lookup, rate
from portfolio_app.security_history import cash_postings, history_fingerprint, replay_securities, selected_events

from portfolio_app.history_diagnostics import HistoryIssue as Issue
from portfolio_app.history_valuation import price_at, provenance, ValuationEvidence

ZERO = Decimal(0)


@dataclass(frozen=True)
class AmountResult:
    amount: Decimal | None
    reasons: tuple[str, ...] = ()
    evidence: tuple[ValuationEvidence, ...] = ()


@dataclass(frozen=True)
class ExternalFlow:
    id: str
    date: str
    sequence: int
    amount: Decimal | None
    reason: str = ''
    event_id: str = ''
    expense: Decimal = ZERO
    income: Decimal = ZERO
    postings: tuple[str, ...] = ()


@dataclass(frozen=True)
class PersonalPerformance:
    opening_value: Decimal | None
    ending_value: Decimal | None
    net_contributions: Decimal | None
    invested_capital: Decimal | None
    gain: Decimal | None
    money_weighted: float | None
    time_weighted: float | None
    days: int
    value_reasons: tuple[str, ...]
    flow_reasons: tuple[str, ...]
    money_weighted_reasons: tuple[str, ...]
    time_weighted_reasons: tuple[str, ...]
    opening_capital: Decimal | None = None
    opening_known_gain: Decimal | None = None
    total_gain: Decimal | None = None
    capital_reasons: tuple[str, ...] = ()
    valuation_evidence: tuple[ValuationEvidence, ...] = ()
    time_weighted_method: str = 'daily'


class HistoryCalculation:
    """Reusable facts for one journal/observation snapshot, never a global cache.

    A chart asks for many overlapping intervals. Reuse dated ledger states and
    opening evidence while each interval keeps its own coverage and return gates.
    """

    def __init__(self, journal, observations):
        self.journal, self.observations = journal, index_observations(observations)
        events = [event for book in journal.securities for event in selected_events(book)]
        # Holdings change on event dates; separately dated charges also change cash.
        self._security_dates = sorted({event.date for event in events} |
            {charge.date for event in events for charge in event.charges if charge.date})
        self.fingerprint = history_fingerprint(journal)
        self._facts = {}
        self._security_fingerprints = {}

    def _get(self, key, load):
        if key not in self._facts:
            self._facts[key] = load()
        return self._facts[key]

    def securities(self, stamp=None):
        interval = bisect_right(self._security_dates, stamp) if stamp is not None else None
        return self._get(('securities', interval), lambda: replay_securities(self.journal.securities, as_of=stamp))

    def security_gains(self, identity, stamp, target):
        from portfolio_app.gain_components import recorded_security_gains
        interval = bisect_right(self._security_dates, stamp)
        def load():
            book = next(b for b in self.journal.securities if b.position_id == identity)
            return recorded_security_gains(book, self.securities(stamp), self.observations, stamp, target)
        return self._get(('security_gains', identity, interval, target), load)

    def accounts(self, stamp):
        from portfolio_app.derivative_types import iso_date
        if stamp is not None:
            iso_date(stamp)
        # Cash postings are derived once from the same immutable journal snapshot.
        # Pending receipts still require explicit receive events: value_date alone
        # does not change account balances and must not advance this timeline.
        postings = self._get(('account_postings',), lambda: cash_postings(
            self.journal, security_cash=self.securities().cash))
        dates = self._get(('account_dates',), lambda: sorted(
            {event.date for event in effective_events(self.journal)} | {event.date for event in postings}))
        interval = bisect_right(dates, stamp) if stamp is not None else None
        return self._get(('accounts', interval), lambda: replay(self.journal, as_of=stamp, _security_postings=postings))

    def value(self, scope, stamp, target):
        return self._get(('value', scope, stamp, target), lambda: value_at(
            self.journal, scope, self.observations, stamp, target, calculation=self))

    def opening(self, scope, stamp, target):
        return self._get(('opening', scope, stamp, target), lambda: opening_value_at(
            self.journal, scope, self.observations, stamp, target, calculation=self))

    def capital(self, scope, stamp, target):
        return self._get(('capital', scope, stamp, target), lambda: capital_at_start(
            self.journal, scope, self.observations, stamp, target, calculation=self))

    def flows(self, scope, start, end, target):
        return self._get(('flows', scope, start, end, target), lambda: external_flows(
            self.journal, scope, self.observations, start, end, target, security_cash=self.securities().cash))


def convert(amount, source, target, stamp, observations):
    if amount is None:
        return None
    if amount == 0:
        return ZERO
    fx = rate(observations, source, target, stamp)
    return amount*fx if fx is not None else None


def confirmed_empty_before(book, stamp):
    events = selected_events(book)
    return bool(events and events[0].kind == 'opening' and not events[0].quantity
                and events[0].source == 'confirmed_zero_start' and stamp < events[0].date)


def account_starts_on(journal, account, stamp):
    owners = {p.id: p.account_id for p in journal.positions}
    events = [e for e in effective_events(journal)
              if e.account_id == account or e.other_account_id == account or owners.get(e.position_id) == account]
    openings = [e for e in events if e.kind == 'cash_open']
    return bool(openings and all(e.date == stamp for e in openings) and not any(e.date < stamp for e in events))


def opening_value_at(journal, scope, observations, stamp, target, *, calculation=None):
    """Account cash openings precede same-day account trades/fees/flows.

    Security openings remain end-of-day snapshots. Their derived cash postings
    already omit payments covered by a same-day account cash opening. Do not
    apply that omission to separately recorded account fees or external funding.
    """
    def value_for(members):
        return (calculation.value(members, stamp, target) if calculation else
                value_at(journal, members, observations, stamp, target))
    if not any(account_starts_on(journal, a, stamp) for a in scope.accounts):
        return value_for(scope)
    total, reasons = ZERO, list(scope.unresolved)
    if scope.positions:
        value = value_for(HistoryScope(scope.positions))
        reasons.extend(value.reasons)
        if value.amount is not None:
            total += value.amount
    for account in scope.accounts:
        if account_starts_on(journal, account, stamp):
            for event in effective_events(journal):
                if event.account_id != account or event.date != stamp or event.kind not in ('cash_open', 'debt_open'):
                    continue
                value = convert(event.amount * (-1 if event.kind == 'debt_open' else 1), event.currency, target, stamp, observations)
                if value is None:
                    reasons.append(Issue(f'{account}: missing opening {event.currency}/{target} FX on {stamp}', 'fx'))
                else:
                    total += value
        else:
            value = value_for(HistoryScope(accounts=(account,)))
            reasons.extend(value.reasons)
            if value.amount is not None:
                total += value.amount
    return AmountResult(None if reasons else total, tuple(dict.fromkeys(reasons)))


def capital_at_start(journal, scope, observations, stamp, target, *, calculation=None):
    """Snapshot cost seeds known gain; it is never a return-opening valuation.

    Account equity already includes cash, debt and claims. Only securities whose
    explicit opening is on this date replace market value with recorded cost.
    Saved cost conversions are evidence; latest-FX estimates are not history.
    """
    from portfolio_app.cost_basis import resolve_cost
    from portfolio_app.history_observations import ObservationFX
    total, reasons = ZERO, list(scope.unresolved)
    states = (calculation.securities(stamp) if calculation else replay_securities(journal.securities, as_of=stamp)).positions
    books = {b.position_id: b for b in journal.securities}
    for identity in scope.positions:
        book, state = books.get(identity), states.get(identity)
        if book and confirmed_empty_before(book, stamp):
            continue
        if state and state.boundary == stamp:
            if not state.quantity:
                continue
            if any(c.get('conversions', {}).get(target, {}).get('method') == 'estimate' for c in state.costs):
                reasons.append(Issue(f'{identity}: estimated FX is not historical acquisition evidence', 'fx'))
                continue
            cost = resolve_cost(list(state.costs), target, ObservationFX(observations))
            if cost.amount is None:
                reasons.append(Issue(f'{identity}: {cost.note}', 'transaction'))
            else:
                total += cost.exact_amount
        else:
            members = HistoryScope((identity,))
            value = (calculation.value(members, stamp, target) if calculation else
                     value_at(journal, members, observations, stamp, target))
            reasons.extend(value.reasons)
            if value.amount is not None:
                total += value.amount
    if scope.accounts:
        members = HistoryScope(accounts=scope.accounts)
        value = (calculation.opening(members, stamp, target) if calculation else
                 opening_value_at(journal, members, observations, stamp, target))
        reasons.extend(value.reasons)
        if value.amount is not None:
            total += value.amount
    return AmountResult(None if reasons else total, tuple(dict.fromkeys(reasons)))


def value_at(journal, scope, observations, stamp, target, *, calculation=None):
    """Exact dated equity: securities + cash - loans + futures/other claims."""
    if scope.unresolved:
        return AmountResult(None, scope.unresolved)
    supplied = lookup(observations, 'value', scope.key, stamp, currency=target)
    if supplied:
        return AmountResult(supplied.amount, evidence=(provenance(supplied, stamp),))
    securities = calculation.securities(stamp) if calculation else replay_securities(journal.securities, as_of=stamp)
    accounts = (calculation.accounts(stamp) if calculation else replay(journal, as_of=stamp)) if scope.accounts else None
    books = {b.position_id: b for b in journal.securities}
    total, reasons, evidence = ZERO, [], []

    def add(amount, denomination, identity):
        nonlocal total
        value = convert(amount, denomination, target, stamp, observations)
        if value is None:
            reasons.append(Issue(f'{identity}: missing value or {denomination}/{target} FX on {stamp}', 'fx'))
        else:
            total += value

    for identity in scope.positions:
        book = books.get(identity)
        state = securities.positions.get(identity)
        if book and confirmed_empty_before(book, stamp):
            continue
        if book is None or state is None or not state.boundary:
            reasons.append(Issue(f'{identity}: no opening quantity on {stamp}', 'transaction'))
            continue
        if state.issues:
            reasons.append(Issue(f'{identity}: resolve historical events before valuing the reconstructed holding', 'transaction'))
            continue
        if not state.quantity:
            continue
        price = price_at(observations, (identity, book.instrument_id), stamp, events=selected_events(book))
        if price is None:
            reasons.append(Issue(f'{identity}: missing price on {stamp}', 'price'))
        else:
            evidence.append(provenance(price, stamp))
            add(state.quantity * price.amount, price.currency, identity)
    events = effective_events(journal)
    for account in scope.accounts:
        openings = [e for e in events if e.account_id == account and e.kind == 'cash_open']
        if not openings or any(e.date > stamp for e in openings):
            reasons.append(Issue(f'{account}: missing opening cash coverage on {stamp}', 'transaction'))
        for (owner, currency), amount in accounts.cash.items():
            if owner == account:
                add(None if (owner, currency) in accounts.unknown_cash else amount, currency, account)
        for (owner, currency, loan), amount in accounts.loans.items():
            if owner == account:
                add(-amount, currency, loan)
        for identity, balance in accounts.positions.items():
            if balance.spec.account_id != account or not balance.quantity:
                continue
            price = price_at(observations, (identity, balance.contract.id), stamp)
            if price is None or price.currency != balance.contract.currency:
                reasons.append(Issue(f'{identity}: missing exact-contract mark on {stamp}', 'price'))
            else:
                evidence.append(provenance(price, stamp))
                add(balance.unsettled_pnl(price.amount), balance.contract.currency, identity)
        for identity, claim in accounts.pending.items():
            if claim.account_id == account:
                add(claim.amount, claim.currency, identity)
    return AmountResult(None if reasons else total, tuple(reasons), tuple(evidence))


def coverage_reasons(journal, scope, observations, start, end, *, fingerprint=None, security_fingerprints=None):
    """A quantity match cannot certify omitted offsetting events or income."""
    reasons = list(scope.unresolved)
    books = {b.position_id: b for b in journal.securities}
    fingerprint = fingerprint or history_fingerprint(journal)
    from portfolio_app.security_history import security_history_fingerprint
    security_fingerprints = {} if security_fingerprints is None else security_fingerprints
    def position_fingerprint(identity):
        if identity not in security_fingerprints:
            security_fingerprints[identity] = security_history_fingerprint(journal, identity)
        return security_fingerprints[identity]
    def matches(review):
        if review.journal_fingerprint == fingerprint:
            return True  # Existing whole-journal attestations retain their contract.
        return (not review.scope.accounts and len(review.scope.positions) == 1
                and review.journal_fingerprint == position_fingerprint(review.scope.positions[0]))
    reviewed = any(set(scope.positions) <= set(r.scope.positions) and set(scope.accounts) <= set(r.scope.accounts)
                   and not r.scope.unresolved and r.start <= start and r.end >= end
                   and matches(r) for r in observations.reviews)
    if not reviewed and not scope.accounts and len(scope.positions) == 1:
        from portfolio_app.security_history import accounting_fingerprint
        book = books.get(scope.positions[0])
        if book and book.reviews:
            review = book.reviews[-1]
            reviewed = (bool(review.complete_from) and review.complete_from <= start and review.complete_through >= end
                        and review.active_ids == book.active_ids
                        and (review.facts_fingerprint == position_fingerprint(book.position_id)
                             or review.facts_fingerprint == accounting_fingerprint(journal)))
    if not reviewed:
        reasons.append(Issue('Review complete scope membership, transactions, income and charges for this interval', 'coverage'))
    for identity in scope.positions:
        book = books.get(identity)
        if book is None or not book.active:
            reasons.append(Issue(f'{identity}: history is not activated', 'transaction'))
            continue
        events = selected_events(book)
        if not any(e.kind == 'opening' and e.date <= start for e in events) and not confirmed_empty_before(book, start):
            reasons.append(Issue(f'{identity}: missing opening snapshot', 'transaction'))
        if any(e.kind in ('opening', 'note', 'cost') and start < e.date <= end
               and not (e.kind == 'opening' and not e.quantity and e.source == 'confirmed_zero_start') for e in events):
            reasons.append(Issue(f'{identity}: unresolved event or replacement boundary in interval', 'transaction'))
        if any(e.kind in ('buy', 'sell', 'distribution') and start < e.date <= end and not e.charges_known for e in events):
            reasons.append(Issue(f'{identity}: charges have not been confirmed', 'transaction'))
    classes = dict(observations.classifications)
    for e in effective_events(journal):
        if e.account_id not in scope.accounts:
            continue
        if start < e.date <= end or (e.date == start and account_starts_on(journal, e.account_id, start)):
            if e.kind in ('cash_open', 'debt_open', 'snapshot') and e.date > start:
                reasons.append(Issue(f'{e.id}: account opening boundary inside interval', 'transaction'))
            if e.kind == 'cash_adjust' and classes.get(e.id, 'boundary') == 'boundary':
                reasons.append(Issue(f'{e.id}: classify or resolve the cash adjustment', 'transaction'))
    return tuple(dict.fromkeys(reasons))


def external_flows(journal, scope, observations, start, end, target, *, security_cash=None):
    flows, reasons = [], []
    members, accounts = set(scope.positions), set(scope.accounts)

    def add(identity, stamp, sequence, amount, currency, *, include_start=False, event_id='', expense=ZERO, income=ZERO):
        if not (start < stamp <= end or (include_start and start == stamp)):
            return
        value = convert(amount, currency, target, stamp, observations)
        reason = '' if value is not None else Issue(f'{identity}: missing cash amount or dated FX', 'transaction' if amount is None else 'fx')
        flows.append(ExternalFlow(identity, stamp, sequence, value, reason, event_id,
            convert(expense, currency, target, stamp, observations) or ZERO,
            convert(income, currency, target, stamp, observations) or ZERO, (identity,)))
        if reason:
            reasons.append(reason)

    for c in replay_securities(journal.securities).cash if security_cash is None else security_cash:
        asset_inside = c.position_id in members
        cash_inside = c.account_id in accounts and not c.external
        if asset_inside == cash_inside:
            continue
        # Asset-only purchases are contributions; account-only payments out are withdrawals.
        amount = None if c.amount is None else -c.amount if asset_inside else c.amount
        add('security:'+c.id, c.date, c.sequence, amount, c.currency, event_id='security:'+(c.event_id or c.id),
            expense=-c.amount if asset_inside and c.kind in ('fee', 'tax') and c.amount is not None else ZERO,
            income=c.amount if asset_inside and c.kind == 'distribution' and c.amount is not None else ZERO)
    for book in journal.securities:
        for e in selected_events(book):
            if e.kind in ('transfer_in', 'transfer_out') and book.position_id in members and start < e.date <= end:
                price = price_at(observations, (book.position_id, book.instrument_id), e.date, events=selected_events(book))
                amount = e.amount if e.amount is not None else e.quantity*price.amount if price else None
                denomination = e.currency if e.amount is not None else price.currency if price else e.currency
                add(e.id, e.date, e.sequence, None if amount is None else amount*(1 if e.kind == 'transfer_in' else -1), denomination)
            if e.kind != 'transfer' or not start < e.date <= end:
                continue
            source_inside, dest_inside = book.position_id in members, e.other_position_id in members
            if source_inside == dest_inside:
                continue
            price = price_at(observations, (book.position_id, book.instrument_id), e.date, events=selected_events(book))
            if price is None:
                reasons.append(Issue(f'{e.id}: supply transfer-date market value', 'transaction'))
                flows.append(ExternalFlow(e.id, e.date, e.sequence, None))
            else:
                add(e.id, e.date, e.sequence, e.quantity * price.amount * (-1 if source_inside else 1), price.currency)
    classes = dict(observations.classifications)
    for e in effective_events(journal):
        inside, other = e.account_id in accounts, e.other_account_id in accounts
        initial = (inside and account_starts_on(journal, e.account_id, start)
                   or other and account_starts_on(journal, e.other_account_id, start))
        if e.kind == 'cash_flow' and inside:
            add(e.id, e.date, e.sequence, e.amount, e.currency, include_start=initial)
        elif e.kind == 'cash_adjust' and inside:
            classification = classes.get(e.id, 'boundary')
            if classification == 'external':
                add(e.id, e.date, e.sequence, e.amount, e.currency, include_start=initial)
            elif (start < e.date <= end or initial and e.date == start) and (classification == 'boundary' or classification == 'income' and e.amount < 0 or classification == 'expense' and e.amount > 0):
                reasons.append(Issue(f'{e.id}: unresolved cash adjustment', 'transaction'))
        elif e.kind in ('transfer', 'fx') and inside != (other or (e.kind == 'fx' and not e.other_account_id and inside)):
            if inside:
                add(e.id, e.date, e.sequence, -e.amount, e.currency, include_start=initial)
            elif other:
                add(e.id, e.date, e.sequence, e.other_amount if e.kind == 'fx' else e.amount,
                    e.other_currency if e.kind == 'fx' else e.currency, include_start=initial)
    grouped = {}
    for flow in flows:
        key = (flow.event_id or flow.id, flow.date)
        if key not in grouped:
            grouped[key] = replace(flow, id=key[0])
        else:
            previous = grouped[key]
            grouped[key] = replace(previous,
                amount=previous.amount+flow.amount if previous.amount is not None and flow.amount is not None else None,
                expense=previous.expense+flow.expense, income=previous.income+flow.income,
                reason=previous.reason or flow.reason, postings=(*previous.postings, *flow.postings))
    return tuple(sorted(grouped.values(), key=lambda f: (f.date, f.sequence, f.id))), tuple(dict.fromkeys(reasons))


def xirr(opening, ending, flows, start, end):
    """Compatibility interface for structured, globally isolated XIRR results."""
    from portfolio_app.history_irr import solve_xirr
    result = solve_xirr(opening, ending, flows, start, end)
    return result.value, result.reason


def execution_boundary(journal, scope, flow, observations, target):
    """Standalone trade-time quantity × execution price, never a daily close."""
    if scope.accounts or len(scope.positions) != 1 or scope.unresolved:
        return None
    book = next((b for b in journal.securities if b.position_id == scope.positions[0]), None)
    if book is None:
        return None
    events = selected_events(book)
    event = next((e for e in events if 'security:'+e.id == flow.event_id and e.date == flow.date), None)
    if event is None or event.kind not in ('buy', 'sell') or not event.charges_known or event.gross is None:
        return None
    if sum((e.date, e.sequence) == (event.date, event.sequence) and e.kind != 'opening' for e in events) != 1:
        return None
    earlier = tuple(e.id for e in events if (e.date, e.sequence) < (event.date, event.sequence)
                    or e.kind == 'opening' and e.date <= event.date)
    state = replay_securities((replace(book, active_ids=earlier),)).positions.get(book.position_id)
    if state is None or not state.boundary or state.issues:
        return None
    price = convert(event.gross/event.quantity, event.currency, target, event.date, observations)
    if price is None:
        return None
    before = state.quantity*price
    after = (state.quantity + event.quantity*(1 if event.kind == 'buy' else -1))*price
    return AmountResult(before), AmountResult(after)


def time_weighted(opening, ending, flows, observations, scope, target, *, journal=None):
    ordered = [(flow.date, flow.sequence) for flow in flows if flow.amount or flow.expense or flow.income]
    if len(ordered) != len(set(ordered)):
        return None, Issue('Assign distinct ordering to same-day external flows before calculating exact time-weighted returns', 'ordering')
    previous, factor = opening, Decimal(1)
    invested = opening > 0
    if previous < 0 or ending < 0:
        return None, 'Time-weighted return is unavailable across negative equity'
    for flow in flows:
        if flow.amount == 0 and not flow.expense and not flow.income:
            continue
        before = lookup(observations, 'value', scope.key, flow.date, side='before', boundary=flow.id, currency=target)
        after = lookup(observations, 'value', scope.key, flow.date, side='after', boundary=flow.id, currency=target)
        if (before is None or after is None) and journal is not None:
            derived = execution_boundary(journal, scope, flow, observations, target)
            if derived is not None:
                before, after = before or derived[0], after or derived[1]
        if before is None or after is None:
            return None, Issue(f'{flow.id}: supply exact before/after flow valuations', 'boundary')
        if before.amount < 0 or after.amount < 0 or abs(after.amount-before.amount-flow.amount+flow.expense-flow.income) > Decimal('.01'):
            return None, Issue(f'{flow.id}: flow valuations do not reconcile with nonnegative equity', 'boundary')
        if previous:
            factor *= before.amount/previous
        elif before.amount:
            return None, f'{flow.id}: value appeared without recorded funding'
        denominator = before.amount + max(flow.amount, ZERO)
        numerator = after.amount + max(-flow.amount, ZERO)
        if denominator:
            factor *= numerator/denominator
        elif numerator:
            return None, Issue('Event has income without a supported funded interval', 'funding')
        invested |= denominator > 0
        previous = after.amount
        invested |= previous > 0
    if not invested or (not previous and ending):
        return None, 'Time-weighted return needs a funded interval with supported boundaries'
    result = float(factor * (ending/previous if previous else 1)-1)
    return (result, '') if math.isfinite(result) else (None, 'Return exceeds supported numeric range')


def known_negative_equity_dates(journal, scope, observations, start, end, target='EUR', *, valuations=None):
    """Known total equity at recorded dates, not individual negative balances.

    Check event-day closes as well as supplied valuations and price/FX dates.
    Missing valuations stay unknown; never fill them to establish eligibility.
    Callers rendering many prefixes can reuse these dates and existing values.
    """
    from portfolio_app.history_observations import active_observations, index_observations
    observations = index_observations(observations)
    active = active_observations(observations)
    owners = {p.id: p.account_id for p in journal.positions}
    subjects = set(scope.positions) | {b.instrument_id for b in journal.securities if b.position_id in scope.positions}
    subjects |= {p.id for p in journal.positions if p.account_id in scope.accounts}
    subjects |= {p.contract_id for p in journal.positions if p.account_id in scope.accounts}
    dates = {e.date for b in journal.securities if b.position_id in scope.positions for e in selected_events(b)}
    dates |= {e.date for e in effective_events(journal)
              if e.account_id in scope.accounts or e.other_account_id in scope.accounts
              or owners.get(e.position_id) in scope.accounts}
    dates |= {o.date for o in active if o.kind == 'fx' or o.kind == 'price' and o.subject in subjects
              or o.kind == 'value' and o.subject == scope.key}
    negative = {o.date for o in active if o.kind == 'value' and o.subject == scope.key
                and o.currency == target and start <= o.date <= end and o.amount < 0}
    for stamp in sorted(dates):
        if not start <= stamp <= end or stamp in negative:
            continue
        value = (valuations[stamp] if valuations is not None and stamp in valuations else
                 value_at(journal, scope, observations, stamp, target))
        if value.amount is not None and value.amount < 0:
            negative.add(stamp)
    return tuple(sorted(negative))


def calculate_history(journal, scope, observations, start, end, target='EUR', *, returns=True, annualized=True, negative_dates=None, calculation=None, method='daily'):
    if method not in ('daily', 'exact'):
        raise ValueError('Unknown time-weighted return method')
    days = (date.fromisoformat(end)-date.fromisoformat(start)).days
    if days < 0:
        from portfolio_app.holdings import DataError
        raise DataError('History end must not precede its start.')
    calculation = calculation or HistoryCalculation(journal, observations)
    observations = calculation.observations
    opening, ending = calculation.opening(scope, start, target), calculation.value(scope, end, target)
    coverage = coverage_reasons(journal, scope, observations, start, end, fingerprint=calculation.fingerprint,
                                security_fingerprints=calculation._security_fingerprints)
    flows, missing_flows = calculation.flows(scope, start, end, target)
    flow_reasons = tuple(dict.fromkeys((*coverage, *missing_flows)))
    net = sum((f.amount for f in flows), ZERO) if not flow_reasons else None
    capital = calculation.capital(scope, start, target)
    invested = capital.amount + net if capital.amount is not None and net is not None else None
    gain = ending.amount - opening.amount - net if ending.amount is not None and opening.amount is not None and net is not None else None
    total_gain = ending.amount - invested if ending.amount is not None and invested is not None else None
    opening_gain = opening.amount - capital.amount if opening.amount is not None and capital.amount is not None else None
    reasons = tuple(dict.fromkeys((*opening.reasons, *ending.reasons, *flow_reasons)))
    mwr = twr = None
    mwr_reasons = twr_reasons = reasons
    if gain is not None and returns:
        # Empty inception and liquidation are legitimate. Negative equity remains
        # outside this return contract; zeroes are handled by each method.
        if negative_dates is None:
            negative_dates = known_negative_equity_dates(journal, scope, observations, start, end, target)
        negative = any(start <= stamp <= end for stamp in negative_dates)
        if negative or opening.amount < 0 or ending.amount < 0:
            mwr_reasons = twr_reasons = (Issue('Percentage return is unavailable across negative equity', 'negative_equity'),)
        else:
            if annualized:
                mwr, reason = xirr(opening.amount, ending.amount, flows, start, end)
                mwr_reasons = (reason,) if reason else ()
            if method == 'exact':
                twr, reason = time_weighted(opening.amount, ending.amount, flows, observations, scope, target, journal=journal)
            else:
                from portfolio_app.history_twr import daily_time_weighted
                twr, reason = daily_time_weighted(calculation, scope, start, end, target, opening.amount, ending.amount, flows)
            twr_reasons = (reason,) if reason else ()
    return PersonalPerformance(opening.amount, ending.amount, net, invested, gain, mwr, twr, days,
        tuple(dict.fromkeys((*opening.reasons, *ending.reasons))), flow_reasons, mwr_reasons, twr_reasons,
        capital.amount, opening_gain, total_gain, capital.reasons, ending.evidence, method)
