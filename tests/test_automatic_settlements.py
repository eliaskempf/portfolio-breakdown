"""Synthetic daily settlement cash, duplicate protection and manual-history fences."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from portfolio_app import account_journal as ledger
from portfolio_app.automatic_settlements import apply_archived_settlements
from portfolio_app.derivative_market import make_contract
from portfolio_app.settlement_archive import SettlementArchive
from test_settlement_collection import workspace, quote, join


def save(root,journal):
    _,revision=ledger.load_journal(root)
    ledger.save_journal(root,journal,revision)


@pytest.mark.parametrize('product',['FMWN','FMWO','FMWB'])
@pytest.mark.parametrize('quantity',[2,-2])
def test_automatic_daily_cash_and_portfolio_equity_are_counted_once(tmp_path,product,quantity):
    journal,contract=workspace(tmp_path)
    contract=make_contract(product,'2026-03-20')
    journal=replace(journal,contracts=(contract,),cash_links=(),
        positions=(replace(journal.positions[0],contract_id=contract.id),),
        events=tuple(replace(e,currency=contract.currency,quantity=quantity if e.kind=='trade' else e.quantity) for e in journal.events))
    save(tmp_path,journal)
    archive=SettlementArchive(tmp_path)
    archive.record(contract,quote(contract,'2026-01-02'))
    before=ledger.replay(journal)
    expected=before.cash['a',contract.currency]+before.positions['p'].unsettled_pnl(1010)
    with ThreadPoolExecutor(max_workers=2) as pool:
        applied=list(pool.map(lambda _:apply_archived_settlements(tmp_path),range(2)))
    assert sorted(applied)==[0,1]
    posted,_=ledger.load_journal(tmp_path);after=ledger.replay(posted)
    assert after.cash['a',contract.currency]==expected
    assert after.positions['p'].unsettled_pnl(1010)==0
    assert posted.events[:len(journal.events)]==journal.events
    assert posted.events[-1].source=='Invented source'
    assert posted.events[-1].note.startswith('Automatic daily settlement')
    assert apply_archived_settlements(tmp_path)==0


def test_partial_close_roll_and_late_gap_preserve_total_pnl(tmp_path):
    journal,contract=workspace(tmp_path)
    journal=ledger.append_event(journal,ledger.Event('partial','2026-01-05','trade',position_id='p',quantity=-1,price=1020))
    next_contract=make_contract('FMWN','2026-06-19')
    journal=ledger.add_contract(journal,next_contract)
    journal=ledger.add_position(journal,ledger.PositionSpec('next','a',next_contract.id))
    journal=ledger.roll(journal,
        close=ledger.Event('close','2026-01-06','trade',position_id='p',quantity=-1,price=1030,group_id='roll'),
        opening=ledger.Event('reopen','2026-01-06','trade',position_id='next',quantity=1,price=1040,group_id='roll'))
    save(tmp_path,journal)
    archive=SettlementArchive(tmp_path)
    archive.record(contract,quote(contract,'2026-01-05',1015))
    archive.record(next_contract,quote(next_contract,'2026-01-06',1050))
    apply_archived_settlements(tmp_path)
    first,_=ledger.load_journal(tmp_path)
    assert ledger.replay(first).cash['a','EUR']==25992
    # A missing older day arriving later redistributes daily cash P&L, without
    # changing cumulative profit already settled on the later dates/trades.
    archive.record(contract,quote(contract,'2026-01-02',1010))
    apply_archived_settlements(tmp_path)
    final,_=ledger.load_journal(tmp_path)
    assert ledger.replay(final).cash['a','EUR']==25992
    assert ledger.replay(final).positions['p'].quantity==0
    assert ledger.replay(final).positions['next'].quantity==1
    assert len([e for e in final.events if e.kind=='settlement'])==3


def test_snapshot_and_later_cash_reconciliation_prevent_duplicate_cash(tmp_path):
    journal,contract=workspace(tmp_path)
    journal=replace(journal,events=tuple(replace(e,kind='snapshot') if e.id=='open' else e for e in journal.events))
    journal=ledger.append_event(journal,ledger.Event('adjust','2026-01-05','cash_adjust',account_id='a',currency='EUR',amount=22000))
    save(tmp_path,journal)
    archive=SettlementArchive(tmp_path)
    for day in ('2026-01-02','2026-01-05','2026-01-06'):
        archive.record(contract,quote(contract,day))
    assert apply_archived_settlements(tmp_path)==1
    posted,_=ledger.load_journal(tmp_path)
    assert [e.date for e in posted.events if e.kind=='settlement']==['2026-01-06']


def test_manual_records_voids_and_source_corrections_are_preserved(tmp_path):
    journal,contract=workspace(tmp_path)
    journal=ledger.append_events(journal,[
        ledger.Event('manual','2026-01-02','settlement',position_id='p',price=1005),
        ledger.Event('voided','2026-01-05','settlement',position_id='p',price=1005),
        ledger.Event('void','2026-01-05','void',replaces='voided')])
    save(tmp_path,journal)
    archive=SettlementArchive(tmp_path)
    for day in ('2026-01-02','2026-01-05','2026-01-06'):
        archive.record(contract,quote(contract,day))
    archive.record(contract,quote(contract,'2026-01-06',1020))
    assert apply_archived_settlements(tmp_path)==0
    assert ledger.load_journal(tmp_path)[0]==journal
    archive.accept_latest(contract,'2026-01-06',1020)
    assert apply_archived_settlements(tmp_path)==1
    posted,_=ledger.load_journal(tmp_path)
    assert posted.events[:len(journal.events)]==journal.events
    assert [e.date for e in ledger.effective_events(posted) if e.kind=='settlement']==['2026-01-02','2026-01-06']


def test_cached_price_is_applied_when_network_refresh_is_not_due(tmp_path):
    from datetime import datetime,timezone
    from portfolio_app.settlement_collection import SettlementCollector,_save_status
    journal,contract=workspace(tmp_path)
    now=datetime(2026,1,7,12,tzinfo=timezone.utc)
    _save_status(tmp_path,contract.id,{'attempted_at':now.isoformat(),'dates':{}})
    SettlementArchive(tmp_path).record(contract,quote(contract,'2026-01-06'))
    def offline(*_):raise AssertionError('No new network request is due')
    worker=SettlementCollector(fetch=offline,now=lambda:now)
    assert worker.schedule(tmp_path,journal)
    join(worker,tmp_path)
    posted,_=ledger.load_journal(tmp_path)
    assert len(posted.events)==len(journal.events)+1
    assert not worker.schedule(tmp_path,posted)
