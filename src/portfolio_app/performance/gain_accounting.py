"""Known monetary gain for shared current and historical presentations.

Securities and accounts form disjoint accounting components. A security's cash
payments cross the asset-only boundary and the account-only boundary with opposite
signs. Adding their gains therefore cancels internal movements, including income
already held in cash. Futures are valued inside their accounts, never added again
from the funded-investment display projection.
"""
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
import math

from portfolio_app.ledger.account_journal import effective_events
from portfolio_app.performance.history_observations import HistoryScope, lookup
from portfolio_app.performance.personal_performance import HistoryCalculation
from portfolio_app.ledger.security_history import SecurityBook, replay_securities, selected_events, affected_positions

ZERO = Decimal(0)


@dataclass(frozen=True)
class GainSummary:
    amount: Decimal | None
    value: Decimal | None
    capital: Decimal | None
    covered: int
    count: int
    reasons: tuple[str, ...]
    as_of: str
    currency: str

    @property
    def partial(self):
        return bool(self.reasons) or self.covered != self.count

    @property
    def status(self):
        return 'Unavailable' if self.amount is None else 'Partial' if self.partial else 'Complete'

    @property
    def coverage_note(self):
        if self.amount is None:
            return 'Gain unavailable'
        if any('Estimated acquisition cost' in r or 'Estimated monetary valuation' in r for r in self.reasons):
            return 'Estimated gain'
        return 'Incomplete gain history' if self.partial else 'Complete gain history'

    @property
    def coverage_help(self):
        if any('Estimated acquisition cost' in r or 'Estimated monetary valuation' in r for r in self.reasons):
            return 'Gain includes imported estimates for acquisition costs or transaction valuations.'
        if self.amount is None:
            return 'Available values and costs are insufficient to calculate a gain.'
        return ('Gain uses available costs and recorded transactions. Missing history or inputs '
                'may leave gains, income or costs unaccounted for.') if self.partial else (
                    'Gain includes recorded transactions, income and costs.')

    @property
    def coverage(self):
        return f'{self.covered} of {self.count}'


def amount(value):
    return Decimal(str(value)) if value is not None and math.isfinite(value) else None


class ScopeEventDates:
    """Dates indexed by the position or cash account affected by each effective event."""
    def __init__(self, journal, account_events, book_events):
        self.positions = defaultdict(set)
        self.accounts = defaultdict(set)
        owners = {p.id: p.account_id for p in journal.positions}
        openings = {(e.account_id, e.currency): e.date for e in account_events if e.kind == 'cash_open'}

        def affects_cash(account, currency, stamp, value_date=''):
            opening = openings.get((account, currency))
            # Match derived postings: the opening includes earlier settled payments.
            return not opening or stamp > opening or bool(value_date and value_date != stamp)

        for book, events in book_events:
            for event in events:
                if event.date:
                    for identity in affected_positions(book, event):
                        self.positions[identity].add(event.date)
                    if (book.account_id and event.kind in {'buy', 'sell', 'distribution', 'fee', 'tax'}
                            and affects_cash(book.account_id, event.currency, event.date, event.value_date)):
                        self.accounts[book.account_id].add(event.date)
                if event.kind != 'opening':
                    for charge in event.charges:
                        stamp = charge.date or event.date
                        self.positions[book.position_id].add(stamp)
                        if book.account_id and not charge.external and affects_cash(book.account_id, charge.currency, stamp):
                            self.accounts[book.account_id].add(stamp)
        for event in account_events:
            for account in (event.account_id, event.other_account_id, owners.get(event.position_id)):
                if account:
                    self.accounts[account].add(event.date)

    def dates(self, scope):
        dates = set()
        for identity in scope.positions:
            dates.update(self.positions.get(identity, ()))
        for account in scope.accounts:
            dates.update(self.accounts.get(account, ()))
        return sorted(dates)


def scope_dates(journal, scope):
    """Every effective balance/cash date, including linked securities and fees."""
    books = ((book, selected_events(book)) for book in journal.securities)
    return ScopeEventDates(journal, effective_events(journal), books).dates(scope)


def current_component_values(valued, journal, *, calculation=None):
    """Consume the raw valuation projection, retaining its quote/FX provenance.

    These values are current inputs, not newly saved historical observations.
    Account equity excludes securities (which have their own component).
    """
    securities = {b.position_id for b in journal.securities}
    result = {row.position_id: amount(row.current_value_reporting) for row in valued.itertuples()}
    states = calculation.securities() if calculation else replay_securities(journal.securities)
    for identity, state in states.positions.items():
        if not state.quantity:
            result.setdefault(identity, ZERO)
    for account in journal.accounts:
        rows = valued.loc[valued.get('account_id', valued.position_id.map(lambda _: '')).eq(account.id)
                          & valued.get('journal_managed', valued.position_id.map(lambda _: False)).fillna(False).eq(True)
                          & ~valued.position_id.isin(securities)]
        values = [amount(v) for v in rows.current_value_reporting]
        result['account:'+account.id] = sum(values, ZERO) if values and all(v is not None for v in values) else None
    return result


@dataclass(frozen=True)
class _GainMember:
    identity: str
    scope: HistoryScope
    book: SecurityBook | None
    first_date: str | None
    has_opening_quantity: bool
    first_disposal: str | None
    first_income: str | None


def _gain_members(calculation, scope):
    # Scope membership and the selected ledger are fixed for this calculation.
    def member(identity, boundary):
        events = calculation._events.get(identity, ())
        opening = next((e for e in events if e.kind == 'opening'), None)
        dates = calculation.event_dates(boundary)
        return _GainMember(identity, boundary, calculation._books.get(identity), dates[0] if dates else None,
            bool(opening and opening.quantity),
            next((e.date for e in events if e.kind in ('sell', 'swap')), None),
            next((e.date for e in events if e.kind in ('distribution', 'reward')), None))

    members = [member(identity, HistoryScope((identity,))) for identity in scope.positions]
    members.extend(member('account:'+identity, HistoryScope(accounts=(identity,))) for identity in scope.accounts)
    return tuple(members)


def summarize_known_gain(journal, scope, observations, end, target='EUR', *, current_values=None, summary_rows=None, calculation=None, _member_closes=None):
    """Sum supported components; missing evidence never becomes a zero gain.

    Current summary rows supply only unrealized gain, with an explicit limitation.
    A complete history replaces that fallback, including when quantity is zero.
    Historical callers never project current quantities or costs backwards.
    """
    calculation = calculation or HistoryCalculation(journal, observations)
    if current_values is not None or summary_rows is not None:
        _member_closes = None  # Chart subtotals correspond to the complete historical membership.
    return _calculate_gain(calculation, scope, end, target, estimate_notes=_estimate_notes(calculation, scope, end),
                           member_closes=_member_closes, current_values=current_values, summary_rows=summary_rows)


def chart_gain_amounts(calculation, scope, end, target, *, member_closes=None):
    """Capital and monetary gain for a plotted date. Estimate explanations belong to its summary."""
    return _calculate_gain(calculation, scope, end, target, estimate_notes=None, member_closes=member_closes)


def _estimate_notes(calculation, scope, end):
    estimate_notes = []
    estimated = calculation._get(('gain_estimates',), lambda: tuple((b, e) for b in calculation.journal.securities
        for e in calculation._events[b.position_id] if e.estimated or e.valuation_quality == 'estimated'
        or any(p.get('quality') == 'estimated' for p in e.costs)))
    for book, event in estimated:
        if not affected_positions(book, event).intersection(scope.positions) and book.account_id not in scope.accounts:
            continue
        if event.date > end:
            continue
        if any(p.get('quality') == 'estimated' for p in event.costs):
            estimate_notes.append('Estimated acquisition cost retained from supplied evidence')
        if event.valuation_quality == 'estimated' or event.estimated:
            estimate_notes.append('Estimated monetary valuation retained from supplied evidence')
    if scope.positions and any(e.valuation_quality == 'estimated' or any(p.get('quality') == 'estimated' for p in e.costs)
                               for _, e in estimated):
        states = calculation.securities(end).positions
        if any(p.get('quality') == 'estimated' for identity in scope.positions
               if identity in states for p in states[identity].costs):
            estimate_notes.append('Estimated acquisition cost retained from supplied evidence')
    return estimate_notes


def _member_values(calculation, scope, parts, end, target, closes):
    """Apply a supplied member close to that member alone, in the original scope order."""
    overrides = calculation._get(('gain_member_overrides', scope), lambda: tuple(
        (i, part.scope.key) for i, part in enumerate(parts)
        if ('value', part.scope.key) in calculation.observations.scope_rows))
    if overrides:
        closes = list(closes)
        for i, subject in overrides:
            mark = lookup(calculation.observations, 'value', subject, end, currency=target)
            if mark is not None:
                closes[i] = mark.amount
    return closes


def _calculate_gain(calculation, scope, end, target, *, estimate_notes, member_closes, current_values=None, summary_rows=None):
    # None requests just the plotted (capital, gain) pair. An empty list is a
    # detailed summary whose ledger has no supplied estimates.
    detailed = estimate_notes is not None
    scope_basis = None
    # When a movement connects positions, value the selected boundary
    # as a whole before component fallbacks. Missing internal transfer marks must
    # not prevent cancellation or make a known portfolio gain partial.
    if len(scope.positions) + len(scope.accounts) > 1 and not scope.unresolved:
        dates = calculation.event_dates(scope)
        if dates and dates[0] <= end:
            scope_basis = calculation.period_capital(scope, dates[0], end, target)
            value = calculation.close(scope, end, target).amount
            if scope_basis.invested_capital is not None:
                if current_values is not None:
                    values = [current_values.get(i) for i in scope.positions]
                    values += [current_values.get('account:'+a) for a in scope.accounts]
                    value = sum(values, ZERO) if all(v is not None for v in values) else None
                if value is not None:
                    capital = scope_basis.invested_capital
                    if not detailed:
                        return capital, value-capital
                    count = len(scope.positions)+len(scope.accounts)
                    reasons = list(estimate_notes)
                    first_nonzero_opening = calculation._get(('first_gain_opening', scope.positions), lambda: min(
                        (e.date for identity in scope.positions for e in calculation._events.get(identity, ())
                         if e.kind == 'opening' and e.quantity), default=None))
                    if first_nonzero_opening is not None and first_nonzero_opening <= end:
                        reasons.append('Earlier realized gains and income before opening snapshots are unknown')
                    return GainSummary(value-capital, value, capital, count, count, tuple(reasons), end, target)
    rows = {} if summary_rows is None else summary_rows.set_index('position_id').to_dict('index')
    parts = calculation._get(('gain_members', scope), lambda: _gain_members(calculation, scope))
    if member_closes is not None:
        member_closes = _member_values(calculation, scope, parts, end, target, member_closes)
        previous = calculation._last_chart_member_capitals
        if not detailed and previous is not None:
            prior_basis, capitals = previous
            if scope_basis is prior_basis and len(member_closes) == len(capitals) and None not in member_closes:
                # Subtract in member order. Subtracting the two totals can round differently.
                gains = (value - cost for value, cost in zip(member_closes, capitals, strict=True))
                return sum(capitals, ZERO), sum(gains, ZERO)
    if rows:
        parts = [part for part in parts if part.book or part.scope.accounts or rows.get(part.identity, {}).get('shares') != 0]
    gains, values, capitals = [], [], []
    reasons = [*scope.unresolved, *estimate_notes] if detailed else []
    for index, part in enumerate(parts):
        identity, member, book = part.identity, part.scope, part.book
        has_history = part.first_date is not None and part.first_date <= end
        # Historical valuations also validate dated FX when a current quote overrides them.
        if member_closes is None:
            historical_value = calculation.close(member, end, target).amount if has_history or current_values is None else None
        else:
            historical_value = member_closes[index]
        value = historical_value if current_values is None else current_values.get(identity)
        values.append(value)
        gain = capital = None
        if has_history:
            basis = calculation.period_capital(member, part.first_date, end, target)
            capital = basis.invested_capital
            if value is not None and capital is not None:
                gain = value-capital
            else:
                if detailed:
                    reasons.extend(f'{identity}: {r}' for r in (*basis.flow_reasons, *basis.opening_capital.reasons))
                if (value is not None and basis.opening_value.amount is not None and basis.net_contributions is not None
                        and not any(getattr(r, 'kind', '') == 'cost_basis' for r in basis.opening_capital.reasons)):
                    gain = value-basis.opening_value.amount-basis.net_contributions
                    if detailed:
                        reasons.append(f'{identity}: only post-snapshot gain is known; opening acquisition cost is unavailable')
            if detailed:
                if book and part.has_opening_quantity:
                    reasons.append(f'{identity}: earlier realized gains and income before the snapshot are unknown')
                elif not book:
                    reasons.append(f'{identity}: account performance begins at its recorded opening equity')
        if gain is None:
            # Summary fallback supports current unrealized gain only. Do not add
            # it to a reconstructed total or invent historical summary holdings.
            row = rows.get(identity, {})
            gain = amount(row.get('unrealized_gain_reporting'))
            if gain is not None and row.get('cost_estimated') is True:
                reasons.append(f'{identity}: unrealized gain uses an estimated cost conversion')
            if book and book.active:
                from portfolio_app.performance.gain_components import security_gain_components
                components = security_gain_components(calculation.journal, identity, calculation.observations, end,
                                                     target, current_value=value, calculation=calculation)
                known_parts = [gain if gain is not None else components.unrealized]
                if part.first_disposal is not None and part.first_disposal <= end:
                    known_parts.append(components.known_realized)
                if part.first_income is not None and part.first_income <= end:
                    known_parts.append(components.known_income)
                if components.known_expenses:
                    known_parts.append(-components.known_expenses)
                supported = [part for part in known_parts if part is not None]
                gain = sum(supported, ZERO) if supported else None
                if detailed:
                    reasons.extend(components.reasons)
            capital = None
            if detailed:
                reasons.append(f'{identity}: only individually supported gain components are included' if gain is not None
                               else f'{identity}: gain is unavailable')
        if gain is not None:
            gains.append(gain)
        capitals.append(capital)
    known_capital = sum(capitals, ZERO) if capitals and all(v is not None for v in capitals) and not scope.unresolved else None
    known_gain = sum(gains, ZERO) if gains else None
    if not detailed:
        if member_closes is not None and scope_basis is not None and known_capital is not None:
            # The scope basis is shared only within one interval of its ledger,
            # linked books and receipts. All member bases are stable inside it.
            calculation._last_chart_member_capitals = scope_basis, tuple(capitals)
        return known_capital, known_gain
    known_value = sum(values, ZERO) if values and all(v is not None for v in values) else None
    return GainSummary(known_gain, known_value, known_capital,
                       len(gains), len(parts), tuple(dict.fromkeys(reasons)), end, target)
