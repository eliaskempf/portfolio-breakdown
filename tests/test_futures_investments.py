"""Explicitly invented capital-attribution examples, with no working data."""
from dataclasses import replace
from decimal import Decimal

import pandas as pd
import pytest

from portfolio_app.account_journal import (
    Event, InvestmentSpec, PositionSpec, append_events, add_contract, add_position,
    journal_from_dict, journal_to_dict, roll,
)
from portfolio_app.actual_portfolio import journal_positions
from portfolio_app.derivative_types import PriceObservation
from portfolio_app.futures_investments import assign_investment, investment_positions, roll_position_ids
from portfolio_app.holdings import DataError
from portfolio_app.portfolio import prepare_portfolio
from portfolio_app.positions import read_snapshot
from test_actual_portfolio import invented


def grouped(tmp_path, *, capital=20000):
    service, journal, contract = invented(tmp_path)
    journal = assign_investment(journal, InvestmentSpec('investment:synthetic', 'Invented index investment',
        'a', 'EUR', capital, capital), ['p'])
    return service, journal, contract


def project(tmp_path, service, journal, *, reporting='EUR', marks=None):
    raw = journal_positions(prepare_portfolio(read_snapshot(tmp_path/'holdings.csv').holdings,
        service, reporting_currency=reporting), journal, service, reporting_currency=reporting, marks=marks)
    before = raw.copy(deep=True)
    display = investment_positions(raw, journal)
    pd.testing.assert_frame_equal(raw, before)
    assert display.current_value_reporting.sum(min_count=len(display)) == pytest.approx(
        raw.current_value_reporting.sum(min_count=len(raw)))
    return raw, display, display.set_index('position_id').loc['investment:synthetic']


@pytest.mark.parametrize('reporting,rate', [('EUR',1), ('USD',1/.9), ('GBP',1/1.2)])
def test_initial_allocation_mark_and_settlement_keep_gain_visible(tmp_path, reporting, rate):
    service, journal, contract = grouped(tmp_path)
    marks = {'p': PriceObservation(contract.id, '2026-01-03', Decimal('1050'))}
    _, display, investment = project(tmp_path, service, journal, reporting=reporting, marks=marks)
    assert len(display) == 1  # Fully attributed cash is not a second investment row.
    assert investment.current_value_reporting == pytest.approx(20992*rate)
    assert investment.unrealized_gain_reporting == pytest.approx(992*rate)
    assert investment.return_pct == pytest.approx(4.96)
    settled = append_events(journal, [Event('settle','2026-01-03','settlement', position_id='p', price=1050)])
    _, after, same = project(tmp_path, service, settled, reporting=reporting, marks=marks)
    assert same.current_value_reporting == pytest.approx(investment.current_value_reporting)
    assert same.unrealized_gain_reporting == pytest.approx(investment.unrealized_gain_reporting)
    assert len(after) == 1


def test_partial_close_roll_taxes_and_full_close_preserve_history(tmp_path):
    service, journal, contract = grouped(tmp_path)
    journal = append_events(journal, [Event('partial', '2026-01-03', 'trade', position_id='p', quantity=-1, price=1020)])
    successor = replace(contract, expiry='2027-06-18')
    journal = add_position(add_contract(journal, successor), PositionSpec('next','a',successor.id, investment_id='investment:synthetic'))
    journal = roll(journal,
        close=Event('roll-old','2026-01-04','trade',position_id='p',quantity=-1,price=1030,group_id='roll'),
        opening=Event('roll-new','2026-01-04','trade',position_id='next',quantity=1,price=1080,group_id='roll'))
    journal = append_events(journal, [Event('tax','2026-01-05','tax',position_id='p',amount=25),
        Event('close','2026-01-06','trade',position_id='next',quantity=-1,price=1090)])
    _, display, row = project(tmp_path, service, journal)
    assert len(display) == 1 and row.contracts == 0 and row.signed_notional_reporting == 0
    assert row.current_value_reporting == 20567 and row.unrealized_gain_reporting == 567
    assert roll_position_ids(journal, 'next') == ('p','next')
    assert display.attrs['investment_members'] == {'investment:synthetic': ('p','next')}
    restarted = journal_from_dict(journal_to_dict(journal))
    assert restarted == journal


def test_delayed_settlement_and_expenses_do_not_duplicate_gain(tmp_path):
    service, journal, _ = grouped(tmp_path)
    journal = append_events(journal, [
        Event('settle','2026-01-03','settlement',position_id='p',price=1050,value_date='2026-01-04'),
        Event('tax','2026-01-03','tax',position_id='p',amount=25,value_date='2026-01-04'),
        Event('later-fee','2026-01-03','fee',position_id='p',amount=10,value_date='2026-01-04'),
    ])
    _, _, before = project(tmp_path, service, journal)
    assert before.current_value_reporting == 20957
    received = append_events(journal, [Event('received-'+reference,'2026-01-04','receive',reference=reference)
        for reference in ('settle','tax','later-fee')])
    _, _, after = project(tmp_path, service, received)
    assert after.unrealized_gain_reporting == before.unrealized_gain_reporting == 957


def test_allocation_changes_borrowing_and_reservations_are_not_gains(tmp_path):
    service, journal, _ = grouped(tmp_path, capital=15000)
    journal = append_events(journal, [Event('loan','2026-01-03','borrow',account_id='a',currency='EUR',reference='invented',amount=3000),
        Event('pledge','2026-01-03','reserve',account_id='a',currency='EUR',collateral_id='margin',amount=4000)])
    journal = assign_investment(journal, replace(journal.investments[0], allocated_capital=18000), ['p'])
    _, display, row = project(tmp_path, service, journal)
    assert row.current_value_reporting == 17992 and row.unrealized_gain_reporting == -8
    assert row.cost_basis_reporting == 15000 and row.net_funding_native == 3000
    assert display.loc[display.instrument_type.eq('cash'), 'current_value_reporting'].sum() == 5000
    assert display.loc[display.instrument_type.eq('liability'), 'current_value_reporting'].sum() == -3000
    with pytest.raises(DataError, match='exceeds'):
        assign_investment(journal, replace(journal.investments[0], allocated_capital=25000), ['p'])
    with pytest.raises(DataError, match='reference'):
        assign_investment(journal, replace(journal.investments[0], reference_capital=18000), ['p'])


def test_two_investments_share_account_without_duplicating_capital(tmp_path):
    service, journal, contract = grouped(tmp_path, capital=10000)
    journal = add_position(journal, PositionSpec('second','a',contract.id))
    journal = append_events(journal, [Event('second-open','2026-01-03','trade',position_id='second',quantity=-1,price=1000)])
    journal = assign_investment(journal, InvestmentSpec('investment:second','Other invented investment','a','EUR',10000,10000), ['second'])
    _, display, _ = project(tmp_path, service, journal)
    assert len(display) == 2 and display.current_value_reporting.sum() == 19992
    assert display.signed_notional_reporting.sum() == 10000
    with pytest.raises(DataError, match='another investment'):
        assign_investment(journal, InvestmentSpec('bad','Bad','a','EUR',0,0), ['second'])


def test_snapshot_performance_only_since_recorded_start(tmp_path):
    service, journal, _ = grouped(tmp_path)
    journal = replace(journal, events=tuple(replace(e, kind='snapshot') if e.id == 'open' else e for e in journal.events))
    _, _, row = project(tmp_path, service, journal)
    assert not row.history_complete
    assert row.unrealized_gain_reporting == -8
    assert 'earlier performance is unknown' in row.performance_note


def test_foreign_currency_fees_pending_receipts_and_current_fx(tmp_path):
    service, journal, _ = grouped(tmp_path)
    journal = append_events(journal, [Event('usd','2026-01-02','cash_open',account_id='a',currency='USD',amount=100),
        Event('usd-fee','2026-01-03','fee',position_id='p',currency='USD',amount=10,value_date='2026-01-04')])
    _, display, before = project(tmp_path, service, journal)
    assert before.unrealized_gain_reporting == -17
    assert before.current_value_reporting == 19983
    assert display.loc[display.instrument_type.eq('cash'), 'current_value_reporting'].sum() == 90
    journal = append_events(journal, [Event('usd-receive','2026-01-04','receive',reference='usd-fee')])
    _, _, after = project(tmp_path, service, journal)
    assert after.unrealized_gain_reporting == before.unrealized_gain_reporting
    assert after.current_value_reporting == before.current_value_reporting


def test_correction_recomputes_gain_and_cash_attribution(tmp_path):
    service, journal, _ = grouped(tmp_path)
    journal = append_events(journal, [Event('settle','2026-01-03','settlement',position_id='p',price=1010),
        Event('correct','2026-01-03','settlement',position_id='p',price=1020,replaces='settle')])
    _, _, row = project(tmp_path, service, journal)
    assert row.unrealized_gain_reporting == 392
    journal = append_events(journal, [Event('void','2026-01-03','void',replaces='correct')])
    _, _, row = project(tmp_path, service, journal)
    assert row.unrealized_gain_reporting == -8


def test_withdrawal_leaves_visible_allocation_shortfall_without_changing_wealth(tmp_path):
    service, journal, _ = grouped(tmp_path)
    journal = append_events(journal, [Event('withdraw','2026-01-03','cash_adjust',account_id='a',currency='EUR',amount=-1000)])
    _, display, row = project(tmp_path, service, journal)
    assert row.unrealized_gain_reporting == -8
    assert display.loc[display.instrument_type.eq('cash'), 'current_value_reporting'].sum() == -1000
    assert 'allocation shortfall' in display.loc[display.instrument_type.eq('cash'), 'valuation_note'].iloc[0]


def test_missing_fx_withholds_group_totals(tmp_path):
    service, journal, _ = grouped(tmp_path)
    journal = append_events(journal, [Event('usd','2026-01-02','cash_open',account_id='a',currency='USD',amount=100),
        Event('usd-fee','2026-01-03','fee',position_id='p',currency='USD',amount=10)])
    raw = journal_positions(prepare_portfolio(read_snapshot(tmp_path/'holdings.csv').holdings,service), journal,service)
    raw.attrs['account_fx_rates']['USD'] = None
    raw.loc[raw.quote_currency.eq('USD'), 'current_value_reporting'] = float('nan')
    row = investment_positions(raw,journal).set_index('position_id').loc['investment:synthetic']
    assert pd.isna(row.current_value_reporting) and pd.isna(row.unrealized_gain_reporting)


def test_legacy_journal_loads_without_allocating_cash_and_roll_requires_same_owner(tmp_path):
    _, journal, contract = invented(tmp_path)
    raw = journal_to_dict(journal)
    raw.pop('investments')
    for item in raw['positions']:
        item.pop('investment_id')
    assert journal_from_dict(raw) == journal
    journal = assign_investment(journal, InvestmentSpec('investment:synthetic','Invented','a','EUR',20000,20000), ['p'])
    next_contract = replace(contract,expiry='2027-06-18')
    journal = add_position(add_contract(journal,next_contract),PositionSpec('next','a',next_contract.id))
    with pytest.raises(DataError, match='identity'):
        roll(journal, close=Event('old','2026-01-03','trade',position_id='p',quantity=-2,price=1000,group_id='r'),
            opening=Event('next','2026-01-03','trade',position_id='next',quantity=2,price=1100,group_id='r'))


@pytest.mark.parametrize('native,rate', [('EUR', 1), ('USD', .9), ('GBP', 1.2)])
def test_native_account_and_contract_capital_currencies(tmp_path, native, rate):
    service, journal, contract = invented(tmp_path)
    # All inputs are explicitly synthetic; adapt the invented account denomination.
    path = tmp_path/'holdings.csv'
    path.write_text(path.read_text().replace(',EUR,', ','+native+','))
    contract = replace(contract, currency=native)
    journal = replace(journal, contracts=(contract,),
        cash_links=tuple(replace(link, currency=native) for link in journal.cash_links),
        events=tuple(replace(event, currency=native) if event.currency else event for event in journal.events))
    journal = assign_investment(journal, InvestmentSpec('investment:synthetic','Invented native investment',
        'a',native,20000,20000), ['p'])
    journal = append_events(journal, [Event('settle','2026-01-03','settlement',position_id='p',price=1050)])
    _, _, row = project(tmp_path, service, journal)
    assert row.current_value_reporting == pytest.approx(20992*rate)
    assert row.unrealized_gain_reporting == pytest.approx(992*rate)
    assert row.return_pct == pytest.approx(4.96)
