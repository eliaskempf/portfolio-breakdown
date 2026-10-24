"""Completeness is an explicit review action, never inferred from reconciliation."""
from dataclasses import replace
from datetime import date

import pytest

from portfolio_app.account_journal import load_journal, save_journal
from portfolio_app.history_coverage_ui import confirm_complete_history
from portfolio_app.history_observations import HistoryScope, load_observations, save_observations
from portfolio_app.holdings import DataError
from portfolio_app.security_history import SecurityEvent
from test_history_ui import fixture, launch, label


def test_apply_and_confirm_records_completeness_in_the_same_journal_write(tmp_path):
    from portfolio_app.personal_performance import coverage_reasons
    fixture(tmp_path)
    before, _ = load_journal(tmp_path)
    app = launch(tmp_path)
    label(app.button, 'Review against portfolio').click().run()
    assert not app.exception and not app.checkbox
    assert any('2024' in c.value for c in app.caption)
    label(app.button, 'Apply and confirm history').click().run()
    assert not app.exception
    after, _ = load_journal(tmp_path)
    assert after.securities[0].records == before.securities[0].records
    review = after.securities[0].reviews[-1]
    assert review.complete_from == '2024-01-01' and review.complete_through == date.today().isoformat()
    assert not coverage_reasons(after, HistoryScope(('position-0',)), load_observations(tmp_path)[0],
                                review.complete_from, review.complete_through)


def test_incomplete_apply_remains_available_without_attesting_history(tmp_path):
    from portfolio_app.personal_performance import coverage_reasons
    fixture(tmp_path)
    journal, revision = load_journal(tmp_path)
    book = journal.securities[0]
    pending = SecurityEvent('extra', 'buy', '2024-02-01', quantity=1, price=10, currency='EUR', charges_known=True)
    save_journal(tmp_path, replace(journal, securities=(replace(book, records=(*book.records, pending)),)), revision)
    app = launch(tmp_path)
    label(app.button, 'Review against portfolio').click().run()
    assert not app.exception
    label(app.button, 'Apply incomplete history').click().run()
    assert not app.exception
    after, _ = load_journal(tmp_path)
    assert pending.id in after.securities[0].active_ids
    assert not after.securities[0].reviews[-1].complete_from
    assert any(reason.startswith('Review complete scope') for reason in coverage_reasons(
        after, HistoryScope(('position-0',)), load_observations(tmp_path)[0], '2024-01-01', date.today().isoformat()))


def test_stale_review_cannot_certify_changed_history_or_observations(tmp_path):
    fixture(tmp_path)
    journal, revision = load_journal(tmp_path)
    store, obs_revision = load_observations(tmp_path)
    save_observations(tmp_path, replace(store, reviews=()), obs_revision)
    with pytest.raises(DataError, match='History data changed'):
        confirm_complete_history(tmp_path, journal, HistoryScope(('position-0',)), '2024-01-01', '2025-01-01', obs_revision)
    obs_revision = load_observations(tmp_path)[1]
    book = journal.securities[0]
    save_journal(tmp_path, replace(journal, securities=(replace(book, records=(*book.records,
        SecurityEvent('note', 'note', '2024-02-01', note='Invented pending correction'))),)), revision)
    with pytest.raises(DataError, match='Transactions changed'):
        confirm_complete_history(tmp_path, journal, HistoryScope(('position-0',)), '2024-01-01', '2025-01-01', obs_revision)
