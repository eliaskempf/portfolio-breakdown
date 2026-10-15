"""Offline publication windows, business-date rollover and concurrent source checks."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest
from portfolio_app import account_journal as ledger
from portfolio_app.automatic_settlements import apply_archived_settlements
from portfolio_app.derivative_market import fetch_settlement, SettlementNotPublished, SettlementUnavailable
from portfolio_app.futures_clock import EXCHANGE_ZONE, business_date, settlement_reference
from portfolio_app.futures_valuation import load_valuations
from portfolio_app.holdings import DataError
from portfolio_app.settlement_archive import SettlementArchive
from portfolio_app.settlement_collection import SettlementCollector, due_days, scan_dates, read_status, _save_status
from test_settlement_collection import workspace, quote, join


def test_available_today_values_immediately_cash_waits_for_exchange_midnight(tmp_path):
    journal,contract = workspace(tmp_path)
    now = datetime(2026,1,7,18,tzinfo=EXCHANGE_ZONE)
    worker = SettlementCollector(fetch=lambda c,d:quote(c,d),now=lambda:now)
    assert worker.schedule(tmp_path,journal)
    join(worker,tmp_path)
    current,_ = ledger.load_journal(tmp_path)
    assert '2026-01-07' in SettlementArchive(tmp_path).entries(contract)
    assert not any(e.date=='2026-01-07' and e.kind=='settlement' for e in current.events)
    values,_ = load_valuations(tmp_path,current,now=now)
    assert values['p'].observation.date == '2026-01-07'
    # UTC still has yesterday's date at this point in Berlin.
    now = datetime(2026,1,8,0,1,tzinfo=EXCHANGE_ZONE).astimezone(timezone.utc)
    assert now.date().isoformat() == '2026-01-07'
    # Persist all source checks as not due, so eligibility must be triggered by
    # the business date, independently of a new archive observation.
    records = read_status(tmp_path)['contracts'][contract.id]
    for row in records['dates'].values():
        row.update(attempted_at=now.isoformat(),next_attempt_at=(now+timedelta(days=1)).isoformat())
    _save_status(tmp_path,contract.id,records)
    worker.fetch = lambda *_: (_ for _ in ()).throw(AssertionError('No download needed'))
    assert worker.schedule(tmp_path,current)
    join(worker,tmp_path)
    final,_ = ledger.load_journal(tmp_path)
    assert len([e for e in final.events if e.kind=='settlement' and e.date=='2026-01-07']) == 1
    assert apply_archived_settlements(tmp_path,today=business_date(now)) == 0


def test_pending_publication_bounded_retries_and_old_gaps(tmp_path):
    _,contract = workspace(tmp_path)
    now = datetime(2026,1,7,17,30,tzinfo=EXCHANGE_ZONE)
    def pending(*_): raise SettlementNotPublished('Invented not published')
    result = scan_dates(tmp_path,contract,['2026-01-07','2026-01-02'],fetch=pending,now=now)
    assert result['2026-01-07']['status']=='not_published'
    assert datetime.fromisoformat(result['2026-01-07']['next_attempt_at']) == now+timedelta(minutes=15)
    assert datetime.fromisoformat(result['2026-01-02']['next_attempt_at']) == now+timedelta(days=1)
    record = read_status(tmp_path)['contracts'][contract.id]
    assert due_days(record,{'2026-01-07'},now+timedelta(minutes=14)) == []
    assert due_days(record,{'2026-01-07'},now+timedelta(minutes=15)) == ['2026-01-07']
    later = now+timedelta(hours=2)
    result = scan_dates(tmp_path,contract,['2026-01-07'],fetch=pending,now=later)
    assert datetime.fromisoformat(result['2026-01-07']['next_attempt_at']) == later+timedelta(hours=1)
    assert due_days({}, {'2026-01-07'}, now-timedelta(minutes=1), force=True) == []


def test_new_business_date_bypasses_success_throttle_and_dst_is_local():
    checked = datetime(2026,1,7,23,59,tzinfo=EXCHANGE_ZONE)
    record = {'dates':{'2026-01-07':dict(status='checked',attempted_at=checked.isoformat(),
                                      next_attempt_at=(checked+timedelta(days=1)).isoformat())}}
    assert due_days(record,{'2026-01-07'},checked+timedelta(minutes=2)) == ['2026-01-07']
    winter = settlement_reference(datetime(2026,1,7).date()).astimezone(timezone.utc)
    summer = settlement_reference(datetime(2026,7,7).date()).astimezone(timezone.utc)
    assert winter.hour==16 and summer.hour==15


def test_provider_distinguishes_not_published_historical_gap_and_invalid_response(tmp_path):
    _,contract = workspace(tmp_path)
    raw = dict(meta=dict(productCode='FMWN',isin='DE000A11Q364'),header={'tradingDates':['06-01-2026 12:00']})
    with pytest.raises(SettlementNotPublished):
        fetch_settlement(contract,'2026-01-07',fetch=lambda _:json.dumps(raw))
    with pytest.raises(SettlementUnavailable):
        fetch_settlement(contract,'2026-01-06',fetch=lambda _:json.dumps(raw))
    raw['meta']['productCode']='OTHER'
    with pytest.raises(DataError) as result:
        fetch_settlement(contract,'2026-01-07',fetch=lambda _:json.dumps(raw))
    assert not isinstance(result.value,(SettlementNotPublished,SettlementUnavailable))


def test_late_old_response_retains_versions_but_not_latest_status(tmp_path):
    _,contract = workspace(tmp_path)
    archive = SettlementArchive(tmp_path)
    # The older response has a lexically later local wall time (13:00+01:00
    # versus 12:30+00:00); ordering must compare instants.
    old_time=datetime(2026,1,7,13,tzinfo=EXCHANGE_ZONE)
    new_time=datetime(2026,1,7,12,30,tzinfo=timezone.utc)
    latest=replace(quote(contract,'2026-01-06',1005),observed_at=new_time.isoformat())
    archive.record(contract,latest)
    archive.record(contract,replace(latest,price=1000,observed_at=old_time.isoformat()))
    assert archive.latest(contract,latest.date).price==1005
    assert len(archive.versions(contract,latest.date))==2
    _save_status(tmp_path,contract.id,{'dates':{latest.date:dict(status='checked',attempted_at=new_time.isoformat())}})
    _save_status(tmp_path,contract.id,{'dates':{latest.date:dict(status='failed',attempted_at=old_time.isoformat())}})
    assert read_status(tmp_path)['contracts'][contract.id]['dates'][latest.date]['status']=='checked'


def test_weekend_has_no_new_dates_but_completed_friday_still_posts(tmp_path):
    journal,contract=workspace(tmp_path)
    from portfolio_app.settlement_collection import tracked_contracts
    saturday=datetime(2026,1,10,12,tzinfo=EXCHANGE_ZONE)
    days=tracked_contracts(journal,saturday.date(),include_today=True)[contract.id][1]
    assert '2026-01-09' in days and '2026-01-10' not in days
