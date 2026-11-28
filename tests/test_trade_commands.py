"""Everyday trades use deliberately invented data in temporary workspaces."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from portfolio_app.ledger.account_journal import load_journal, save_journal
from portfolio_app.ledger.security_history import Charge, SecurityEvent, effective_holdings, replay_securities
from portfolio_app.ledger.trade_commands import save_trade
from portfolio_app.portfolio.holdings import DataError
from portfolio_app.portfolio.positions import read_snapshot


def trade(identity='buy-1', kind='buy', stamp='2026-01-02', quantity='2.5', price='100', fee='3', sequence=1):
    return SecurityEvent(identity, kind, stamp, quantity=D(quantity), price=D(price), currency='EUR',
                         charges=(Charge(D(fee), 'EUR'),), charges_known=True, sequence=sequence)


def save(directory, event, **kwargs):
    return save_trade(directory, event,
        expected_holdings_revision=read_snapshot(directory/'holdings.csv').revision,
        expected_journal_revision=load_journal(directory)[1], **kwargs)


def state(directory):
    journal = load_journal(directory)[0]
    return effective_holdings(read_snapshot(directory/'holdings.csv').holdings, journal.securities), journal


def test_new_purchase_and_sale_update_history_and_quantity_once(tmp_path):
    identity = save(tmp_path, trade(), fields=dict(name='Invented investment', account='Synthetic account'))
    holdings, journal = state(tmp_path)
    assert len(holdings) == 1 and holdings.iloc[0].shares == 2.5
    assert holdings.iloc[0].acquisition_price == pytest.approx(101.2)
    assert journal.securities[0].active
    assert [e.kind for e in journal.securities[0].records] == ['opening', 'buy']
    assert journal.securities[0].records[0].date == '2026-01-01'
    assert not journal.events and not journal.accounts  # No invented cash or deposits.
    save(tmp_path, trade('sell-1', 'sell', '2026-01-03', '1', '110', '1', 2), position_id=identity)
    holdings, journal = state(tmp_path)
    assert holdings.iloc[0].shares == 1.5
    assert holdings.iloc[0].acquisition_price == pytest.approx(101.2)
    with pytest.raises(DataError):
        save(tmp_path, trade('too-many', 'sell', '2026-01-04', '2', sequence=3), position_id=identity)
    assert state(tmp_path)[0].iloc[0].shares == 1.5


def test_snapshot_forward_trade_preserves_unknown_cost_and_pending_history(tmp_path):
    path = tmp_path/'holdings.csv'
    path.write_text('id,name,shares,account,holdings_confirmed_on\na,Invented snapshot,10,Synthetic account,2026-01-01\n')
    identity = read_snapshot(path).holdings.iloc[0].position_id
    from portfolio_app.ledger.history_commands import book_for, replace_book
    from portfolio_app.ledger.security_history import append_evidence
    journal, revision = load_journal(tmp_path)
    pending = trade('old-pending', stamp='2025-12-10', quantity='10')
    book = append_evidence(book_for(journal, read_snapshot(path).holdings.iloc[0]), (pending,))
    save_journal(tmp_path, replace_book(journal, book), revision)
    save(tmp_path, trade(), position_id=identity)
    holdings, journal = state(tmp_path)
    assert holdings.iloc[0].shares == 12.5
    assert holdings.iloc[0].acquisition_price != holdings.iloc[0].acquisition_price
    assert 'old-pending' not in journal.securities[0].active_ids
    assert journal.securities[0].records[1].kind == 'opening'
    with pytest.raises(DataError, match='overlaps'):
        save(tmp_path, trade('overlap', stamp='2026-01-01'), position_id=identity)
    assert state(tmp_path)[0].iloc[0].shares == 12.5


def test_undated_snapshot_requires_boundary_and_retains_exact_quantity(tmp_path):
    path = tmp_path/'holdings.csv'
    path.write_text('id,name,shares,acquisition_price,acquisition_currency\na,Invented precision,1.234567890123456789,2.123456789123456789,EUR\n')
    original = path.read_bytes()
    identity = read_snapshot(path).holdings.iloc[0].position_id
    with pytest.raises(DataError, match='when the existing holding'):
        save(tmp_path, trade(), position_id=identity)
    assert path.read_bytes() == original
    save(tmp_path, trade(), position_id=identity, opening_date='2026-01-01')
    _, journal = state(tmp_path)
    opening = journal.securities[0].records[0]
    assert opening.quantity == D('1.234567890123456789')
    assert D(opening.costs[0]['amount']) == D('2.621551567916476154267337309750190521')
    result = replay_securities(journal.securities).positions[identity]
    assert result.quantity == D('3.734567890123456789')


def test_duplicate_and_stale_submissions_are_guarded(tmp_path):
    event = trade()
    identity = save(tmp_path, event, fields=dict(name='Invented duplicate'))
    snapshot = read_snapshot(tmp_path/'holdings.csv')
    _, revision = load_journal(tmp_path)
    with pytest.raises(DataError, match='already been saved'):
        save(tmp_path, event, position_id=identity)
    with pytest.raises(DataError, match='matching trade'):
        save(tmp_path, replace(event, id='another-buy', sequence=2), position_id=identity)
    save(tmp_path, replace(event, id='another-buy', sequence=2), position_id=identity, accept_duplicate=True)
    with pytest.raises(DataError, match='changed'):
        save_trade(tmp_path, trade('stale'), position_id=identity,
            expected_holdings_revision=snapshot.revision, expected_journal_revision=revision)
    assert state(tmp_path)[0].iloc[0].shares == 5


def test_failed_publication_leaves_no_partial_position(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('Invented storage failure')
    monkeypatch.setattr('portfolio_app.ledger.account_journal.save_journal', fail)
    with pytest.raises(OSError, match='Invented storage failure'):
        save(tmp_path, trade(), fields=dict(name='Invented rollback'))
    assert not (tmp_path/'holdings.csv').exists()
    assert not (tmp_path/'account-journal.json').exists()
    assert not (tmp_path/'.history-publication.json').exists()


def test_matching_position_requires_explicit_destination(tmp_path):
    identity = save(tmp_path, trade(), fields=dict(id='invented', name='Invented', account='Synthetic'))
    with pytest.raises(DataError, match='already exists'):
        save(tmp_path, trade('new'), fields=dict(id='invented', name='Invented', account='Synthetic'))
    assert state(tmp_path)[0].iloc[0].position_id == identity


def test_linked_cash_and_fees_publish_together_and_preserve_unrelated_account(tmp_path):
    from portfolio_app.ledger.account_journal import Account, Event, Journal, replay
    from portfolio_app.ledger.security_history import SecurityBook
    path = tmp_path/'holdings.csv'
    path.write_text('position_key,id,name,shares,acquisition_price,acquisition_currency\nposition-a,a,Invented linked holding,10,10,EUR\n')
    opening = SecurityEvent('opening', 'opening', '2026-01-01', quantity=D(10), amount=D(100), currency='EUR')
    book = SecurityBook('position-a', 'a', ('10', '10', 'EUR'), account_id='account-a', records=(opening,), active_ids=('opening',))
    journal = Journal(version=5, accounts=(Account('account-a', 'Invented brokerage'), Account('other', 'Invented other account')),
        events=(Event('cash-a', '2026-01-01', 'cash_open', 'account-a', 'EUR', D(1000)),
                Event('cash-b', '2026-01-01', 'cash_open', 'other', 'EUR', D(500))), securities=(book,))
    save_journal(tmp_path, journal, None)
    save(tmp_path, trade(), position_id='position-a')
    holdings, journal = state(tmp_path)
    assert holdings.iloc[0].shares == 12.5
    assert replay(journal).cash['account-a', 'EUR'] == D(747)
    assert replay(journal).cash['other', 'EUR'] == D(500)
    assert holdings.iloc[0].acquisition_price == pytest.approx(28.24)


def test_foreign_purchase_keeps_native_cost_and_original_date(tmp_path):
    event = replace(trade(), currency='USD', charges=(Charge(D(3), 'USD'),))
    save(tmp_path, event, fields=dict(name='Invented foreign investment'))
    holdings, journal = state(tmp_path)
    assert holdings.iloc[0].acquisition_currency == 'USD'
    assert holdings.iloc[0].acquisition_price == 101.2
    from portfolio_app.performance.cost_basis import resolve_cost
    costs = replay_securities(journal.securities).positions[journal.securities[0].position_id].costs
    assert resolve_cost(list(costs), 'EUR', None).exact_amount is None
    assert all(c['date'] == '2026-01-02' for c in costs)


def test_new_trade_cannot_be_silently_absorbed_by_cash_snapshot(tmp_path):
    from portfolio_app.ledger.account_journal import Account, Event, Journal
    from portfolio_app.ledger.security_history import SecurityBook
    (tmp_path/'holdings.csv').write_text('id,name,shares\na,Invented cash-boundary test,0\n')
    opening = SecurityEvent('opening', 'opening', '2026-01-01')
    book = SecurityBook('position-0', 'a', ('0', '', ''), account_id='account', records=(opening,), active_ids=('opening',))
    journal = Journal(version=5, accounts=(Account('account', 'Invented cash account'),),
        events=(Event('cash', '2026-01-02', 'cash_open', 'account', 'EUR', D(1000)),), securities=(book,))
    save_journal(tmp_path, journal, None)
    revision = load_journal(tmp_path)[1]
    with pytest.raises(DataError, match='linked cash snapshot'):
        save(tmp_path, trade(), position_id='position-0')
    assert load_journal(tmp_path)[1] == revision
