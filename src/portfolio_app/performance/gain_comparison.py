"""Category comparison uses the same monetary ledger and daily TWR contract."""
from datetime import date

import pandas as pd

from portfolio_app.performance.gain_accounting import current_component_values, summarize_known_gain
from portfolio_app.performance.history_observations import HistoryScope
from portfolio_app.performance.history_scope import CategoryHistoryScopes
from portfolio_app.performance.history_view import full_history_time_weighted
from portfolio_app.portfolio.strategic import strategic_performance
from portfolio_app.performance.personal_performance import HistoryCalculation


def known_category_performance(displayed, holdings, config, journal, observations, current_valued,
                               bucket='', *, target='EUR', today=None, calculation=None, current_values=None):
    today = today or date.today()
    calculation = calculation or HistoryCalculation(journal, observations)
    values = (current_values if current_values is not None else
              current_component_values(current_valued, journal, calculation=calculation))
    # Removed closed holdings have no present value, but retain realized gain.
    # Include them in comparison membership without restoring an allocation row
    # or inferring a past category. A retained current assignment still applies.
    scopes = CategoryHistoryScopes(journal, holdings, config, displayed)
    visible = set(displayed.position_id)
    accounts = {account.id for account in journal.accounts}
    securities = {book.position_id for book in journal.securities}
    investments = set(displayed.attrs.get('investment_members', {})) | {'future:overlay'}
    observations = calculation.observations
    balances = calculation.securities().positions
    closed = [dict(position_id=b.position_id, name=b.instrument_id, shares=0,
                   bucket_id=scopes.assignments.get(b.position_id, ''), current_value_reporting=0,
                   unrealized_gain_reporting=None)
              for b in journal.securities if b.active and b.position_id not in visible
              and b.position_id in balances and not balances[b.position_id].quantity]
    comparison = pd.concat([displayed, pd.DataFrame(closed)], ignore_index=True) if closed else displayed

    comparison.attrs = dict(displayed.attrs)

    def evaluate(rows, category):
        if category is not None:
            scope = scopes.scope(category)
        else:
            identities = tuple(rows.position_id)
            account_rows = set()
            if 'journal_managed' in rows and 'account_id' in rows:
                managed = rows.journal_managed.fillna(False).eq(True)
                account_rows = accounts.intersection(rows.loc[managed, 'account_id'])
            if any(identity in scopes.linked or identity.startswith('account-') or identity in investments for identity in identities) or (account_rows and not set(identities) <= securities):
                # A single cash/funded display row is not its whole account.
                scope = HistoryScope(unresolved=('Account display components need an explicit dated scope allocation',))
            else:
                scope = HistoryScope(identities)
        gain = summarize_known_gain(journal, scope, observations, today.isoformat(), target,
                                    current_values=values, summary_rows=current_valued, calculation=calculation)
        twr = calculation._get(('category_twr', scope, target, today), lambda:
            full_history_time_weighted(calculation, scope, today, target))
        return gain, twr

    return strategic_performance(comparison, config, bucket, evaluate=evaluate)
