"""Category comparison uses the same monetary ledger and exact TWR contract."""
from datetime import date

import pandas as pd

from portfolio_app.gain_accounting import current_component_values, summarize_known_gain
from portfolio_app.history_observations import HistoryScope
from portfolio_app.history_scope import category_history_scope
from portfolio_app.history_view import history_view
from portfolio_app.strategic import strategic_performance
from portfolio_app.personal_performance import HistoryCalculation


def known_category_performance(displayed, holdings, config, journal, observations, current_valued,
                               bucket='', *, target='EUR', today=None):
    today = today or date.today()
    values = current_component_values(current_valued, journal)
    linked_cash = {link.position_id: link.account_id for link in journal.cash_links}
    accounts = {a.id for a in journal.accounts}
    # Removed closed holdings have no present value, but retain realized gain.
    # Include them in comparison membership without restoring an allocation row
    # or inferring a past category. A retained current assignment still applies.
    assignments = holdings.set_index('position_id').get('bucket_id', {})
    calculation = HistoryCalculation(journal, observations)
    observations = calculation.observations
    balances = calculation.securities().positions
    closed = [dict(position_id=b.position_id, name=b.instrument_id, shares=0,
                   bucket_id=assignments.get(b.position_id, ''), current_value_reporting=0,
                   unrealized_gain_reporting=None)
              for b in journal.securities if b.active and b.position_id not in set(displayed.position_id)
              and b.position_id in balances and not balances[b.position_id].quantity]
    comparison = pd.concat([displayed, pd.DataFrame(closed)], ignore_index=True) if closed else displayed

    comparison.attrs = dict(displayed.attrs)

    def evaluate(rows, category):
        if category is not None:
            scope = category_history_scope(journal, holdings, config, category, displayed=displayed)
        else:
            identities = tuple(rows.position_id)
            managed = rows.get('journal_managed', rows.position_id.map(lambda _: False)).fillna(False).eq(True)
            account_rows = set(rows.loc[managed].get('account_id', rows.position_id.map(lambda _: ''))) & accounts
            securities = {b.position_id for b in journal.securities}
            investments = set(displayed.attrs.get('investment_members', {})) | {'future:overlay'}
            if any(identity in linked_cash or identity.startswith('account-') or identity in investments for identity in identities) or (account_rows and not set(identities) <= securities):
                # A single cash/funded display row is not its whole account.
                scope = HistoryScope(unresolved=('Account display components need an explicit dated scope allocation',))
            else:
                scope = HistoryScope(identities)
        gain = summarize_known_gain(journal, scope, observations, today.isoformat(), target,
                                    current_values=values, summary_rows=current_valued, calculation=calculation)
        view = history_view(journal, scope, observations, 'All', today=today, target=target,
                            include_points=False, calculation=calculation)
        return gain, view.all_result.time_weighted

    return strategic_performance(comparison, config, bucket, evaluate=evaluate)
