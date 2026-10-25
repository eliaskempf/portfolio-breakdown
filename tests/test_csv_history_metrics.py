"""Synthetic integration oracles for CSV, PDF and shared metric calculations."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from portfolio_app.account_journal import Journal, load_journal
from portfolio_app.csv_transaction_imports import prepare_import
from portfolio_app.history_observations import CoverageReview, HistoryScope, Observation, ObservationStore
from portfolio_app.personal_performance import calculate_history, external_flows
from portfolio_app.security_history import history_fingerprint
from portfolio_app.transaction_csv import normalize
from test_csv_transaction_imports import holdings, MAPPING


def test_csv_reward_and_unit_expense_use_audited_daily_returns_through_liquidation():
    def event(identity, kind, day, quantity, **fields):
        return normalize(dict(schema_version='2', source_id='invented-return-ledger', event_id=identity,
            timestamp_utc=f'2024-04-{day:02d}T09:00:00Z', event_type=kind, account_id='Desk',
            asset_id='AUR', quantity=quantity, fee_amount='0') | fields)
    rows = [event('buy', 'buy', 1, '10', gross_amount='100', currency='EUR', fee_amount='2', fee_currency='EUR'),
            event('reward', 'reward', 2, '1', valuation_amount='10', valuation_currency='EUR',
                  valuation_quality='recorded', valuation_source='Invented receipt'),
            event('expense', 'fee', 3, '0.1'),
            event('sale', 'sell', 4, '10.9', gross_amount='130', currency='EUR', fee_amount='3', fee_currency='EUR')]
    assert not any(r.issues for r in rows)
    journal = prepare_import(Journal(), holdings(), rows, MAPPING,
                             selected_ids=[r.identity for r in rows], starts_at_zero=True).journal
    scope = HistoryScope(('desk',))
    start, end = '2024-03-31', '2024-04-05'
    store = ObservationStore(tuple(Observation(str(i), 'price', 'desk', f'2024-04-{i:02d}', D(10), 'EUR')
                                   for i in range(1, 4)),
                             (CoverageReview('invented-review', scope, start, end, history_fingerprint(journal)),))
    flows, reasons = external_flows(journal, scope, store, start, end, 'EUR')
    assert not reasons and [f.amount for f in flows] == [D(102), D(-127)]
    result = calculate_history(journal, scope, store, start, end)
    assert result.ending_value == 0 and result.total_gain == 25
    assert result.time_weighted == pytest.approx(127/102-1)
    assert result.time_weighted_method == 'daily' and result.money_weighted is not None


def test_pdf_pending_review_and_csv_evidence_coexist_without_activating_each_other(tmp_path):
    from test_transaction_imports import setup
    from test_consors_pdf import statement
    from portfolio_app.consors_pdf import parse_consors_pdf
    from portfolio_app.transaction_imports import save_pdf_drafts
    from portfolio_app.transaction_import_commands import save_transaction_import
    from portfolio_app.positions import read_snapshot
    from portfolio_app.history_commands import reconstruction_candidate, save_history
    from portfolio_app.security_history import effective_holdings
    snapshot, journal, revision = setup(tmp_path)
    save_pdf_drafts(tmp_path, snapshot.holdings.iloc[0], journal, revision, snapshot,
                    [parse_consors_pdf(statement())], charges_known=True)
    journal, revision = load_journal(tmp_path)
    snapshot = read_snapshot(tmp_path/'holdings.csv')
    before = effective_holdings(snapshot.holdings, journal.securities)
    row = normalize(dict(schema_version='2', source_id='invented-csv', event_id='buy',
        timestamp_utc='2024-04-01T09:00:00Z', event_type='buy', account_id='Desk', asset_id='AUR',
        quantity='1', gross_amount='10', currency='EUR', fee_amount='0'))
    saved = save_transaction_import(tmp_path, [row], {}, expected_journal_revision=revision,
                                   expected_holdings_revision=snapshot.revision)
    assert saved.journal.securities == journal.securities
    assert effective_holdings(snapshot.holdings, saved.journal.securities).equals(before)
    journal, revision = load_journal(tmp_path)
    book = journal.securities[0]
    candidate = reconstruction_candidate(book, [e.id for e in book.records], starts_at_zero=True)
    save_history(tmp_path, candidate, expected_journal_revision=revision,
                 expected_holdings_revision=snapshot.revision, activate=True)
    applied = load_journal(tmp_path)[0]
    assert applied.transaction_imports == saved.journal.transaction_imports
    assert applied.securities[0].active and all(e.id != row.identity for e in applied.securities[0].records)


def test_unmapped_evidence_does_not_invalidate_unrelated_scoped_history():
    from test_daily_twr import example, trade, START, END
    from portfolio_app.security_history import security_history_fingerprint
    from portfolio_app.transaction_csv import ImportReceipt
    from portfolio_app.personal_performance import coverage_reasons
    journal, scope, store = example((trade('buy', 'buy', '2024-01-02', 100),), prices=((END, 110),))
    store = replace(store, reviews=(CoverageReview('scoped', scope, START, END,
                    security_history_fingerprint(journal, 'invented')),))
    row = normalize(dict(schema_version='2', source_id='invented-unmapped', event_id='buy',
        timestamp_utc='2024-01-03T09:00:00Z', event_type='buy', account_id='Unmapped', asset_id='OTHER',
        quantity='1', gross_amount='10', currency='EUR', fee_amount='0'))
    journal = replace(journal, version=4, transaction_imports=(ImportReceipt(row.identity, row.raw, row.facts),))
    assert not coverage_reasons(journal, scope, store, START, END)
    assert calculate_history(journal, scope, store, START, END).time_weighted == pytest.approx(.1)


def test_invalid_pending_correction_keeps_original_scope_for_review():
    from test_csv_transaction_imports import activated, scenario
    from portfolio_app.personal_performance import coverage_reasons
    original = scenario()[0]
    invalid = normalize({**original.facts, 'timestamp_utc': ''})
    journal = prepare_import(activated().journal, holdings(), [invalid], {}, correction_ids=[invalid.identity]).journal
    assert 'Unapplied transaction evidence requires review' in coverage_reasons(
        journal, HistoryScope(('desk',)), ObservationStore(), '2024-03-31', '2024-04-08')
