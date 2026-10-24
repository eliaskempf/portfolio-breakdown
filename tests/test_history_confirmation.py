"""Synthetic attestations do not alter balances or certify unselected histories."""
from dataclasses import replace
from datetime import date

import pytest

from portfolio_app.account_journal import load_journal, save_journal
from portfolio_app.history_confirmation import confirmation_candidates, confirm_position_histories
from portfolio_app.history_observations import HistoryScope, load_observations, save_observations, ObservationStore
from portfolio_app.holdings import DataError
from portfolio_app.personal_performance import coverage_reasons, calculate_history
from portfolio_app.security_history import SecurityEvent as E, HistoryReview, security_history_fingerprint
from test_history_ui import fixture

TODAY = date(2026, 10, 24)


def two_positions(tmp_path):
    fixture(tmp_path)
    journal, revision = load_journal(tmp_path)
    book = journal.securities[0]
    other = replace(book, position_id='other', instrument_id='INVENTED-SECOND',
                    records=(replace(book.records[0], id='other-open'),), active_ids=('other-open',))
    save_journal(tmp_path, replace(journal, securities=(*journal.securities, other)), revision)
    return load_journal(tmp_path)


def confirm(tmp_path, identities, **kw):
    journal, revision = load_journal(tmp_path)
    observations, obs_revision = load_observations(tmp_path)
    confirm_position_histories(tmp_path, identities, expected_journal_revision=revision,
                               expected_observations_revision=obs_revision, today=TODAY, **kw)
    return journal, observations


def test_bulk_confirmation_saves_each_interval_without_changing_holdings_or_journal(tmp_path):
    journal, revision = two_positions(tmp_path)
    holdings = (tmp_path / 'holdings.csv').read_bytes()
    before = load_observations(tmp_path)[0]
    confirm(tmp_path, ('position-0', 'other'))
    assert load_journal(tmp_path) == (journal, revision)
    assert (tmp_path / 'holdings.csv').read_bytes() == holdings
    observations, _ = load_observations(tmp_path)
    assert observations.observations == before.observations
    assert len(observations.reviews) == len(before.reviews) + 2
    assert all(item.status == 'Confirmed' for item in confirmation_candidates(journal, observations, today=TODAY))
    for identity in ('position-0', 'other'):
        assert not coverage_reasons(journal, HistoryScope((identity,)), observations, '2024-01-01', TODAY.isoformat())
    # Confirmation cannot supply an absent price or create a return.
    result = calculate_history(journal, HistoryScope(('other',)), observations, '2024-01-01', TODAY.isoformat())
    assert result.money_weighted is None and result.ending_value is None


def test_confirmation_survives_unrelated_edits_but_not_own_pending_evidence(tmp_path):
    journal, _ = two_positions(tmp_path)
    confirm(tmp_path, ('position-0',))
    observations, _ = load_observations(tmp_path)
    other = replace(journal.securities[1], records=(*journal.securities[1].records,
                    E('pending-other', 'note', '2024-02-01')))
    changed = replace(journal, securities=(journal.securities[0], other))
    scope = HistoryScope(('position-0',))
    assert not coverage_reasons(changed, scope, observations, '2024-01-01', TODAY.isoformat())
    own = replace(journal.securities[0], records=(*journal.securities[0].records,
                  E('pending-own', 'note', '2024-02-01')))
    changed = replace(changed, securities=(own, other))
    assert coverage_reasons(changed, scope, observations, '2024-01-01', TODAY.isoformat())


def test_pending_edits_unknown_fees_and_inactive_positions_are_not_bulk_confirmed(tmp_path):
    journal, revision = two_positions(tmp_path)
    book = journal.securities[0]
    pending = replace(book, records=(*book.records, E('draft', 'buy', '2024-02-01', quantity=1, price=10, currency='EUR')))
    other = journal.securities[1]
    unknown = E('unknown-fee', 'distribution', '2024-03-01', amount=2, currency='EUR')
    other = replace(other, records=(*other.records, unknown), active_ids=(*other.active_ids, unknown.id))
    inactive = replace(book, position_id='inactive', records=(replace(book.records[0], id='inactive-open'),), active_ids=())
    journal = replace(journal, securities=(pending, other, inactive))
    save_journal(tmp_path, journal, revision)
    observations = load_observations(tmp_path)[0]
    assert [item.status for item in confirmation_candidates(journal, observations, today=TODAY)] == ['Needs transaction review']*2
    with pytest.raises(DataError, match='Only applied histories'):
        confirm(tmp_path, ('position-0', 'other'))
    assert load_observations(tmp_path)[0] == observations  # No partial confirmation.


def test_bulk_confirmation_has_revision_guards(tmp_path):
    journal, revision = two_positions(tmp_path)
    observations, obs_revision = load_observations(tmp_path)
    save_observations(tmp_path, replace(observations, reviews=()), obs_revision)
    with pytest.raises(DataError, match='History data changed'):
        confirm_position_histories(tmp_path, ('position-0',), expected_journal_revision=revision,
            expected_observations_revision=obs_revision, today=TODAY)
    obs_revision = load_observations(tmp_path)[1]
    save_journal(tmp_path, replace(journal, securities=(journal.securities[0],)), revision)
    with pytest.raises(DataError, match='Transactions changed'):
        confirm_position_histories(tmp_path, ('position-0',), expected_journal_revision=revision,
            expected_observations_revision=obs_revision, today=TODAY)


def test_transfer_dependencies_and_legacy_confirmations_remain_conservative(tmp_path):
    from portfolio_app.security_history import accounting_fingerprint
    journal, _ = two_positions(tmp_path)
    source, destination = journal.securities
    destination = replace(destination, instrument_id=source.instrument_id)
    transfer = E('move', 'transfer', '2024-03-01', quantity=1, other_position_id=destination.position_id)
    source = replace(source, records=(*source.records, transfer), active_ids=(*source.active_ids, transfer.id))
    journal = replace(journal, securities=(source, destination))
    initial = security_history_fingerprint(journal, destination.position_id)
    changed = replace(journal, securities=(replace(source, records=(*source.records,
                      E('unreviewed', 'note', '2024-02-01'))), destination))
    assert security_history_fingerprint(changed, destination.position_id) != initial
    legacy = HistoryReview('old-review', destination.active_ids, TODAY.isoformat(), 'accept',
                           '2024-01-01', TODAY.isoformat(), accounting_fingerprint(journal))
    old_journal = replace(journal, securities=(source, replace(destination, reviews=(legacy,))))
    assert not coverage_reasons(old_journal, HistoryScope((destination.position_id,)), ObservationStore(),
                                '2024-01-01', TODAY.isoformat())
    # Neither legacy nor new confirmation receipts change financial facts.
    assert security_history_fingerprint(old_journal, destination.position_id) == initial


def test_bulk_confirmation_retains_different_inception_and_snapshot_intervals(tmp_path):
    journal, revision = two_positions(tmp_path)
    second = journal.securities[1]
    opening = replace(second.records[0], date='2024-06-30', quantity=0, amount=None, source='confirmed_zero_start')
    purchase = E('second-buy', 'buy', '2024-07-01', quantity=2, price=10, currency='EUR', charges_known=True)
    second = replace(second, records=(opening, purchase), active_ids=(opening.id, purchase.id))
    save_journal(tmp_path, replace(journal, securities=(journal.securities[0], second)), revision)
    confirm(tmp_path, ('position-0', 'other'))
    reviews = load_observations(tmp_path)[0].reviews[-2:]
    assert [(r.scope.positions[0], r.start, r.end) for r in reviews] == [
        ('position-0', '2024-01-01', TODAY.isoformat()), ('other', '2024-06-30', TODAY.isoformat())]


def test_combined_action_attestation_survives_unrelated_position_changes(tmp_path):
    journal, _ = two_positions(tmp_path)
    first, other = journal.securities
    review = HistoryReview('combined', first.active_ids, TODAY.isoformat(), 'accept',
        '2024-01-01', TODAY.isoformat(), security_history_fingerprint(journal, first.position_id))
    first = replace(first, reviews=(review,))
    other = replace(other, records=(*other.records, E('later-evidence', 'note', '2024-02-01')))
    journal = replace(journal, securities=(first, other))
    assert not coverage_reasons(journal, HistoryScope((first.position_id,)), ObservationStore(),
                                '2024-01-01', TODAY.isoformat())
    first = replace(first, records=(*first.records, E('own-evidence', 'note', '2024-02-01')))
    assert coverage_reasons(replace(journal, securities=(first, other)), HistoryScope((first.position_id,)),
                            ObservationStore(), '2024-01-01', TODAY.isoformat())
