"""Completeness belongs beside transaction review, separate from market data."""
from datetime import date
from uuid import uuid4

import streamlit as st

from portfolio_app.account_journal import load_journal
from portfolio_app.gain_accounting import scope_dates
from portfolio_app.history_observations import CoverageReview, load_observations, save_observations
from portfolio_app.holdings import DataError
from portfolio_app.personal_performance import coverage_reasons
from portfolio_app.security_history import history_fingerprint
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
        review = CoverageReview(uuid4().hex, scope, start, end, history_fingerprint(latest))
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
    st.caption('Confirm that all purchases, sales, dividends, fees, taxes and transfers in this interval are recorded. Matching today’s holding alone does not prove completeness.')
    if st.button('Confirm complete history', key=key+'complete', disabled=bool(scope.unresolved)):
        try:
            confirm_complete_history(directory, journal, scope, start, end, revision)
            st.rerun()
        except (DataError, ValueError, OSError) as exc:
            st.error(str(exc))
