"""Entirely invented accounts/contracts and temporary documents; no live data."""
from dataclasses import replace
from decimal import Decimal as D
import json

import pytest

from portfolio_app.account_journal import (
    Account, CashLink, Event, HoldingLink, Journal, MarginRequirement, PositionSpec,
    add_account, add_contract, add_position, append_event, append_events,
    effective_events, load_journal, replay, roll, save_journal,
)
from portfolio_app.derivative_types import ContractSpec, PriceObservation, decimal, money
from portfolio_app.holdings import DataError

DAY = '2026-01-05'


def invented(denomination='EUR'):
    contract = ContractSpec('SYNTH', '2026-03-20', denomination, '10', '.5', name='Invented index future')
    return Journal(accounts=(Account('broker', 'Invented broker'), Account('bank', 'Invented bank', 'bank')),
        contracts=(contract,), positions=(PositionSpec('future', 'broker', contract.id),))


def event(id, kind, **fields):
    return Event(id, fields.pop('date', DAY), kind, **fields)


def trade(id, quantity, price, **fields):
    return event(id, 'trade', position_id='future', quantity=quantity, price=price, **fields)


def cash(journal, account='broker', denomination='EUR'):
    return replay(journal).cash.get((account, denomination), D(0))


@pytest.mark.parametrize('denomination', ['EUR', 'USD', 'GBP'])
def test_lifecycle_native_numeric_example(denomination):
    journal = append_events(invented(denomination), (
        event('cash', 'cash_open', account_id='broker', currency=denomination, amount='20000'),
        trade('open', 2, '1000'),
        event('entry-fee', 'fee', position_id='future', amount='8'),
        event('collateral', 'reserve', account_id='broker', currency=denomination, amount='4000', collateral_id='cash-margin'),
    ))
    state = replay(journal)
    assert cash(journal, denomination=denomination) == D('19992')
    assert state.available_cash('broker', denomination) == D('15992')
    assert state.positions['future'].unsettled_pnl('1010') == D('200')
    journal = append_event(journal, event('settle', 'settlement', position_id='future', price='1010'))
    assert cash(journal, denomination=denomination) == D('20192')
    assert replay(journal).positions['future'].unsettled_pnl('1010') == 0
    journal = append_events(journal, (
        trade('partial', -1, '1015', date='2026-01-06'),
        event('close-fee', 'fee', position_id='future', amount='4', date='2026-01-06'),
    ))
    assert cash(journal, denomination=denomination) == D('20238')
    assert replay(journal).positions['future'].unsettled_pnl('1015') == 50
    journal = append_event(journal, event('settle-two', 'settlement', position_id='future', price='1012', date='2026-01-06'))
    assert cash(journal, denomination=denomination) == D('20258')
    next_contract = replace(journal.contracts[0], expiry='2026-06-19')
    journal = add_position(add_contract(journal, next_contract), PositionSpec('next', 'broker', next_contract.id))
    journal = roll(journal,
        close=trade('roll-close', -1, '1012', date='2026-01-07', group_id='roll'),
        opening=event('roll-open', 'trade', position_id='next', quantity=1, price='1020', date='2026-01-07', group_id='roll'),
        charges=(event('roll-fees', 'fee', position_id='future', amount='8', date='2026-01-07'),))
    assert cash(journal, denomination=denomination) == D('20250')
    assert replay(journal).positions['next'].notional('1020') == D('10200')
    journal = append_event(journal, event('tax', 'tax', position_id='future', amount='30', date='2026-01-07'))
    assert cash(journal, denomination=denomination) == D('20220')
    assert replay(journal).positions['future'].quantity == 0
    assert replay(journal).positions['future'].realized_pnl == 270


def test_snapshot_does_not_add_past_settlements_again():
    journal = append_events(invented(), (
        event('opening', 'cash_open', account_id='broker', currency='EUR', amount=12000),
        event('existing', 'snapshot', position_id='future', quantity=1, price=1000),
    ))
    position = replay(journal).positions['future']
    assert cash(journal)+position.unsettled_pnl(1005) == 12050
    assert not position.history_complete and position.realized_pnl == 0
    with pytest.raises(DataError, match='first event'):
        append_event(journal, event('duplicate', 'snapshot', position_id='future', quantity=1, price=1000))


def test_fifo_partial_close_sign_reversal_and_short_symmetry():
    for sign in (1, -1):
        journal = append_events(invented(), (
            trade('one', sign*2, 1000), trade('two', sign, 1010), trade('close', -sign, 1020),
        ))
        p = replay(journal).positions['future']
        assert cash(journal) == sign*200
        assert [lot.quantity for lot in p.lots] == [sign, sign]
        assert p.unsettled_pnl(1020) == sign*300
        journal = append_event(journal, trade('reverse', -sign*3, 1030))
        p = replay(journal).positions['future']
        assert p.quantity == -sign
        assert cash(journal) == sign*700
        assert p.lots[0].basis == 1030


def test_delayed_settlement_and_close_have_receivable_until_explicit_receipt():
    journal = append_events(invented(), (
        trade('open', 2, 1000),
        event('settle', 'settlement', position_id='future', price=1010, value_date='2026-01-07'),
        trade('close', -1, 1015, date='2026-01-06', value_date='2026-01-07'),
    ))
    state = replay(journal)
    assert cash(journal) == 0
    assert sum(p.amount for p in state.pending.values()) == 250
    assert state.positions['future'].unsettled_pnl(1015) == 50
    with pytest.raises(DataError, match='value date'):
        append_event(journal, event('early', 'receive', reference='settle'))
    journal = append_events(journal, (
        event('receipt', 'receive', reference='settle', date='2026-01-07'),
        event('close-receipt', 'receive', reference='close', date='2026-01-07'),
    ))
    assert cash(journal) == 250 and not replay(journal).pending
    with pytest.raises(DataError, match='outstanding'):
        append_event(journal, event('again', 'receive', reference='settle', date='2026-01-07'))


def test_final_settlement_closes_contract_and_retains_native_cash():
    journal = append_event(invented(), trade('open', -2, 1000))
    with pytest.raises(DataError, match='precede'):
        append_event(journal, event('early', 'final_settlement', position_id='future', price=990))
    journal = append_event(journal, event('final', 'final_settlement', position_id='future', price=990, date='2026-03-20'))
    assert cash(journal) == 200 and replay(journal).positions['future'].quantity == 0


@pytest.mark.parametrize('denomination', ['EUR', 'USD', 'GBP'])
def test_borrow_repay_and_signed_cash_are_not_duplicate_debt(denomination):
    journal = append_events(invented(denomination), (
        event('start', 'cash_open', account_id='broker', currency=denomination, amount=10000),
        event('loan', 'borrow', account_id='broker', currency=denomination, amount=3000, reference='loan'),
    ))
    state = replay(journal)
    assert cash(journal, denomination=denomination) == 13000
    assert sum(state.loans.values()) == 3000
    journal = append_events(journal, (
        event('interest', 'interest', account_id='broker', currency=denomination, amount=-20),
        event('repay', 'repay', account_id='broker', currency=denomination, amount=1000, reference='loan'),
    ))
    assert cash(journal, denomination=denomination) == 11980
    assert sum(replay(journal).loans.values()) == 2000
    with pytest.raises(DataError, match='exceeds'):
        append_event(journal, event('overpay', 'repay', account_id='broker', currency=denomination, amount=5000, reference='loan'))
    debit = append_event(invented(), event('debit', 'cash_open', account_id='broker', currency='EUR', amount=-100))
    assert cash(debit) == -100 and not replay(debit).loans


def test_fx_transfer_transit_conservation_and_reporting_currency_independence():
    journal = append_events(invented('USD'), (
        event('start', 'cash_open', account_id='bank', currency='EUR', amount=10000),
        event('convert', 'fx', account_id='bank', currency='EUR', amount=3600, other_currency='USD', other_amount=4000),
        event('dispatch', 'transfer', account_id='bank', currency='USD', amount=4000, other_account_id='broker', value_date='2026-01-07'),
    ))
    state = replay(journal)
    assert state.cash[('bank', 'EUR')] == 6400
    assert state.pending['dispatch'].amount == 4000
    assert cash(journal, denomination='USD') == 0
    assert state.cash[('bank', 'EUR')]+state.pending['dispatch'].amount*D('.90') == 10000
    assert state.cash[('bank', 'EUR')]+state.pending['dispatch'].amount*D('.99') == 10360
    journal = append_event(journal, event('arrived', 'receive', reference='dispatch', date='2026-01-07'))
    assert cash(journal, denomination='USD') == 4000
    assert not replay(journal).pending
    with pytest.raises(DataError, match='source cash'):
        append_event(journal, event('overdraw', 'transfer', account_id='bank', currency='EUR', amount=7000, other_account_id='broker'))


def test_corrections_idempotency_and_economic_order():
    original = trade('open', 1, 1000)
    journal = append_event(invented(), original)
    assert append_event(journal, original) == journal
    with pytest.raises(DataError, match='different content'):
        append_event(journal, replace(original, quantity=2))
    journal = append_event(journal, trade('close', -1, 1010, date='2026-01-06'))
    corrected = replace(original, id='corrected', price=D(1005), replaces='open')
    journal = append_event(journal, corrected)
    assert cash(journal) == 50
    assert len(journal.events) == 3
    assert [e.id for e in effective_events(journal)] == ['corrected', 'close']
    journal = append_event(journal, event('void-close', 'void', replaces='close', date='2026-01-07'))
    assert cash(journal) == 0 and replay(journal).positions['future'].quantity == 1
    with pytest.raises(DataError, match='active event'):
        append_event(journal, replace(corrected, id='double-correction'))
    assert replay(journal, as_of='2026-01-04').positions['future'].quantity == 0


def test_corrected_delayed_settlement_retains_receipt_reference_and_same_day_order():
    original = event('settle', 'settlement', position_id='future', price=1010, value_date='2026-01-07')
    journal = append_events(invented(), (
        trade('open', 2, 1000), original, trade('close', -1, 1015),
        event('receipt', 'receive', reference='settle', date='2026-01-07'),
    ))
    journal = append_event(journal, replace(original, id='corrected', replaces='settle', price=D(1005)))
    assert [e.id for e in effective_events(journal)] == ['open', 'corrected', 'close', 'receipt']
    assert cash(journal) == 200
    assert not replay(journal).pending
    assert replay(journal).positions['future'].unsettled_pnl(1015) == 100


def test_contract_event_rejects_ignored_cash_payload():
    with pytest.raises(DataError, match='derived from price'):
        append_event(invented(), trade('ambiguous', 1, 1000, amount=100))


@pytest.mark.parametrize('kind,amount,expected', [('fee', '3.25', '-3.25'), ('tax', '7', '-7'), ('interest', '-2', '-2'), ('interest', '2', '2')])
def test_delayed_expenses_and_interest_post_once_in_original_currency(kind, amount, expected):
    obligation = event('obligation', kind, account_id='broker', position_id='future', currency='USD',
                       amount=amount, value_date='2026-01-07')
    journal = append_event(invented(), obligation)
    assert append_event(journal, obligation) == journal
    pending = replay(journal).pending['obligation']
    assert pending.amount == D(expected) and pending.currency == 'USD'
    assert cash(journal, denomination='USD') == 0
    # Native contract attribution never mistakes USD expenses for EUR amounts.
    assert replay(journal).positions['future'].fees == 0
    receipt = event('receipt', 'receive', reference='obligation', date='2026-01-07')
    journal = append_event(journal, receipt)
    assert append_event(journal, receipt) == journal
    assert cash(journal, denomination='USD') == D(expected) and not replay(journal).pending
    with pytest.raises(DataError, match='different content'):
        append_event(journal, replace(obligation, amount=D(amount)+1))


def test_delayed_expense_requires_account_and_currency():
    with pytest.raises(DataError, match='unknown account'):
        append_event(invented(), event('bad', 'fee', amount=1, currency='EUR', value_date='2026-01-07'))


def test_settlement_duplicate_and_tick_validation():
    journal = append_event(invented(), trade('open', 1, 1000))
    journal = append_event(journal, event('settle', 'settlement', position_id='future', price=1010))
    with pytest.raises(DataError, match='one settlement'):
        append_event(journal, event('different-id', 'settlement', position_id='future', price=1010))
    with pytest.raises(DataError, match='tick'):
        append_event(journal, trade('bad-price', 1, '1000.1'))
    with pytest.raises(DataError, match='last trading'):
        append_event(journal, trade('late', 1, 1000, date='2026-03-21'))


def test_adopted_cash_and_security_collateral_have_one_owner():
    journal = append_event(invented(), event('cash', 'cash_open', account_id='broker', currency='EUR', amount=10000))
    journal = replace(journal, cash_links=(CashLink('broker', 'EUR', 'existing-cash'),),
        holding_links=(HoldingLink('stock', 'broker'),))
    journal = append_events(journal, (
        event('cash-reserve', 'reserve', account_id='broker', currency='EUR', amount=4000, collateral_id='r'),
        event('stock-reserve', 'reserve', account_id='broker', currency='EUR', amount=2000,
              collateral_id='s', holding_id='stock', haircut='.2'),
    ))
    assert cash(journal) == 10000 and replay(journal).available_cash('broker', 'EUR') == 6000
    with pytest.raises(DataError, match='exceeds'):
        append_event(journal, event('over-reserve', 'reserve', account_id='broker', currency='EUR', amount=7000, collateral_id='r2'))
    with pytest.raises(DataError, match='linked'):
        append_event(journal, event('wrong-owner', 'reserve', account_id='bank', currency='EUR', amount=2000, collateral_id='s2', holding_id='stock'))
    journal = append_event(journal, event('release', 'release', collateral_id='r'))
    assert replay(journal).available_cash('broker', 'EUR') == 10000
    with pytest.raises(DataError, match='opening balance'):
        replay(replace(invented(), cash_links=(CashLink('broker', 'EUR', 'cash'),)))


def test_atomic_private_document_roundtrip_revision_and_backup(tmp_path):
    journal = append_events(invented(), (
        event('cash', 'cash_open', account_id='broker', currency='EUR', amount='123.45'),
        trade('open', 1, '1000.5'),
    ))
    assert load_journal(tmp_path) == (Journal(), None)
    save_journal(tmp_path, journal, None)
    loaded, rev = load_journal(tmp_path)
    assert loaded == journal
    assert json.loads((tmp_path/'account-journal.json').read_text())['events'][0]['amount'] == '123.45'
    changed = append_event(loaded, event('fee', 'fee', position_id='future', amount='3.25'))
    save_journal(tmp_path, changed, rev)
    with pytest.raises(DataError, match='changed'):
        save_journal(tmp_path, journal, rev)
    assert list((tmp_path/'.backups').glob('account-journal-*.json'))
    assert load_journal(tmp_path)[0] == changed


@pytest.mark.parametrize('value', [True, False, 'NaN', 'Infinity', float('nan'), object(), '1e999'])
def test_decimal_rejects_invalid_values(value):
    with pytest.raises(DataError):
        decimal(value)


def test_native_precision_and_types_validation():
    assert money('.005') == D('.00') and money('.015') == D('.02')
    assert decimal(.1) == D('.1')
    with pytest.raises(DataError):
        ContractSpec('SYNTH', '2026-03-20', 'JPY', 10, '.5')
    with pytest.raises(DataError):
        event('future', 'cash_open', account_id='broker', currency='EUR', date='2099-01-01')
    with pytest.raises(DataError):
        trade('bool', True, 1000)
    with pytest.raises(DataError):
        PriceObservation('test', DAY, 0)
    with pytest.raises(DataError):
        MarginRequirement('r', 'broker', 'EUR', 100, 200, DAY)
    assert PriceObservation('test', DAY, '1000').price == D(1000)


def test_corrupt_documents_are_retained(tmp_path):
    path = tmp_path/'account-journal.json'
    path.write_text('{broken')
    with pytest.raises(DataError, match='retained'):
        load_journal(tmp_path)
    assert path.read_text() == '{broken'


def test_convenience_additions_preserve_identity():
    journal = invented()
    assert add_account(journal, journal.accounts[0]) == journal
    assert add_contract(journal, journal.contracts[0]) == journal
    assert add_position(journal, journal.positions[0]) == journal
    with pytest.raises(DataError, match='different facts'):
        add_account(journal, Account('broker', 'Different name'))


def test_parsed_journal_cache_tracks_exact_content_and_keeps_callers_isolated(tmp_path, monkeypatch):
    import portfolio_app.account_journal as module
    original = module.journal_from_dict
    calls = []
    def counted(raw):
        calls.append(True)
        return original(raw)
    module._journal_from_bytes.cache_clear()
    monkeypatch.setattr(module, 'journal_from_dict', counted)
    j = invented()
    revision = save_journal(tmp_path, j, None)
    first, revision = load_journal(tmp_path)
    count = len(calls)
    second, same_revision = load_journal(tmp_path)
    assert len(calls) == count
    assert first == second == j and first is not second
    assert first.accounts[0] is not second.accounts[0]
    assert same_revision == revision
    changed = append_event(j, event('cash', 'cash_open', account_id='bank', currency='EUR', amount=42))
    save_journal(tmp_path, changed, revision)
    assert load_journal(tmp_path)[0] == changed
    assert load_journal(tmp_path)[1] != revision
    path = tmp_path / module.FILE
    valid = path.read_bytes()
    path.write_text('{invalid')
    with pytest.raises(DataError, match='Invalid account journal'):
        load_journal(tmp_path)
    path.write_bytes(valid)
    assert load_journal(tmp_path)[0] == changed


def test_journal_cache_does_not_share_mutable_nested_costs(tmp_path):
    from portfolio_app.cost_basis import component
    from portfolio_app.security_history import SecurityBook, SecurityEvent
    opening = SecurityEvent('open', 'opening', '2024-01-01', quantity=D(2),
        costs=(component(2, 20, 'EUR'),))
    j = Journal(version=2, securities=(SecurityBook('holding', 'invented', ('2', '10', 'EUR'),
        records=(opening,), active_ids=('open',)),))
    save_journal(tmp_path, j, None)
    caller, revision = load_journal(tmp_path)
    caller.securities[0].records[0].costs[0]['amount'] = '999'
    latest, same_revision = load_journal(tmp_path)
    assert latest.securities[0].records[0].costs[0]['amount'] == '20'
    assert same_revision == revision
