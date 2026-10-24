"""Explicit completeness confirmation for already-applied position histories."""
from dataclasses import dataclass, replace
from datetime import date
from uuid import uuid4

from portfolio_app.account_journal import load_journal
from portfolio_app.history_observations import CoverageReview, HistoryScope, load_observations, save_observations
from portfolio_app.holdings import DataError
from portfolio_app.personal_performance import coverage_reasons
from portfolio_app.security_history import selected_events, security_history_fingerprint
from portfolio_app.workspace_lock import workspace_lock


@dataclass(frozen=True)
class ConfirmationCandidate:
    position_id: str
    start: str
    end: str
    transactions: int
    status: str


def confirmation_interval(book, *, today=None):
    end = (today or date.today()).isoformat()
    openings = [e.date for e in selected_events(book) if e.kind == 'opening' and e.date]
    if not openings or max(openings) > end:
        raise DataError('History needs an applied starting boundary on or before today.')
    return max(openings), end


def confirmation_candidates(journal, observations, *, today=None):
    """List applied histories; do not infer completeness from matching balances."""
    candidates = []
    for book in journal.securities:
        if not book.active:
            continue
        try:
            start, end = confirmation_interval(book, today=today)
        except DataError:
            continue
        events = selected_events(book)
        superseded = {e.replaces for e in book.records if e.replaces}
        pending = any(e.id not in book.active_ids for e in book.records
                      if e.id not in superseded and e.source != 'confirmed_zero_start')
        reasons = coverage_reasons(journal, HistoryScope((book.position_id,)), observations, start, end)
        needs_review = pending or any(not r.startswith('Review complete scope') for r in reasons)
        status = ('Needs transaction review' if needs_review else
                  'Ready to confirm' if reasons else 'Confirmed')
        count = sum(e.kind != 'opening' and start <= e.date <= end for e in events)
        candidates.append(ConfirmationCandidate(book.position_id, start, end, count, status))
    return tuple(candidates)


def confirm_position_histories(directory, identities, *, expected_journal_revision,
                               expected_observations_revision, today=None):
    """One guarded evidence write, with no transaction or holding changes."""
    identities = tuple(dict.fromkeys(identities))
    if not identities:
        raise DataError('Select at least one history to confirm.')
    with workspace_lock(directory):
        journal, revision = load_journal(directory)
        if revision != expected_journal_revision:
            raise DataError('Transactions changed. Review the selection again before confirming.')
        observations, obs_revision = load_observations(directory)
        if obs_revision != expected_observations_revision:
            raise DataError('History data changed. Review the selection again before confirming.')
        candidates = {item.position_id: item for item in confirmation_candidates(journal, observations, today=today)}
        if any(identity not in candidates or candidates[identity].status != 'Ready to confirm' for identity in identities):
            raise DataError('Only applied histories without pending transaction reviews can be confirmed here.')
        reviews = tuple(CoverageReview(uuid4().hex, HistoryScope((identity,)),
                        candidates[identity].start, candidates[identity].end,
                        security_history_fingerprint(journal, identity)) for identity in identities)
        save_observations(directory, replace(observations, reviews=(*observations.reviews, *reviews)), obs_revision)
