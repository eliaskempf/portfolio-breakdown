"""Synthetic quantity histories; deliberately no acquisition costs are needed."""
from dataclasses import replace
from decimal import Decimal as D

from portfolio_app.security_history import SecurityBook, SecurityEvent as E, SecurityLot
from portfolio_app.tax_history import project_holdings
from portfolio_app.vorabpauschale import FundInputs, calculate_fund


def book(position='a', account='one', events=(), instrument='invented'):
    return SecurityBook(position, instrument, ('0', '', ''), account, events, tuple(e.id for e in events))


def opening(position='a', qty='0', day='2024-12-31'):
    return E(position+'-open', 'opening', day, quantity=D(qty))


def trade(id, kind, day, qty, **kwargs):
    return E(id, kind, day, quantity=D(qty), **kwargs)


def project(*books, as_of='2025-12-31'):
    return project_holdings(books, as_of)


def test_monthly_buys_fifo_sale_and_cost_correction():
    b = book(events=(opening(), trade('j', 'buy', '2025-01-03', '10'),
        trade('f', 'buy', '2025-02-03', '10'), E('cost', 'cost', '2025-02-04'),
        trade('sale', 'sell', '2025-03-04', '15')))
    h, = project(b)
    assert not h.issues
    assert [(l.quantity, l.acquired) for l in h.lots] == [(D(5), '2025-02-03')]


def test_cross_depot_transfer_retains_month_and_partial_sale():
    a = book(events=(opening(), trade('buy', 'buy', '2025-07-01', '10'),
                     trade('transfer', 'transfer', '2025-09-01', '6', other_position_id='b')))
    b = book('b', 'two', (opening('b'), trade('sell', 'sell', '2025-10-01', '2')))
    first, second = project(a, b)
    assert first.lots[0].quantity == second.lots[0].quantity == 4
    assert second.lots[0].acquired == '2025-07-01'


def test_depot_fifo_across_allocation_sleeves():
    a = book(events=(opening(), trade('early', 'buy', '2025-01-01', '10')))
    b = book('b', 'one', (opening('b'), trade('late', 'buy', '2025-08-01', '10'),
                        trade('sell', 'sell', '2025-09-01', '10')))
    h, = project(a, b)
    assert h.lots[0].acquired == '2025-08-01'
    assert h.lots[0].quantity == 10


def test_transfer_without_original_lots_is_unknown():
    h, = project(book(events=(opening(), trade('incoming', 'transfer_in', '2025-08-01', '10'))))
    assert h.lots[0].acquired is None
    assert calculate_fund(FundInputs(2025, D(100), D(110), D(0)), h.lots).gross is None


def test_transferred_explicit_lots_are_preserved():
    e = trade('incoming', 'transfer_in', '2025-08-01', '10',
              lots=(SecurityLot('old', D(10), date='2020-01-01'),))
    h, = project(book(events=(opening(), e)))
    assert h.lots[0].acquired == '2020-01-01'


def test_snapshot_unknown_month_and_preyear_ownership():
    h, = project(book(events=(opening(qty='10', day='2025-07-01'),)))
    assert h.lots[0].acquired is None
    h, = project(book(events=(opening(qty='10'),)))
    assert h.lots[0].acquired == '2024-12-31'


def test_equal_checkpoint_preserves_lots_but_changed_balance_blocks():
    events = (opening(), trade('buy', 'buy', '2025-03-01', '10'))
    same = E('check', 'checkpoint', '2025-05-01', quantity=D(10))
    h, = project(book(events=(*events, same)))
    assert h.lots[0].acquired == '2025-03-01' and not h.issues
    h, = project(book(events=(*events, replace(same, quantity=D(11)))))
    assert h.issues


def test_split_preserves_dates_and_uses_ending_units():
    h, = project(book(events=(opening(qty='10'), trade('split', 'split', '2025-05-01', '2'))))
    assert h.lots[0].quantity == 20
    assert h.lots[0].acquired == '2024-12-31'
    assert h.splits == (('2025-05-01', D(2)),)


def test_complete_sale_repurchase_and_correction():
    first = trade('buy', 'buy', '2025-01-02', '10')
    corrected = replace(first, id='corrected', quantity=D(20), replaces='buy')
    b = book(events=(opening(), first, corrected, trade('sell', 'sell', '2025-02-01', '20'),
                    trade('again', 'buy', '2025-12-01', '2')))
    b = replace(b, active_ids=tuple(i for i in b.active_ids if i != 'buy'))
    h, = project(b)
    assert [(l.quantity, l.acquired) for l in h.lots] == [(D(2), '2025-12-01')]
    h, = project(b, as_of='2025-11-30')
    assert h.lots == ()


def test_unsupported_event_and_oversale_block():
    h, = project(book(events=(opening(), trade('bad', 'sell', '2025-02-01', '1'))))
    assert h.issues
    h, = project(book(events=(opening(), E('note', 'note', '2025-02-01', note='Invented unresolved merger'))))
    assert h.issues


def test_unknown_lot_sale_cannot_make_remaining_months_look_known():
    h, = project(book(events=(opening(qty='10', day='2025-02-01'),
        trade('buy', 'buy', '2025-05-01', '10'), trade('sale', 'sell', '2025-06-01', '10'))))
    assert 'FIFO acquisition order unknown' in h.issues


def test_long_savings_history_consumes_each_lot_without_rebuilding_fifo():
    # Many distinct invented events on one date exercise fractional conservation.
    buys = tuple(E(f'buy-{i}', 'buy', '2025-01-02', sequence=i, quantity=D('.125')) for i in range(2000))
    sales = tuple(E(f'sell-{i}', 'sell', '2025-02-03', sequence=i, quantity=D('.125')) for i in range(1999))
    h, = project(book(events=(opening(), *buys, *sales)))
    assert not h.issues and len(h.lots) == 1 and h.lots[0].quantity == D('.125')


def test_later_snapshot_does_not_imply_empty_prehistory():
    h, = project(book(events=(opening(qty='10', day='2025-01-01'),)), as_of='2024-12-31')
    assert h.issues and not h.lots
    assert calculate_fund(FundInputs(2024), h.lots, ownership_issues=h.issues).gross is None


def test_preyear_incoming_transfer_proves_full_year_ownership():
    h, = project(book(events=(opening(day='2023-12-31'), trade('incoming', 'transfer_in', '2024-08-01', '10'))))
    assert h.lots[0].acquired == '2024-08-01'
    assert calculate_fund(FundInputs(2025, D(100), D(110), D(0)), h.lots).gross == D('17.71')
