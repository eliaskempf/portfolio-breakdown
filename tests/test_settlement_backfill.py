"""Invented historical cash postings; no network or personal workspace access."""
from dataclasses import replace
from decimal import Decimal

import pytest

from portfolio_app import account_journal as ledger
from portfolio_app.derivative_types import PriceObservation
from portfolio_app.holdings import DataError
from portfolio_app.settlement_backfill import settlement_days, retrieve_settlements, propose_settlements
from test_futures_investments import grouped, project


def observed(contract, day, price=1010):
    return PriceObservation(contract.id, day, price, kind='daily_settlement', source='Invented settlement source')


def test_week_backfill_partial_close_and_duplicate_prevention(tmp_path):
    service, journal, contract = grouped(tmp_path)
    journal = ledger.append_event(journal, ledger.Event('reduce','2026-01-06','trade',position_id='p',quantity=-1,price=1020))
    prices = {'2026-01-02':1005, '2026-01-05':1010, '2026-01-06':1025, '2026-01-07':1030}
    days = retrieve_settlements(journal,'p','2026-01-02','2026-01-07',fetch=lambda c,d:observed(c,d,prices[d]))
    assert [d.date for d in days] == list(prices)
    assert all(d.status == 'Available' for d in days)
    updated = propose_settlements(journal,'p','2026-01-02','2026-01-07',[d.observation for d in days])
    assert len(journal.events) == 4  # fetch/propose are pure
    assert ledger.replay(updated).cash['a','EUR'] == Decimal('20492')
    assert ledger.replay(updated).positions['p'].quantity == 1
    marks = {'p':observed(contract,'2026-01-07',1030)}
    before = project(tmp_path,service,journal,marks=marks)[2]
    after = project(tmp_path,service,updated,marks=marks)[2]
    assert after.current_value_reporting == before.current_value_reporting
    assert after.unrealized_gain_reporting == before.unrealized_gain_reporting
    assert all(d.status == 'Already recorded' for d in settlement_days(updated,'p','2026-01-02','2026-01-07'))
    with pytest.raises(DataError,match='duplicated'):
        propose_settlements(updated,'p','2026-01-02','2026-01-07',[days[0].observation])


def test_missing_days_wrong_identity_and_partial_success(tmp_path):
    _, journal, contract = grouped(tmp_path)
    def fetch(c,d):
        if d == '2026-01-05': raise DataError('Invented unavailable business date')
        if d == '2026-01-06': return observed(c,'2026-01-02')
        return observed(c,d)
    days = retrieve_settlements(journal,'p','2026-01-02','2026-01-06',fetch=fetch)
    assert [d.status for d in days] == ['Available','Unavailable','Unavailable']
    assert 'different' in days[-1].message
    updated = propose_settlements(journal,'p','2026-01-02','2026-01-06',[days[0].observation])
    assert len(updated.events) == len(journal.events)+1
    with pytest.raises(DataError,match='duplicated'):
        propose_settlements(journal,'p','2026-01-02','2026-01-06',[days[0].observation]*2)


def test_snapshot_cash_reconciliation_and_final_day_are_not_double_posted(tmp_path):
    _, journal, contract = grouped(tmp_path)
    snapshot = replace(journal,events=tuple(replace(e,kind='snapshot') if e.id=='open' else e for e in journal.events))
    days = settlement_days(snapshot,'p','2026-01-01','2026-01-05')
    assert [d.status for d in days] == ['Included in snapshot','Included in snapshot','Ready to retrieve']
    adjusted = ledger.append_event(journal,ledger.Event('adjust','2026-01-05','cash_adjust',account_id='a',currency='EUR',amount=100))
    assert all('reconciliation' in d.status for d in settlement_days(adjusted,'p','2026-01-02','2026-01-05'))
    expiring = replace(journal,contracts=(replace(contract,expiry='2026-01-06'),),
        positions=(replace(journal.positions[0],contract_id=replace(contract,expiry='2026-01-06').id),))
    assert settlement_days(expiring,'p','2026-01-06','2026-01-06')[0].status == 'Expiry: enter manually'


def test_closed_quantity_corrections_and_range_limits(tmp_path):
    _,journal,_ = grouped(tmp_path)
    closed = ledger.append_event(journal,ledger.Event('close','2026-01-05','trade',position_id='p',quantity=-2,price=1010))
    assert settlement_days(closed,'p','2026-01-05','2026-01-06')[0].status == 'No open contracts'
    voided = ledger.append_event(closed,ledger.Event('void','2026-01-06','void',replaces='close'))
    assert settlement_days(voided,'p','2026-01-05','2026-01-06')[0].status == 'Ready to retrieve'
    for start,end in [('2026-01-05','2026-01-02'),('2026-01-01','2026-02-02'),('2099-01-01','2099-01-02')]:
        with pytest.raises(DataError): settlement_days(journal,'p',start,end)


@pytest.mark.parametrize('currency,multiplier', [('EUR',100),('USD',10),('GBP',5)])
def test_signed_backfill_preserves_native_currency(tmp_path,currency,multiplier):
    from portfolio_app.derivative_types import ContractSpec
    contract=ContractSpec('SYNTH','2026-03-20',currency,multiplier,1)
    journal=ledger.Journal(accounts=(ledger.Account('a','Invented'),),contracts=(contract,),
        positions=(ledger.PositionSpec('p','a',contract.id),),events=(
        ledger.Event('open','2026-01-02','trade',position_id='p',quantity=-2,price=1000),))
    candidate=propose_settlements(journal,'p','2026-01-02','2026-01-02',[observed(contract,'2026-01-02',1010)])
    assert ledger.replay(candidate).cash['a',currency] == -20*multiplier
