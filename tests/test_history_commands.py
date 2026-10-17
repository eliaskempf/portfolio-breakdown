"""Synthetic workspaces exercise migration, review, crash safety and authority."""
from dataclasses import replace
from decimal import Decimal as D
import json
import pytest

from portfolio_app.account_journal import load_journal, journal_to_dict
from portfolio_app.history_commands import book_for, save_history
from portfolio_app.holdings import DataError
from portfolio_app.positions import read_snapshot, save_position
from portfolio_app.security_history import SecurityEvent as E, append_evidence, effective_holdings


def setup(tmp_path):
    path=tmp_path/'holdings.csv'
    path.write_text('id,name,shares,acquisition_price,acquisition_currency\ninvented,Invented,12,10,EUR\n')
    snapshot=read_snapshot(path)
    journal,revision=load_journal(tmp_path)
    book=book_for(journal,snapshot.holdings.iloc[0])
    events=(E('open','opening','2024-01-01',quantity=D(10),amount=D(100),currency='EUR'),
            E('buy','buy','2024-02-01',quantity=D(2),price=D(10),currency='EUR',charges_known=True))
    book=append_evidence(book,events)
    return path,snapshot,replace(book,active_ids=('open','buy'))


def test_activation_reconstructs_without_adding_shares_twice_and_raw_snapshot_remains(tmp_path):
    path,snapshot,book=setup(tmp_path)
    journal=save_history(tmp_path,book,expected_journal_revision=None,expected_holdings_revision=snapshot.revision,
        activate=True,accept_balance_change=True)
    current=read_snapshot(path)
    assert effective_holdings(current.holdings,journal.securities).iloc[0].shares==12
    assert current.holdings.iloc[0].shares==12
    assert len(journal.securities[0].records)==2
    assert 'position_key' in current.holdings
    assert load_journal(tmp_path)[0]==journal
    with pytest.raises(DataError,match='changed'):
        save_history(tmp_path,book,expected_journal_revision=None,expected_holdings_revision=snapshot.revision,activate=True)


def test_incomplete_evidence_persists_and_summary_user_can_still_edit(tmp_path):
    path,snapshot,book=setup(tmp_path)
    book=append_evidence(book,(E('missing','buy',quantity=D(1)),))
    save_history(tmp_path,book,expected_journal_revision=None,expected_holdings_revision=snapshot.revision)
    current=read_snapshot(path)
    assert not load_journal(tmp_path)[0].securities[0].active
    save_position(path,{'shares':'13'},expected_revision=current.revision,position_id='position-0')
    assert effective_holdings(read_snapshot(path).holdings,load_journal(tmp_path)[0].securities).iloc[0].shares==13


def test_discrepancy_requires_explicit_acceptance_and_managed_edits_are_guarded(tmp_path):
    path,snapshot,book=setup(tmp_path)
    book=replace(book,active_ids=('open',))
    with pytest.raises(DataError,match='Resolve'):
        save_history(tmp_path,book,expected_journal_revision=None,expected_holdings_revision=snapshot.revision,activate=True)
    assert not (tmp_path/'account-journal.json').exists()
    save_history(tmp_path,book,expected_journal_revision=None,expected_holdings_revision=snapshot.revision,activate=True,accept_balance_change=True)
    with pytest.raises(DataError,match='History'):
        save_position(path,{'shares':'99'},expected_revision=read_snapshot(path).revision,position_id='position-0')


def test_failed_journal_publish_leaves_source_balance_intact(tmp_path,monkeypatch):
    path,snapshot,book=setup(tmp_path)
    from pathlib import Path
    original=Path.replace
    def fail(self,destination):
        if Path(destination).name=='account-journal.json':
            raise OSError('Synthetic publish failure')
        return original(self,destination)
    monkeypatch.setattr(Path,'replace',fail)
    with pytest.raises(OSError):
        save_history(tmp_path,book,expected_journal_revision=None,expected_holdings_revision=snapshot.revision,activate=True,accept_balance_change=True)
    assert read_snapshot(path).holdings.iloc[0].shares==12
    assert not load_journal(tmp_path)[0].securities


def test_version_one_roundtrip_does_not_claim_migration():
    from portfolio_app.account_journal import Journal,journal_from_dict
    raw=journal_to_dict(Journal())
    assert 'securities' not in raw and 'capital_history' not in raw
    assert journal_from_dict(raw).version==1


def test_history_backup_restore_preserves_evidence_and_observations(tmp_path):
    directory=tmp_path/'synthetic-source';directory.mkdir()
    path,snapshot,book=setup(directory)
    save_history(directory,book,expected_journal_revision=None,expected_holdings_revision=snapshot.revision,
                 activate=True,accept_balance_change=True)
    from portfolio_app.history_observations import Observation,ObservationStore,save_observations,load_observations
    observations=ObservationStore((Observation('price','price',book.position_id,'2024-01-01',D(10),'EUR'),))
    save_observations(directory,observations,None)
    from portfolio_app.backup import create_backup,inspect_backup,commit_restore
    archive=create_backup(directory)
    draft=inspect_backup(archive)
    destination=tmp_path/'synthetic-restored'
    commit_restore(draft,destination,directory)
    assert load_journal(destination)[0]==load_journal(directory)[0]
    assert load_observations(destination)[0]==observations


def test_unknown_opening_fifo_cost_can_be_manually_reconciled_without_inventing_lots(tmp_path):
    path,snapshot,book=setup(tmp_path)
    sell=E('sale','sell','2024-03-01',quantity=D(1),price=D(12),currency='EUR',charges_known=True)
    cost=E('cost','cost','2024-03-02',amount=D(110),currency='EUR')
    book=append_evidence(book,(sell,cost))
    book=replace(book,active_ids=(*book.active_ids,'sale','cost'))
    journal=save_history(tmp_path,book,expected_journal_revision=None,expected_holdings_revision=snapshot.revision,
        activate=True,accept_balance_change=True)
    row=effective_holdings(read_snapshot(path).holdings,journal.securities).iloc[0]
    assert row.shares==11 and row.acquisition_price==10
    from portfolio_app.security_history import replay_securities
    assert not replay_securities(journal.securities).positions[book.position_id].lots[0].exact


def test_position_completeness_confirmation_is_used_and_new_evidence_invalidates_it(tmp_path):
    from portfolio_app.personal_performance import coverage_reasons
    from portfolio_app.history_observations import HistoryScope,ObservationStore
    path,snapshot,book=setup(tmp_path)
    journal=save_history(tmp_path,book,expected_journal_revision=None,expected_holdings_revision=snapshot.revision,
        activate=True,accept_balance_change=True,complete_from='2024-01-01',complete_through='2025-01-01')
    scope=HistoryScope((book.position_id,))
    assert not coverage_reasons(journal,scope,ObservationStore(),'2024-01-01','2025-01-01')
    changed=append_evidence(journal.securities[0],(E('missing','note',note='Unresolved synthetic evidence'),))
    journal=replace(journal,securities=(changed,))
    assert coverage_reasons(journal,scope,ObservationStore(),'2024-01-01','2025-01-01')


def test_zero_start_requires_confirmation_and_is_not_a_purchase(tmp_path):
    from portfolio_app.history_commands import reconstruction_candidate
    path,snapshot,book=setup(tmp_path)
    draft=replace(book,records=(book.records[1],),active_ids=())
    with pytest.raises(DataError,match='Starting holding unknown'):
        reconstruction_candidate(draft,('buy',))
    candidate=reconstruction_candidate(draft,('buy',),starts_at_zero=True)
    opening=candidate.records[-1]
    assert opening.kind=='opening' and opening.quantity==0 and opening.date=='2024-01-31'
    assert opening.source=='confirmed_zero_start'
    assert reconstruction_candidate(candidate,('buy',),starts_at_zero=True)==candidate
    assert not draft.active and not (tmp_path/'account-journal.json').exists()
    with pytest.raises(DataError,match='not both'):
        reconstruction_candidate(book,('open','buy'),starts_at_zero=True)


def test_undated_evidence_cannot_be_reconstructed_from_an_assumed_date(tmp_path):
    from portfolio_app.history_commands import reconstruction_candidate
    path,snapshot,book=setup(tmp_path)
    draft=replace(book,records=(E('undated','buy',quantity=D(1)),),active_ids=())
    with pytest.raises(DataError,match='Supply dates'):
        reconstruction_candidate(draft,('undated',),starts_at_zero=True)


def test_correction_preserves_authority_precision_and_other_fees(tmp_path):
    from portfolio_app.history_commands import correct_transaction
    from portfolio_app.security_history import Charge
    path,snapshot,book=setup(tmp_path)
    original=replace(book.records[1],amount=D('20'),price=D(10),charges=(Charge(D('1.123456'),'USD','2024-02-02'),Charge(D('2.987654'),'EUR',kind='tax')),charges_known=True)
    book=replace(book,records=(book.records[0],original))
    edited=correct_transaction(book,original.id,amount=D('21.000001'),price=None)
    assert edited.active_ids==book.active_ids
    assert edited.records[:len(book.records)]==book.records
    new=edited.records[-1]
    assert new.amount==D('21.000001') and new.charges==original.charges
    assert new.replaces==original.id
    assert correct_transaction(book,original.id,amount=original.amount)==book
    with pytest.raises(DataError,match='latest version'):
        correct_transaction(edited,original.id,note='An obsolete edit')
