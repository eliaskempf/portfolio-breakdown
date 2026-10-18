"""Synthetic append-only corrections, cash timing and readable provenance."""
from dataclasses import replace
from decimal import Decimal

import pytest

from portfolio_app import account_journal as ledger
from portfolio_app.account_activity import accounting_effects, activity_rows, correct_entry, entry_label, related_entries
from portfolio_app.holdings import DataError
from test_actual_portfolio import invented


def test_replacement_retains_original_and_corrects_cash_once(tmp_path):
    _, journal, _ = invented(tmp_path)
    updated = correct_entry(journal, 'fee', {'amount': Decimal('3')})
    assert updated.events[:-1] == journal.events
    assert updated.events[-1].replaces == 'fee'
    assert ledger.replay(updated).cash['a', 'EUR'] == 19997
    assert accounting_effects(journal, updated) == [{'Balance': 'Cash', 'Account / contract': 'Invented account',
        'Unit': 'EUR', 'Before': '19992.00', 'After': '19997.00'}]
    assert activity_rows(updated)[0]['entry_id'] == updated.events[-1].id
    rows = {r['entry_id']: r for r in activity_rows(updated)}
    assert rows['fee']['Status'] == 'Corrected'
    assert rows[updated.events[-1].id]['Status'] == 'Replacement'
    assert rows['open']['Amount'] is None  # Notional is never a purchase payment.
    assert rows['open']['Account'] == 'Invented account'
    assert 'SYNTH 2027-03-19' in entry_label(updated, updated.events[1])
    reversed_journal = correct_entry(updated, updated.events[-1].id, reverse=True)
    assert ledger.replay(reversed_journal).cash['a', 'EUR'] == 20000
    with pytest.raises(DataError, match='already reversed or corrected'):
        correct_entry(updated, 'fee', {'amount': 1})


def test_correction_chain_keeps_pending_claim_and_receipt_identity(tmp_path):
    _, journal, _ = invented(tmp_path)
    journal = ledger.append_events(journal, [
        ledger.Event('settle', '2026-01-03', 'settlement', account_id='a', position_id='p', price=1010, value_date='2026-01-05'),
        ledger.Event('receive', '2026-01-05', 'receive', account_id='a', reference='settle')])
    first = correct_entry(journal, 'settle', {'price': Decimal('1011')})
    second = correct_entry(first, first.events[-1].id, {'price': Decimal('1012')})
    assert ledger.replay(second).cash['a', 'EUR'] == 20232
    assert not ledger.replay(second).pending
    row = next(r for r in activity_rows(second) if r['entry_id'] == second.events[-1].id)
    assert row['Amount'] == '240.00'
    assert row['Cash timing'] == 'Claim due 2026-01-05 · received 2026-01-05'
    with pytest.raises(DataError, match='Receipt requires'):
        correct_entry(journal, 'settle', {'value_date': '2026-01-06'})
    with pytest.raises(DataError, match='Receipt requires'):
        correct_entry(journal, 'settle', reverse=True)
    assert 'receive' in [e.id for e in related_entries(journal, journal.events[-2])]


def test_claim_is_not_presented_as_already_received_cash(tmp_path):
    _, journal, _ = invented(tmp_path)
    journal = ledger.append_event(journal, ledger.Event('settle', '2026-01-03', 'settlement',
        account_id='a', position_id='p', price=1010, value_date='2026-01-05'))
    row = next(r for r in activity_rows(journal) if r['entry_id'] == 'settle')
    assert row['Amount'] == '200.00' and row['Cash timing'] == 'Claim due 2026-01-05'
    assert ledger.replay(journal).cash['a', 'EUR'] == 19992


def test_invalid_or_linked_correction_cannot_change_source(tmp_path):
    _, journal, _ = invented(tmp_path)
    ledger.save_journal(tmp_path, journal, None)
    before = (tmp_path / ledger.FILE).read_bytes()
    with pytest.raises(DataError, match='nonnegative'):
        correct_entry(journal, 'fee', {'amount': -1})
    with pytest.raises(DataError, match='identity'):
        correct_entry(journal, 'fee', {'reference': 'invented-other'})
    grouped = replace(journal, events=tuple(replace(e, group_id='invented-roll') if e.id == 'open' else e for e in journal.events))
    with pytest.raises(DataError, match='linked transaction'):
        correct_entry(grouped, 'open', {'price': 1001})
    assert (tmp_path / ledger.FILE).read_bytes() == before
