"""Explicit account review using deliberately invented accounts and contracts."""
from dataclasses import replace
from datetime import date

import pytest
from streamlit.testing.v1 import AppTest

from portfolio_app.account_journal import Account, Event, Journal, PositionSpec, append_events, load_journal, save_journal
from portfolio_app.derivative_types import ContractSpec
from portfolio_app.history_coverage_ui import confirm_complete_history
from portfolio_app.history_observations import HistoryScope, Observation, ObservationStore, load_observations, save_observations
from portfolio_app.personal_performance import calculate_history, coverage_reasons


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


def test_account_confirmation_unlocks_independently_calculated_return_only_for_selected_account(tmp_path):
    journal = invented_account_history(tmp_path)
    scope = HistoryScope(accounts=('a',))
    before, revision = load_observations(tmp_path)
    result = calculate_history(journal, scope, before, '2024-10-01', '2024-10-03')
    assert result.time_weighted is None
    confirm_complete_history(tmp_path, journal, scope, '2024-10-01', date.today().isoformat(), revision)
    after, _ = load_observations(tmp_path)
    assert load_journal(tmp_path)[0] == journal
    assert after.observations == before.observations
    assert after.reviews[-1].scope == scope
    result = calculate_history(journal, scope, after, '2024-10-01', '2024-10-03')
    # One contract: (120-100)*10 = 200 profit, 1200/1000-1 = 20%.
    # The intermediate 100 settlement is internal, not a deposit.
    assert result.gain == 200 and result.net_contributions == 0
    assert result.time_weighted == pytest.approx(.2)
    assert coverage_reasons(journal, HistoryScope(accounts=('b',)), after, '2024-10-01', '2024-10-03')
    assert coverage_reasons(journal, HistoryScope(accounts=('a', 'b')), after, '2024-10-01', '2024-10-03')
    changed = append_events(journal, (Event('charge', '2024-10-03', 'fee', account_id='a', currency='EUR', amount=5),))
    assert coverage_reasons(changed, scope, after, '2024-10-01', '2024-10-03')
    missing = replace(after, observations=())
    assert calculate_history(journal, scope, missing, '2024-10-01', '2024-10-02').time_weighted is None


def test_account_picker_and_confirmation_do_not_confirm_other_accounts(tmp_path):
    journal = invented_account_history(tmp_path)
    app = AppTest.from_string(f'''
from pathlib import Path
from portfolio_app.account_journal import load_journal
from portfolio_app.history_coverage_ui import render_account_coverage_review
root = Path({str(tmp_path)!r})
render_account_coverage_review(root, load_journal(root)[0], key='test_')
''').run()
    assert not app.exception
    assert app.selectbox[0].label == 'Account to confirm'
    app.selectbox[0].set_value('b').run()
    app.button[0].click().run()
    assert not app.exception
    assert any('History confirmed' in item.value for item in app.caption)
    store, _ = load_observations(tmp_path)
    assert store.reviews[-1].scope == HistoryScope(accounts=('b',))
    assert load_journal(tmp_path)[0] == journal
    app.selectbox[0].set_value('a').run()
    assert app.button[0].label == 'Confirm complete history'
