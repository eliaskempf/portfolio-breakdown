"""Completeness belongs beside transaction review, separate from market data."""
from datetime import date
from uuid import uuid4

import streamlit as st

from portfolio_app.account_journal import load_journal
from portfolio_app.gain_accounting import scope_dates
from portfolio_app.history_observations import CoverageReview, load_observations, save_observations
from portfolio_app.holdings import DataError
from portfolio_app.personal_performance import coverage_reasons
from portfolio_app.security_history import history_fingerprint, security_history_fingerprint
from portfolio_app.workspace_lock import workspace_lock


def confirm_complete_history(directory, journal, scope, start, end, revision):
    """Save an explicit dated attestation without changing holdings or transactions."""
    from dataclasses import replace
    with workspace_lock(directory):
        latest, _ = load_journal(directory)
        if history_fingerprint(latest) != history_fingerprint(journal):
            raise DataError('Transactions changed. Review them again before confirming completeness.')
        store, current = load_observations(directory)
        if current != revision:
            raise DataError('History data changed. Review again before confirming completeness.')
        if scope.unresolved or not (scope.positions or scope.accounts):
            raise DataError('Resolve which holdings and accounts are included before confirming history.')
        active = {b.position_id for b in latest.securities if b.active}
        if not set(scope.positions) <= active:
            raise DataError('Apply the selected transactions before confirming complete history.')
        fingerprint = (security_history_fingerprint(latest, scope.positions[0])
                       if len(scope.positions) == 1 and not scope.accounts else history_fingerprint(latest))
        review = CoverageReview(uuid4().hex, scope, start, end, fingerprint)
        save_observations(directory, replace(store, reviews=(*store.reviews, review)), revision)


def render_coverage_review(directory, journal, scope, *, key, can_confirm=True):
    dates = scope_dates(journal, scope)
    if not dates:
        return
    start, end = dates[0], date.today().isoformat()
    store, revision = load_observations(directory)
    needs_review = any(r.startswith('Review complete scope') for r in coverage_reasons(journal, scope, store, start, end))
    st.markdown('**History completeness**')
    interval = f'{date.fromisoformat(start):%d %b %Y} – {date.fromisoformat(end):%d %b %Y}'
    if not can_confirm:
        st.caption('Apply the selected transactions first, then confirm completeness here.')
        return
    if not needs_review:
        st.caption('History confirmed · '+interval)
        return
    st.caption(interval)
    if scope.accounts and not scope.positions:
        st.caption('Confirm that the opening balances and positions are correct, and all contracts (including closed ones), '
            'trades, rolls, cash movements, income, fees and taxes since then are recorded.')
        st.caption('Earlier contracts before an opening snapshot are not required. This does not certify downloaded '
            'settlement prices; missing market data and transaction details are checked separately.')
    else:
        st.caption('Confirm that all purchases, sales, dividends, fees, taxes and transfers in this interval are recorded. Matching today’s holding alone does not prove completeness.')
    if st.button('Confirm complete history', key=key+'complete', disabled=bool(scope.unresolved)):
        try:
            confirm_complete_history(directory, journal, scope, start, end, revision)
            st.rerun()
        except (DataError, ValueError, OSError) as exc:
            st.error(str(exc))


def render_account_coverage_review(directory, journal, *, key, account_id=None):
    """Explicitly review one whole account, including its closed contracts."""
    from portfolio_app.history_observations import HistoryScope
    accounts = {a.id: a.name for a in journal.accounts}
    if not accounts or (account_id is not None and account_id not in accounts):
        return
    with st.expander('Review account history completeness'):
        if account_id is None:
            account_id = st.selectbox('Account to confirm', list(accounts), format_func=accounts.get,
                key=key+'account')
        else:
            st.caption('Account: '+accounts[account_id])
        st.caption('Covers this account’s cash and all its futures investments, including closed contracts.')
        scope = HistoryScope(accounts=(account_id,))
        if not scope_dates(journal, scope):
            st.caption('Record an opening balance or transaction before confirming this account.')
            return
        render_coverage_review(directory, journal, scope, key=key+account_id+'_')
