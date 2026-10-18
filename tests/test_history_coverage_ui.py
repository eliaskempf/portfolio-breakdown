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


def test_review_confirms_history_without_changing_portfolio_or_transactions(tmp_path):
    fixture(tmp_path)
    before, revision = load_journal(tmp_path)
    holdings = (tmp_path/'holdings.csv').read_bytes()
    app = launch(tmp_path)
    label(app.button, 'Review against portfolio').click().run()
    assert not app.exception
    assert not app.checkbox
    assert any('2024' in c.value for c in app.caption)
    label(app.button, 'Confirm complete history').click().run()
    assert not app.exception
    assert any(c.value.startswith('History confirmed') for c in app.caption)
    assert load_journal(tmp_path) == (before, revision)
    assert (tmp_path/'holdings.csv').read_bytes() == holdings
    review = load_observations(tmp_path)[0].reviews[-1]
    assert review.start == '2024-01-01' and review.end == date.today().isoformat()
    assert review.scope == HistoryScope(('position-0',))


def test_pending_changes_must_be_applied_before_completeness_confirmation(tmp_path):
    fixture(tmp_path)
    journal, revision = load_journal(tmp_path)
    book = journal.securities[0]
    pending = SecurityEvent('extra', 'buy', '2024-02-01', quantity=1, price=10, currency='EUR', charges_known=True)
    save_journal(tmp_path, replace(journal, securities=(replace(book, records=(*book.records, pending)),)), revision)
    app = launch(tmp_path)
    label(app.button, 'Review against portfolio').click().run()
    assert not app.exception
    assert not any(b.label == 'Confirm complete history' for b in app.button)
    assert any('Apply the selected transactions first' in c.value for c in app.caption)


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
