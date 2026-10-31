"""Invented account ledgers need no additional history attestation."""
import pytest
from portfolio_app.account_journal import Account, Event, Journal, PositionSpec, append_events, save_journal
from portfolio_app.derivative_types import ContractSpec
from portfolio_app.history_observations import HistoryScope, Observation, ObservationStore, load_observations, save_observations
from portfolio_app.personal_performance import calculate_history


def invented_account_history(directory):
    contract = ContractSpec('SYNTH', '2025-03-21', 'EUR', 10, 1)
    journal = Journal(accounts=(Account('a', 'Invented account A'), Account('b', 'Invented account B')),
        contracts=(contract,), positions=(PositionSpec('closed', 'a', contract.id),))
    journal = append_events(journal, (
        Event('cash-a', '2024-10-01', 'cash_open', account_id='a', currency='EUR', amount=1000),
        Event('cash-b', '2024-10-01', 'cash_open', account_id='b', currency='EUR', amount=2000),
        Event('open', '2024-10-01', 'trade', account_id='a', position_id='closed', quantity=1, price=100),
        Event('settle', '2024-10-02', 'settlement', account_id='a', position_id='closed', price=110),
        Event('close', '2024-10-03', 'trade', account_id='a', position_id='closed', quantity=-1, price=120),
    ))
    save_journal(directory, journal, None)
    store = ObservationStore(tuple(Observation('price-'+stamp, 'price', contract.id, stamp, price, 'EUR')
        for stamp, price in [('2024-10-01', 100), ('2024-10-02', 110)]))
    save_observations(directory, store, None)
    return journal


def test_account_gain_and_returns_follow_applied_events_without_confirmation(tmp_path):
    journal=invented_account_history(tmp_path)
    store=load_observations(tmp_path)[0]
    scope=HistoryScope(accounts=('a',))
    result=calculate_history(journal,scope,store,'2024-10-01','2024-10-03')
    assert result.gain==200 and result.net_contributions==0
    assert result.time_weighted==pytest.approx(.2)
    combined=calculate_history(journal,HistoryScope(accounts=('a','b')),store,'2024-10-01','2024-10-03')
    assert combined.gain==200 and combined.time_weighted==pytest.approx(200/3000)
    changed=append_events(journal,(Event('fee','2024-10-03','fee',account_id='a',currency='EUR',amount=5),))
    assert calculate_history(changed,scope,store,'2024-10-01','2024-10-03').gain==195
    missing=calculate_history(journal,scope,ObservationStore(),'2024-10-01','2024-10-02')
    assert missing.ending_value is None and missing.time_weighted is None
