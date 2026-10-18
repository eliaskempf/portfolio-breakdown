"""Synthetic persistence tests for evidence, atomic activation and recovery."""
from dataclasses import replace
from pathlib import Path
import pytest

from portfolio_app.account_journal import load_journal
from portfolio_app.holdings import DataError
from portfolio_app.positions import read_snapshot
from portfolio_app.transaction_import_commands import prepare_positions, save_transaction_import
from portfolio_app.security_history import replay_securities, effective_holdings
from test_transaction_imports import scenario, MAPPING


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
    assert result.version==3 and result.transaction_imports==journal.transaction_imports
    assert replay_securities(result.securities).positions['desk'].quantity==3
