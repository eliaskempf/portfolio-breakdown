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
    st.caption('Confirm that all purchases, sales, dividends, fees, taxes and transfers in this interval are recorded. Matching today’s holding alone does not prove completeness.')
    if st.button('Confirm complete history', key=key+'complete', disabled=bool(scope.unresolved)):
        try:
            confirm_complete_history(directory, journal, scope, start, end, revision)
            st.rerun()
        except (DataError, ValueError, OSError) as exc:
            st.error(str(exc))


def render_existing_history_confirmation(directory, holdings):
    """A single explicit action for histories applied before combined confirmation."""
    import pandas as pd
    from portfolio_app.history_confirmation import confirmation_candidates, confirm_position_histories
    journal, journal_revision = load_journal(directory)
    observations, observations_revision = load_observations(directory)
    candidates = confirmation_candidates(journal, observations)
    ready = [item for item in candidates if item.status == 'Ready to confirm']
    if not ready:
        st.info('No applied histories are waiting for confirmation.')
    else:
        names = holdings.set_index('position_id').name.to_dict()
        st.caption('Select the histories you have finished entering. Confirm that all purchases, sales, income, '
                   'fees, taxes and transfers in each shown interval are recorded.')
        frame = pd.DataFrame([{'Position': names.get(item.position_id, next(
            b.instrument_id for b in journal.securities if b.position_id == item.position_id)),
            'From': date.fromisoformat(item.start), 'Through': date.fromisoformat(item.end),
            'Transactions': item.transactions} for item in ready])
        # The key binds row selections to the exact evidence the user saw.
        table = st.dataframe(frame, hide_index=True, width='stretch', on_select='rerun', selection_mode='multi-row',
            key=f'positions_confirm_histories_{journal_revision}_{observations_revision}')
        selected = tuple(ready[index].position_id for index in table.selection.rows)
        if st.button('Confirm selected histories', type='primary', disabled=not selected):
            try:
                confirm_position_histories(directory, selected, expected_journal_revision=journal_revision,
                                           expected_observations_revision=observations_revision)
                st.session_state['position_saved_notice'] = f'Confirmed {len(selected)} '+('history.' if len(selected) == 1 else 'histories.')
                st.rerun()
            except (DataError, ValueError, OSError) as exc:
                st.error(str(exc))
        st.caption('Balances stay unchanged. Prices and FX are checked separately when calculating returns.')
    blocked = [item for item in candidates if item.status == 'Needs transaction review']
    if blocked:
        names = holdings.set_index('position_id').name.to_dict()
        st.caption('Needs transaction review first: '+', '.join(names.get(item.position_id, 'Removed position') for item in blocked))
