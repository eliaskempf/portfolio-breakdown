"""Synthetic persistence tests for evidence, atomic activation and recovery."""
from dataclasses import replace
from pathlib import Path
import pytest

from portfolio_app.account_journal import load_journal
from portfolio_app.holdings import DataError
from portfolio_app.positions import read_snapshot
from portfolio_app.transaction_import_commands import prepare_positions, save_transaction_import
from portfolio_app.security_history import replay_securities, effective_holdings
from test_csv_transaction_imports import scenario, MAPPING


def setup(directory):
    additions=[dict(position_key=pos,id=asset.lower(),name=asset,account=account,instrument_type='crypto') for (account,asset),pos in MAPPING.items()]
    snapshot=prepare_positions(directory,additions,expected_holdings_revision=None)
    return snapshot


def save(directory,snapshot,**kw):
    rows=scenario()
    return save_transaction_import(directory,rows,MAPPING,expected_journal_revision=None,
        expected_holdings_revision=snapshot.revision,selected_ids=[r.identity for r in rows],starts_at_zero=True,
        accept_reconciliation=True,comparison_date='2024-04-08',**kw)


def test_evidence_then_activation_and_reimport_no_write(tmp_path):
    snapshot=setup(tmp_path)
    evidence=save_transaction_import(tmp_path,scenario(),{},expected_journal_revision=None,expected_holdings_revision=snapshot.revision)
    assert len(evidence.journal.transaction_imports)==7 and not evidence.journal.securities
    journal,revision=load_journal(tmp_path)
    result=save_transaction_import(tmp_path,scenario(),MAPPING,expected_journal_revision=revision,expected_holdings_revision=snapshot.revision,
        selected_ids=[r.identity for r in scenario()],starts_at_zero=True,accept_reconciliation=True,comparison_date='2024-04-08')
    assert all(r.positions for r in result.journal.transaction_imports)
    assert read_snapshot(tmp_path/'holdings.csv').holdings.shares.sum()==0
    assert effective_holdings(read_snapshot(tmp_path/'holdings.csv').holdings,result.journal.securities).shares.sum()>0
    journal,revision=load_journal(tmp_path)
    again=save_transaction_import(tmp_path,scenario(),MAPPING,expected_journal_revision=revision,expected_holdings_revision=snapshot.revision,
        selected_ids=[r.identity for r in scenario()],accept_reconciliation=True,comparison_date='2024-04-08')
    assert load_journal(tmp_path)[1]==revision and again.applied==0


def test_failed_publish_preserves_balances_and_stale_revision(tmp_path,monkeypatch):
    snapshot=setup(tmp_path)
    original=Path.replace
    def fail(self,destination):
        if Path(destination).name=='account-journal.json':
            raise OSError('Invented disk failure')
        return original(self,destination)
    monkeypatch.setattr(Path,'replace',fail)
    with pytest.raises(OSError,match='Invented'):
        save(tmp_path,snapshot)
    assert not load_journal(tmp_path)[0].securities
    assert read_snapshot(tmp_path/'holdings.csv').holdings.shares.sum()==0
    with pytest.raises(DataError,match='changed'):
        save(tmp_path,replace(snapshot,revision='stale'))


def test_backup_restore_v3(tmp_path):
    from portfolio_app.backup import create_backup,inspect_backup,commit_restore
    source=tmp_path/'source';source.mkdir()
    snapshot=setup(source);save(source,snapshot)
    draft=inspect_backup(create_backup(source))
    destination=tmp_path/'restored'
    commit_restore(draft,destination,source)
    assert load_journal(source)==load_journal(destination)
    assert replay_securities(load_journal(destination)[0].securities).positions['vault'].quantity==0


def test_changed_valuation_evidence_requires_review(tmp_path):
    from portfolio_app.history_observations import save_observations,ObservationStore,Observation
    snapshot=setup(tmp_path)
    save_observations(tmp_path,ObservationStore((Observation('fx','fx','USD/EUR','2024-04-01',1,'USD',target='EUR'),)),None)
    with pytest.raises(DataError,match='Valuation evidence'):
        save(tmp_path,snapshot,expected_observations_revision=None)
    assert not load_journal(tmp_path)[0].securities


def test_manual_followup_preserves_imported_movements(tmp_path):
    from portfolio_app.history_commands import save_history
    from portfolio_app.security_history import append_evidence,SecurityEvent
    from decimal import Decimal
    snapshot=setup(tmp_path);save(tmp_path,snapshot)
    journal,revision=load_journal(tmp_path)
    book=next(b for b in journal.securities if b.position_id=='desk')
    event=SecurityEvent('manual-followup','buy','2024-04-09',quantity=Decimal(1),amount=Decimal(18),currency='EUR',charges_known=True)
    candidate=replace(append_evidence(book,(event,)),active_ids=(*book.active_ids,event.id))
    result=save_history(tmp_path,candidate,expected_journal_revision=revision,
        expected_holdings_revision=read_snapshot(tmp_path/'holdings.csv').revision,activate=True,accept_balance_change=True)
    assert result.version==5 and result.transaction_imports==journal.transaction_imports
    assert replay_securities(result.securities).positions['desk'].quantity==3


def test_combined_application_records_scoped_completeness(tmp_path):
    from portfolio_app.transaction_import_commands import import_confirmation_intervals
    from portfolio_app.csv_transaction_imports import prepare_import
    from portfolio_app.security_history import security_history_fingerprint
    snapshot = setup(tmp_path)
    rows = scenario()
    preview = prepare_import(load_journal(tmp_path)[0], snapshot.holdings, rows, MAPPING,
                             selected_ids=[r.identity for r in rows], starts_at_zero=True)
    intervals = import_confirmation_intervals(preview.journal, {c['position_id'] for c in preview.comparisons})
    result = save(tmp_path, snapshot, complete_intervals=intervals)
    for book in result.journal.securities:
        review = book.reviews[-1]
        assert (review.complete_from, review.complete_through) == intervals[book.position_id]
        assert review.facts_fingerprint == security_history_fingerprint(result.journal, book.position_id)
    # A changed companion leg invalidates the connected position's attestation.
    companion = next(b for b in result.journal.securities if b.position_id == 'orb')
    changed = replace(companion, records=(*companion.records, replace(companion.records[0], id='invented-correction', source='manual', replaces=companion.records[0].id)))
    journal = replace(result.journal, securities=tuple(changed if b.position_id == 'orb' else b for b in result.journal.securities))
    assert security_history_fingerprint(journal, 'desk') != security_history_fingerprint(result.journal, 'desk')


def test_incomplete_application_does_not_confirm_and_stale_intervals_do_not_write(tmp_path):
    snapshot = setup(tmp_path)
    with pytest.raises(DataError, match='intervals changed'):
        save(tmp_path, snapshot, complete_intervals={'desk': ('2024-01-01', '2024-04-08')})
    assert not load_journal(tmp_path)[0].securities
    result = save(tmp_path, snapshot)
    assert all(not b.reviews[-1].complete_from for b in result.journal.securities)


def test_imported_economic_event_removal_includes_every_leg(tmp_path):
    from portfolio_app.history_commands import remove_transactions
    snapshot = setup(tmp_path)
    result = save(tmp_path, snapshot)
    book = next(b for b in result.journal.securities if b.position_id == 'vault')
    swap = next(e for e in book.records if e.kind == 'swap')
    from portfolio_app.history_commands import changed_positions
    candidate = remove_transactions(book, [swap.id])
    assert changed_positions(result.journal, (candidate,)) == {'vault', 'orb'}


def test_listing_connection_is_staged_and_applies_with_history(tmp_path):
    from portfolio_app.instruments import Instrument
    from portfolio_app.transaction_import_commands import positions_content
    snapshot = setup(tmp_path)
    before = (tmp_path/'holdings.csv').read_bytes()
    listing = Instrument('AUR-EUR', 'Invented Aurora', kind='CRYPTOCURRENCY', currency='EUR')
    changes = {'aur': listing}
    content = positions_content(before.decode(), snapshot, (), listing_changes=changes)
    assert 'AUR-EUR' in content
    assert (tmp_path/'holdings.csv').read_bytes() == before
    save(tmp_path, snapshot, listing_changes=changes)
    saved = read_snapshot(tmp_path/'holdings.csv').holdings
    assert saved.loc[saved.id.eq('aur'), 'ticker'].eq('AUR-EUR').all()
    assert load_journal(tmp_path)[0].securities


def test_failed_history_publication_rolls_back_staged_listing(tmp_path, monkeypatch):
    from portfolio_app.instruments import Instrument
    snapshot = setup(tmp_path)
    before = (tmp_path/'holdings.csv').read_bytes()
    original = Path.replace
    def fail(self, destination):
        if Path(destination).name == 'account-journal.json':
            raise OSError('Invented failure')
        return original(self, destination)
    monkeypatch.setattr(Path, 'replace', fail)
    with pytest.raises(OSError, match='Invented'):
        save(tmp_path, snapshot, listing_changes={'aur': Instrument('AUR-EUR', 'Invented Aurora', kind='CRYPTOCURRENCY')})
    assert (tmp_path/'holdings.csv').read_bytes() == before
    assert not load_journal(tmp_path)[0].securities
