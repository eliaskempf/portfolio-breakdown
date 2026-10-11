"""Synthetic settlements: cash timing, corrections and exact-contract P&L."""
from dataclasses import replace

import pandas as pd
import pytest

from portfolio_app.account_journal import Event, append_events
from portfolio_app.derivative_types import PriceObservation
from portfolio_app.investment_history import pnl_breakdown, settlement_history
from test_futures_investments import grouped, project


@pytest.mark.parametrize('short', [False, True])
def test_settlement_history_cash_timing_corrections_and_quantities(tmp_path, short):
    _, journal, _ = grouped(tmp_path)
    sign = -1 if short else 1
    if short:
        journal = replace(journal, events=tuple(replace(e, quantity=-2) if e.id == 'open' else e for e in journal.events))
    journal = append_events(journal, [
        Event('settle', '2026-01-03', 'settlement', position_id='p', price=1010, value_date='2026-01-04'),
        Event('partial', '2026-01-03', 'trade', position_id='p', quantity=-sign, price=1015, sequence=1),
        Event('flat', '2026-01-04', 'settlement', position_id='p', price=1010),
    ])
    history = settlement_history(journal, ['p'])
    assert list(history.Contracts) == [2*sign, sign]
    assert list(history['Realized P&L']) == [200*sign, 0]
    assert list(history['Cash status']) == ['Pending', 'Posted']
    journal = append_events(journal, [
        Event('corrected', '2026-01-03', 'settlement', position_id='p', price=1012,
              value_date='2026-01-04', replaces='settle'),
        Event('receipt', '2026-01-05', 'receive', reference='settle'),
    ])
    history = settlement_history(journal, ['p'])
    assert list(history['Realized P&L']) == [240*sign, -20*sign]
    assert list(history['Cash status']) == ['Posted', 'Posted']
    assert list(history['Posted on']) == ['2026-01-05', '2026-01-04']
    assert settlement_history(journal, ['unrelated']).empty


@pytest.mark.parametrize('reporting,rate', [('EUR',1), ('USD',1/.9), ('GBP',1/1.2)])
def test_realized_and_unrealized_exclude_pending_claim_double_count(tmp_path, reporting, rate):
    service, journal, contract = grouped(tmp_path)
    journal = append_events(journal, [
        Event('settle', '2026-01-03', 'settlement', position_id='p', price=1010, value_date='2026-01-04'),
        Event('tax', '2026-01-03', 'tax', position_id='p', amount=25),
    ])
    marks = {'p': PriceObservation(contract.id, '2026-01-03', 1015)}
    raw, _, investment = project(tmp_path, service, journal, reporting=reporting, marks=marks)
    realized, unrealized = pnl_breakdown(journal, ['p'], raw)
    assert realized == pytest.approx(200*rate)
    assert unrealized == pytest.approx(100*rate)
    assert investment.unrealized_gain_reporting == pytest.approx((300-8-25)*rate)
    paid = append_events(journal, [Event('receive', '2026-01-04', 'receive', reference='settle')])
    paid_raw, _, _ = project(tmp_path, service, paid, reporting=reporting, marks=marks)
    assert pnl_breakdown(paid, ['p'], paid_raw) == pytest.approx((realized, unrealized))
    raw.loc[raw.position_id.eq('p'), 'mark_price'] = float('nan')
    assert pd.isna(pnl_breakdown(journal, ['p'], raw)[1])
    raw.attrs['account_fx_rates'] = {}
    assert all(pd.isna(value) for value in pnl_breakdown(journal, ['p'], raw))


def test_history_retains_old_expiry_final_settlement_and_snapshot_boundary(tmp_path):
    from portfolio_app.account_journal import PositionSpec, add_contract, add_position
    _, journal, contract = grouped(tmp_path)
    old = replace(contract, expiry='2026-01-05')
    journal = replace(journal, contracts=(old,),
                      positions=tuple(replace(p, contract_id=old.id) for p in journal.positions),
                      events=tuple(replace(e, kind='snapshot') if e.id == 'open' else e for e in journal.events))
    journal = add_position(add_contract(journal, contract),
                           PositionSpec('next', 'a', contract.id, investment_id='investment:synthetic'))
    journal = append_events(journal, [
        Event('final', '2026-01-05', 'final_settlement', position_id='p', price=1020),
        Event('new', '2026-01-05', 'trade', position_id='next', quantity=1, price=1030),
        Event('next-day', '2026-01-06', 'settlement', position_id='next', price=1040),
    ])
    history = settlement_history(journal, ['p', 'next'])
    assert list(history.Maturity) == ['2026-01-05', contract.expiry]
    assert list(history.Type) == ['Final settlement', 'Daily settlement']
    assert list(history.Contracts) == [2, 1]
    assert list(history['Realized P&L']) == [400, 100]
