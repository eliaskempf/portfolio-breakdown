"""Offline durable collection, throttling, corrections and automatic journal updates."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Event as Gate

from portfolio_app import account_journal as ledger
from portfolio_app.derivative_market import make_contract, SettlementCache
from portfolio_app.derivative_types import PriceObservation
from portfolio_app.holdings import DataError
from portfolio_app.settlement_archive import SettlementArchive, reconciliation_rows
from portfolio_app.settlement_collection import SettlementCollector, due, read_status, scan_dates, fetch_archived
from test_actual_portfolio import invented


def workspace(tmp_path):
    _,journal,_=invented(tmp_path)
    contract=make_contract('FMWN','2026-03-20')
    journal=replace(journal, contracts=(contract,),positions=(replace(journal.positions[0],contract_id=contract.id),))
    ledger.save_journal(tmp_path,journal,None)
    return journal,contract


def quote(contract,day,price=1010):
    return PriceObservation(contract.id,day,price,kind='daily_settlement',source='Invented source',observed_at='2026-01-07T12:00:00+00:00')


def join(collector,directory):
    collector._workers[str(directory.resolve())].join(10)
    assert not collector.running(directory)
    assert not collector.error(directory)


def test_startup_collection_posts_once_and_rechecks_overlap_without_rewriting(tmp_path):
    journal,contract=workspace(tmp_path)
    now=datetime(2026,1,7,12,tzinfo=timezone.utc)
    calls=[]
    def fetch(c,d):
        calls.append(d)
        return quote(c,d)
    worker=SettlementCollector(fetch=fetch,now=lambda:now)
    original_events=journal.events
    assert worker.schedule(tmp_path,journal)
    join(worker,tmp_path)
    assert sorted(calls)==['2026-01-02','2026-01-05','2026-01-06']
    archive=SettlementArchive(tmp_path)
    assert len(archive.entries(contract))==3
    posted,_=ledger.load_journal(tmp_path)
    assert posted.events[:len(original_events)]==original_events
    assert len([e for e in posted.events if e.kind=='settlement'])==3
    assert ledger.replay(posted).cash['a','EUR']==21992
    assert not worker.schedule(tmp_path,posted)
    now+=timedelta(days=1)
    worker.fetch=lambda c,d:quote(c,d,1020)
    assert worker.schedule(tmp_path,journal)
    join(worker,tmp_path)
    assert archive.load(contract,'2026-01-05').price==1010
    assert archive.latest(contract,'2026-01-05').price==1020
    assert any('Source changed' in row['Status'] for row in reconciliation_rows(journal,archive))
    updated,_=ledger.load_journal(tmp_path)
    assert updated.events[:len(posted.events)]==posted.events
    assert len([e for e in updated.events if e.kind=='settlement'])==4


def test_missing_source_keeps_archive_and_uses_retry_throttle(tmp_path):
    journal,contract=workspace(tmp_path)
    archive=SettlementArchive(tmp_path)
    archive.record(contract,quote(contract,'2026-01-06'))
    now=datetime(2026,1,7,12,tzinfo=timezone.utc)
    def failing(c,d):raise DataError('Invented outage')
    worker=SettlementCollector(fetch=failing,now=lambda:now)
    assert worker.schedule(tmp_path,journal)
    join(worker,tmp_path)
    assert archive.load(contract,'2026-01-06').price==1010
    record=read_status(tmp_path)['contracts'][contract.id]
    assert not due(record,now+timedelta(minutes=59))
    assert due(record,now+timedelta(hours=1))
    assert all(row['status']=='unavailable' for row in record['dates'].values())


def test_single_worker_demo_and_closed_history_retention(tmp_path):
    journal,contract=workspace(tmp_path)
    gate=Gate()
    def fetch(c,d):
        assert gate.wait(5)
        return quote(c,d)
    worker=SettlementCollector(fetch=fetch,now=lambda:datetime(2026,1,7,12,tzinfo=timezone.utc))
    assert not worker.schedule(tmp_path,journal,demo=True)
    assert worker.schedule(tmp_path,journal)
    assert not worker.schedule(tmp_path,journal)
    gate.set();join(worker,tmp_path)
    posted,_=ledger.load_journal(tmp_path)
    closed=ledger.append_event(posted,ledger.Event('close','2026-01-07','trade',position_id='p',quantity=-2,price=1020))
    _,rev=ledger.load_journal(tmp_path);ledger.save_journal(tmp_path,closed,rev)
    worker.now=lambda:datetime(2026,3,1,12,tzinfo=timezone.utc)
    assert worker.schedule(tmp_path,closed)
    join(worker,tmp_path)
    assert not worker.schedule(tmp_path,closed)
    assert len(SettlementArchive(tmp_path).entries(contract))==3


def test_migration_manual_scan_and_canonical_review(tmp_path):
    journal,contract=workspace(tmp_path)
    cache=SettlementCache(tmp_path/'.cache'/'derivative-settlements')
    cache.save(contract,quote(contract,'2026-01-02'),None)
    assert fetch_archived(tmp_path,contract,'2026-01-02',fetch=lambda *_:(_ for _ in ()).throw(AssertionError('offline'))).price==1010
    archive=SettlementArchive(tmp_path)
    scan_dates(tmp_path,contract,['2026-01-02'],fetch=lambda c,d:quote(c,d,1020))
    import pytest
    with pytest.raises(DataError,match='differs'):
        fetch_archived(tmp_path,contract,'2026-01-02')
    archive.accept_latest(contract,'2026-01-02','1020')
    assert fetch_archived(tmp_path,contract,'2026-01-02').price==1020
    assert len(archive.versions(contract,'2026-01-02'))==2
    assert ledger.load_journal(tmp_path)[0]==journal
