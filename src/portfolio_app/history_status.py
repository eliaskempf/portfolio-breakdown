"""Per-position history diagnostics using the existing coverage and metric rules."""
from dataclasses import dataclass
from datetime import date

from portfolio_app.gain_accounting import scope_dates
from portfolio_app.history_observations import HistoryScope, active_observations
from portfolio_app.history_view import history_view
from portfolio_app.personal_performance import HistoryCalculation, coverage_reasons
from portfolio_app.security_history import current_records


@dataclass(frozen=True)
class HistoryStatus:
    position_id: str
    transactions: str
    market: str
    action: str
    target: str
    screen: str
    scope: HistoryScope
    reasons: tuple[str, ...] = ()
    needs_attention: bool = True
    start: str = ''
    end: str = ''


def position_history_statuses(holdings, journal, observations, *, target='EUR', today=None):
    """Assess saved evidence once per scope; do not download or infer missing facts."""
    today = today or date.today()
    end = today.isoformat()
    calculation = HistoryCalculation(journal, observations)
    books = {book.position_id: book for book in journal.securities}
    owners = {item.position_id: item.account_id for item in journal.cash_links}
    owners.update({item.id: item.account_id for item in (*journal.positions, *journal.investments)})
    account_ids = {a.id for a in journal.accounts}
    latest_prices, latest_values = {}, {}
    for observation in active_observations(calculation.observations):
        if observation.date <= end and observation.side == 'close':
            index = latest_prices if observation.kind == 'price' else latest_values if observation.kind == 'value' else None
            if index is not None:
                index[observation.subject] = max(index.get(observation.subject, ''), observation.date)
    summaries, results = {}, []
    for row in holdings.itertuples():
        identity = row.position_id
        book = books.get(identity)
        account = owners.get(identity)
        if not book and not account and getattr(row, 'account_id', None) in account_ids:
            account = row.account_id
        account_managed = bool(account) or getattr(row, 'instrument_type', '') in {'cash', 'future'}
        scope = HistoryScope(accounts=(account,)) if account else HistoryScope((identity,))
        route = 'accounts' if account_managed else 'transactions'
        if not book and not account:
            results.append(HistoryStatus(identity, 'No account history' if account_managed else 'No history',
                '—', 'Open account journal' if account_managed else 'Add transactions', route, 'list', scope))
            continue
        if book and not book.active:
            recorded = bool(current_records(book))
            results.append(HistoryStatus(identity, 'Not applied' if recorded else 'No history', '—',
                'Review transactions' if recorded else 'Add transactions', route, 'review' if recorded else 'list', scope))
            continue
        dates = scope_dates(journal, scope)
        if not dates:
            results.append(HistoryStatus(identity, 'No account history', '—', 'Open account journal', 'accounts', 'list', scope))
            continue
        pending = False
        if book:
            pending = any(event.id not in book.active_ids for event in current_records(book))
        if scope not in summaries:
            subjects = set(scope.positions)
            subjects.update(b.instrument_id for b in journal.securities if b.position_id in scope.positions)
            subjects.update(p.id for p in journal.positions if p.account_id in scope.accounts)
            subjects.update(p.contract_id for p in journal.positions if p.account_id in scope.accounts)
            latest = max([dates[0], *(stamp for stamp in dates if stamp <= end),
                          *(latest_prices[subject] for subject in subjects if subject in latest_prices),
                          latest_values.get(scope.key, '')])
            # Cash/account balances can be valued today without a market quote.
            if scope.accounts and not subjects:
                latest = end
            view = history_view(journal, scope, observations, 'All', today=date.fromisoformat(latest), target=target,
                                include_points=False, calculation=calculation)
            # Check the recorded interval, not invented weekend/today prices.
            coverage = coverage_reasons(journal, scope, calculation.observations, dates[0], latest,
                fingerprint=calculation.fingerprint, security_fingerprints=calculation._security_fingerprints)
            reasons = [*coverage, *view.all_result.value_reasons, *view.all_result.flow_reasons,
                       *view.all_result.capital_reasons, *view.all_result.money_weighted_reasons,
                       *view.all_result.time_weighted_reasons]
            # Interior gaps can be joined in charts and need not block endpoint
            # metrics. Flag required inputs and unsupported latest evidence.
            if latest > view.all_end:
                reasons.extend(calculation.value(scope, latest, target).reasons)
            summaries[scope] = view, coverage, tuple(dict.fromkeys(reasons)), latest
        view, coverage, reasons, latest = summaries[scope]
        unconfirmed = any(reason.startswith('Review complete scope') for reason in coverage)
        transaction_issues = [reason for reason in coverage if not reason.startswith('Review complete scope')]
        cost_issues = [reason for reason in view.all_result.capital_reasons if 'FX' not in reason]
        transaction_issues.extend(cost_issues)
        transactions = ('Unapplied changes' if pending else 'Needs transaction details' if transaction_issues else
                        'Not confirmed' if unconfirmed else 'Confirmed')
        missing_prices = any('missing price' in reason or 'missing exact-contract mark' in reason for reason in reasons)
        missing_fx = any(' FX' in reason or 'dated FX' in reason for reason in reasons)
        flow_values = any('before/after flow valuations' in reason or 'flow valuations do not reconcile' in reason for reason in reasons)
        market = ('Prices and FX missing' if missing_prices and missing_fx else 'Prices missing' if missing_prices else
                  'FX / cash inputs missing' if missing_fx else 'Return valuations missing' if flow_values else
                  'Available' if view.useful else 'Starting value only')
        market_issue = missing_prices or missing_fx or flow_values
        if pending or transaction_issues or unconfirmed:
            action, screen = ('Review transactions' if pending or transaction_issues else 'Confirm history'), 'review'
        elif market_issue:
            action, screen = 'Review market data', 'list'
            route = 'accounts' if account_managed else 'holdings'
        else:
            action, screen = 'View history', 'list'
            route = 'accounts' if account_managed else 'holdings'
        if account_managed:
            action = 'Open account journal'
        results.append(HistoryStatus(identity, transactions, market, action, route, screen, scope, reasons,
                                     bool(pending or transaction_issues or unconfirmed or market_issue), view.all_start, latest))
    return tuple(results)
