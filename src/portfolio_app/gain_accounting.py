"""Known monetary gain for shared current and historical presentations.

Securities and accounts form disjoint accounting components. A security's cash
payments cross the asset-only boundary and the account-only boundary with opposite
signs. Adding their gains therefore cancels internal movements, including income
already held in cash. Futures are valued inside their accounts, never added again
from the funded-investment display projection.
"""
from dataclasses import dataclass
from decimal import Decimal
import math

from portfolio_app.account_journal import effective_events
from portfolio_app.history_observations import HistoryScope
from portfolio_app.personal_performance import calculate_history, external_flows, value_at
from portfolio_app.security_history import replay_securities, selected_events

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
    def review_required_only(self):
        return (self.amount is not None and self.covered == self.count and bool(self.reasons)
                and all(reason.startswith('Review complete scope') for reason in self.reasons))

    @property
    def coverage_note(self):
        if self.amount is None:
            return 'Gain unavailable'
        if self.review_required_only:
            return 'History not confirmed'
        return 'Incomplete gain history' if self.partial else 'Complete gain history'

    @property
    def coverage_help(self):
        if self.review_required_only:
            return ('Gain uses recorded transactions. Review the selected transactions and confirm history completeness; '
                    'applying transactions to the portfolio does not confirm completeness.')
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


def scope_dates(journal, scope):
    owners = {p.id: p.account_id for p in journal.positions}
    dates = [e.date for b in journal.securities if b.position_id in scope.positions
             for e in selected_events(b) if e.date]
    dates += [e.date for e in effective_events(journal)
              if e.account_id in scope.accounts or e.other_account_id in scope.accounts
              or owners.get(e.position_id) in scope.accounts]
    return sorted(set(dates))


def current_component_values(valued, journal):
    """Consume the raw valuation projection, retaining its quote/FX provenance.

    These values are current inputs, not newly saved historical observations.
    Account equity excludes securities (which have their own component).
    """
    securities = {b.position_id for b in journal.securities}
    result = {row.position_id: amount(row.current_value_reporting) for row in valued.itertuples()}
    for identity, state in replay_securities(journal.securities).positions.items():
        if not state.quantity:
            result.setdefault(identity, ZERO)
    for account in journal.accounts:
        rows = valued.loc[valued.get('account_id', valued.position_id.map(lambda _: '')).eq(account.id)
                          & valued.get('journal_managed', valued.position_id.map(lambda _: False)).fillna(False).eq(True)
                          & ~valued.position_id.isin(securities)]
        values = [amount(v) for v in rows.current_value_reporting]
        result['account:'+account.id] = sum(values, ZERO) if values and all(v is not None for v in values) else None
    return result


def summarize_known_gain(journal, scope, observations, end, target='EUR', *, current_values=None, summary_rows=None):
    """Sum supported components; missing evidence never becomes a zero gain.

    Current summary rows supply only unrealized gain, with an explicit limitation.
    A complete history replaces that fallback, including when quantity is zero.
    Historical callers never project current quantities or costs backwards.
    """
    books = {b.position_id: b for b in journal.securities}
    rows = {} if summary_rows is None else summary_rows.set_index('position_id').to_dict('index')
    parts = [(identity, HistoryScope((identity,))) for identity in scope.positions
             if not (rows.get(identity, {}).get('shares') == 0 and identity not in books)]
    parts += [('account:'+identity, HistoryScope(accounts=(identity,))) for identity in scope.accounts]
    gains, values, capitals, reasons = [], [], [], list(scope.unresolved)
    for identity, member in parts:
        dates = scope_dates(journal, member)
        value = (current_values.get(identity) if current_values is not None else
                 value_at(journal, member, observations, end, target).amount)
        values.append(value)
        gain = capital = None
        book = books.get(identity)
        if dates and dates[0] <= end:
            result = calculate_history(journal, member, observations, dates[0], end, target, returns=False)
            capital = result.invested_capital
            # A missing completeness attestation is different from an explicitly
            # unknown amount. Retain the subtotal of recorded facts, clearly
            # partial; never enable a percentage return through this fallback.
            review_only = result.flow_reasons and all(r.startswith('Review complete scope') for r in result.flow_reasons)
            if capital is None and result.opening_capital is not None and review_only:
                flows, missing = external_flows(journal, member, observations, dates[0], end, target)
                if not missing:
                    capital = result.opening_capital + sum((f.amount for f in flows), ZERO)
                    reasons.extend(result.flow_reasons)
            if value is not None and capital is not None:
                gain = value-capital
            else:
                reasons.extend(f'{identity}: {r}' for r in (*result.flow_reasons, *result.capital_reasons))
                if value is not None and result.opening_value is not None and result.net_contributions is not None:
                    gain = value-result.opening_value-result.net_contributions
                    reasons.append(f'{identity}: only post-snapshot gain is known; opening acquisition cost is unavailable')
            if book:
                openings = [e for e in selected_events(book) if e.kind == 'opening']
                if openings and openings[0].quantity:
                    reasons.append(f'{identity}: earlier realized gains and income before the snapshot are unknown')
            else:
                reasons.append(f'{identity}: account performance begins at its recorded opening equity')
        if gain is None:
            # Summary fallback supports current unrealized gain only. Do not add
            # it to a reconstructed total or invent historical summary holdings.
            row = rows.get(identity, {})
            gain = amount(row.get('unrealized_gain_reporting'))
            if gain is not None and row.get('cost_estimated') is True:
                reasons.append(f'{identity}: unrealized gain uses an estimated cost conversion')
            if book and book.active:
                from portfolio_app.gain_components import security_gain_components
                components = security_gain_components(journal, identity, observations, end, target, current_value=value)
                events = [e for e in selected_events(book) if e.date <= end]
                kinds = {e.kind for e in events}
                known_parts = [gain if gain is not None else components.unrealized]
                if 'sell' in kinds:
                    known_parts.append(components.realized)
                if 'distribution' in kinds:
                    known_parts.append(components.income)
                if components.expenses:
                    known_parts.append(-components.expenses)
                supported = [part for part in known_parts if part is not None]
                gain = sum(supported, ZERO) if supported else None
                reasons.extend(components.reasons)
            capital = None
            reasons.append(f'{identity}: only individually supported gain components are included' if gain is not None
                           else f'{identity}: gain is unavailable')
        if gain is not None:
            gains.append(gain)
        capitals.append(capital)
    known_value = sum(values, ZERO) if values and all(v is not None for v in values) else None
    known_capital = sum(capitals, ZERO) if capitals and all(v is not None for v in capitals) and not scope.unresolved else None
    return GainSummary(sum(gains, ZERO) if gains else None, known_value, known_capital,
                       len(gains), len(parts), tuple(dict.fromkeys(reasons)), end, target)
