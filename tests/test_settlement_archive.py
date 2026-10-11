"""Synthetic durable market history; no network or personal workspace access."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
import json

import pytest

from portfolio_app import account_journal as ledger
from portfolio_app.derivative_types import ContractSpec, PriceObservation
from portfolio_app.holdings import DataError
from portfolio_app.settlement_archive import FILE, SettlementArchive, reconciliation_rows


@pytest.fixture
def contract():
    return ContractSpec('SYNTH', '2026-12-18', 'EUR', 10, '.5', name='Invented contract')


def quote(contract, price=1000, *, day='2026-01-05', source='Invented source', at='2026-01-06T12:00:00+00:00'):
    return PriceObservation(contract.id, day, price, source=source, observed_at=at)


def journal_for(contract, price=1000):
    return ledger.Journal(accounts=(ledger.Account('a', 'Invented account'),), contracts=(contract,),
        positions=(ledger.PositionSpec('p', 'a', contract.id),), events=(
            ledger.Event('open', '2026-01-02', 'trade', position_id='p', quantity=1, price=990),
            ledger.Event('settle', '2026-01-05', 'settlement', position_id='p', price=price)))


def test_read_only_empty_archive_does_not_create_workspace(tmp_path, contract):
    root = tmp_path / 'absent'
    archive = SettlementArchive(root)
    assert archive.revision() is None
    assert archive.load(contract, '2026-01-05') is None
    assert archive.latest(contract, '2026-01-05') is None
    assert archive.versions(contract, '2026-01-05') == ()
    assert archive.entries(contract) == {}
    rows = reconciliation_rows(journal_for(contract), archive)
    assert rows[0]['Status'] == 'No archived price'
    assert not root.exists()


def test_preserves_versions_and_canonical_until_review(tmp_path, contract):
    archive = SettlementArchive(tmp_path)
    original, corrected = quote(contract), quote(contract, 1005, at='2026-01-07T12:00:00+00:00')
    archive.record(contract, original)
    initial = archive.revision()
    archive.record(contract, original)
    assert archive.revision() == initial
    archive.record(contract, corrected)
    assert archive.load(contract, original.date) == original
    assert archive.latest(contract, original.date) == corrected
    assert archive.versions(contract, original.date) == (original, corrected)
    assert archive.entries(contract) == {original.date: original}
    with pytest.raises(DataError, match='latest settlement changed'):
        archive.accept_latest(contract, original.date, 1004)
    archive.accept_latest(contract, original.date, corrected.price)
    reopened = SettlementArchive(tmp_path)
    assert reopened.load(contract, original.date) == corrected
    assert reopened.versions(contract, original.date) == (original, corrected)
    assert (tmp_path / FILE).is_file()
    assert not (tmp_path / '.cache').exists()


def test_repeated_retrieval_updates_time_without_unbounded_versions(tmp_path, contract):
    archive = SettlementArchive(tmp_path)
    archive.record(contract, quote(contract))
    for day in range(7, 20):
        archive.record(contract, quote(contract, at=f'2026-01-{day:02}T12:00:00+00:00'))
    assert len(archive.versions(contract, '2026-01-05')) == 1
    assert archive.latest(contract, '2026-01-05').observed_at == '2026-01-19T12:00:00+00:00'
    assert not (tmp_path / '.backups').exists()


def test_latest_can_return_to_earlier_price_and_source_versions_are_retained(tmp_path, contract):
    archive = SettlementArchive(tmp_path)
    first, different = quote(contract), quote(contract, 1005)
    archive.record(contract, first)
    archive.record(contract, different)
    archive.record(contract, first)
    assert archive.latest(contract, first.date) == first
    assert len(archive.versions(contract, first.date)) == 2
    archive.record(contract, quote(contract, source='Another invented source'))
    assert len(archive.versions(contract, first.date)) == 3
    assert reconciliation_rows(journal_for(contract), archive) == []


@pytest.mark.parametrize('changes', [
    {'contract_id': 'EUREX:OTHER:2026-12-18'}, {'date': '2026-12-18'},
    {'kind': 'final_settlement'}, {'kind': 'mark'}, {'kind': 'manual'},
])
def test_rejects_wrong_identity_expiry_and_nondaily_kinds(tmp_path, contract, changes):
    archive = SettlementArchive(tmp_path)
    with pytest.raises(DataError, match='identity, date or price type'):
        archive.record(contract, replace(quote(contract), **changes))
    assert archive.revision() is None


@pytest.mark.parametrize('changes', [
    {'currency': 'USD'}, {'multiplier': 20}, {'tick': 1},
    {'last_trade_date': '2026-12-17'}, {'return_convention': 'net'},
])
def test_rejects_changed_contract_economics_on_read_or_write(tmp_path, contract, changes):
    archive = SettlementArchive(tmp_path)
    archive.record(contract, quote(contract))
    initial = archive.revision()
    changed = replace(contract, **changes)
    for action in (lambda: archive.load(changed, '2026-01-05'),
                   lambda: archive.record(changed, quote(changed)),
                   lambda: archive.accept_latest(changed, '2026-01-05', 1000)):
        with pytest.raises(DataError, match='specifications differ'):
            action()
    assert archive.revision() == initial
    updated_metadata = replace(contract, name='Updated invented name', source='Public metadata')
    assert archive.load(updated_metadata, '2026-01-05') == quote(contract)


@pytest.mark.parametrize('corruption', ['invalid_json', 'version', 'identity', 'index', 'date', 'duplicate'])
def test_corrupt_archive_fails_closed_and_is_never_overwritten(tmp_path, contract, corruption):
    archive = SettlementArchive(tmp_path)
    archive.record(contract, quote(contract))
    raw = json.loads(archive.path.read_text())
    day = raw['contracts'][contract.id]['days']['2026-01-05']
    if corruption == 'version': raw['version'] = True
    elif corruption == 'identity': raw['contracts']['wrong'] = raw['contracts'].pop(contract.id)
    elif corruption == 'index': day['latest'] = 99
    elif corruption == 'date': day['versions'][0]['date'] = '2026-01-06'
    elif corruption == 'duplicate': day['versions'].append(day['versions'][0])
    archive.path.write_text('not json' if corruption == 'invalid_json' else json.dumps(raw))
    before = archive.path.read_bytes()
    for action in (lambda: archive.load(contract, '2026-01-05'),
                   lambda: archive.record(contract, quote(contract, 1005)),
                   lambda: archive.accept_latest(contract, '2026-01-05', 1000)):
        with pytest.raises(DataError, match='existing history has been retained'):
            action()
    assert archive.path.read_bytes() == before


def test_concurrent_writers_merge_without_losing_dates_or_versions(tmp_path, contract):
    def record(index):
        archive = SettlementArchive(tmp_path)
        archive.record(contract, quote(contract, 1000 + index, day=f'2026-01-{index + 1:02}'))
        archive.record(contract, quote(contract, 1100 + index, day='2026-02-02'))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(record, range(20)))
    archive = SettlementArchive(tmp_path)
    assert len(archive.entries(contract)) == 21
    assert len(archive.versions(contract, '2026-02-02')) == 20
    assert {item.price for item in archive.versions(contract, '2026-02-02')} == {
        Decimal(1100 + index) for index in range(20)}


def test_reconciliation_keeps_archive_correction_separate_from_accounting(tmp_path, contract):
    archive = SettlementArchive(tmp_path)
    journal = journal_for(contract)
    journal_path = tmp_path / 'account-journal.json'
    journal_path.write_text('invented journal sentinel')
    original_bytes = journal_path.read_bytes()
    original_state = ledger.replay(journal)
    archive.record(contract, quote(contract))
    archive.record(contract, quote(contract, 1005))
    rows = reconciliation_rows(journal, archive)
    assert len(rows) == 1
    assert rows[0]['Status'] == 'Source changed'
    assert [rows[0][field] for field in ('Archived price', 'Retrieved price', 'Recorded price')] == ['1000', '1005', '1000']
    identity = rows[0]['identity']
    archive.record(contract, quote(contract, 1005, at='2026-01-09T12:00:00+00:00'))
    assert reconciliation_rows(journal, archive)[0]['identity'] == identity
    archive.accept_latest(contract, '2026-01-05', 1005)
    rows = reconciliation_rows(journal, archive)
    assert rows[0]['Status'] == 'Recorded settlement differs'
    assert ledger.replay(journal) == original_state
    assert journal_path.read_bytes() == original_bytes
    fixed = ledger.append_event(journal, ledger.Event('fix', '2026-01-05', 'settlement',
        position_id='p', price=1005, replaces='settle'))
    assert reconciliation_rows(fixed, archive) == []
    assert len(fixed.events) == 3  # correction preserves original journal event


def test_reconciliation_missing_archive_closed_positions_voids_and_final_exclusion(tmp_path, contract):
    archive = SettlementArchive(tmp_path)
    journal = journal_for(contract, price=1005)
    assert reconciliation_rows(journal, archive)[0]['Status'] == 'No archived price'
    archive.record(contract, quote(contract))
    closed = ledger.append_event(journal, ledger.Event('close', '2026-01-06', 'trade',
        position_id='p', quantity=-1, price=1010))
    assert reconciliation_rows(closed, archive)[0]['Status'] == 'Recorded settlement differs'
    assert reconciliation_rows(closed, archive, position_ids=()) == []
    assert len(reconciliation_rows(closed, archive, position_ids=('p',))) == 1
    voided = ledger.append_event(closed, ledger.Event('void', '2026-01-07', 'void', replaces='settle'))
    assert reconciliation_rows(voided, archive) == []
    expired_contract = replace(contract, expiry='2026-01-06')
    expired = replace(journal_for(expired_contract), events=(
        ledger.Event('open', '2026-01-02', 'trade', position_id='p', quantity=1, price=990),
        ledger.Event('final', '2026-01-06', 'final_settlement', position_id='p', price=1010)))
    assert reconciliation_rows(expired, archive) == []


def test_reconciliation_missing_retrieval_does_not_hide_manual_mismatch(tmp_path, contract):
    archive = SettlementArchive(tmp_path)
    journal = journal_for(contract, price=1005)
    archive.record(contract, quote(contract))
    archive.record(contract, quote(contract, 1010))
    row = reconciliation_rows(journal, archive)[0]
    assert row['Status'] == 'Source changed · Recorded settlement differs'
    assert row['position_id'] == 'p'
    assert row['contract_id'] == contract.id
    assert all(isinstance(value, str) for value in row.values())


def test_workspace_backup_restores_entire_archive_with_versions(tmp_path, contract):
    from portfolio_app.backup import create_backup, inspect_backup, commit_restore
    source = tmp_path / 'invented-workspace'
    archive = SettlementArchive(source)
    archive.record(contract, quote(contract))
    archive.record(contract, quote(contract, 1005))
    before = archive.path.read_bytes()
    draft = inspect_backup(create_backup(source))
    try:
        restored = commit_restore(draft, tmp_path / 'restored', source)
        copied = SettlementArchive(restored)
        assert copied.path.read_bytes() == before
        assert len(copied.versions(contract, '2026-01-05')) == 2
        assert copied.load(contract, '2026-01-05').price == 1000
        assert copied.latest(contract, '2026-01-05').price == 1005
    finally:
        draft.close()


def test_external_edit_during_atomic_write_is_preserved(tmp_path, contract, monkeypatch):
    from contextlib import contextmanager
    import portfolio_app.settlement_archive as module
    archive = SettlementArchive(tmp_path)
    archive.record(contract, quote(contract))
    stage = module.staged_text
    external_content = archive.path.read_text() + ' '

    @contextmanager
    def edited_during_stage(*args, **kwargs):
        with stage(*args, **kwargs) as temporary:
            archive.path.write_text(external_content)
            yield temporary

    monkeypatch.setattr(module, 'staged_text', edited_during_stage)
    with pytest.raises(DataError, match='changed during save'):
        archive.record(contract, quote(contract, 1005))
    assert archive.path.read_text() == external_content
    assert not list(tmp_path.glob('*.tmp'))
