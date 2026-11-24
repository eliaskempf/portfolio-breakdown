"""Invented ledgers compare incremental snapshots with independent fresh replays."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from portfolio_app.portfolio.holdings import DataError
from portfolio_app.ledger.security_history import SecurityEvent as E, SecurityLot, SecurityTimeline, replay_securities
from test_security_history import book, opening
from test_csv_transaction_imports import activated
from test_history_account_reuse import mixed_journal


def mixed_books():
    return (book(opening('0', '0'),
        E('purchase', 'buy', '2024-01-02', quantity=2, price=10, currency='EUR', charges_known=True),
        E('split', 'split', '2024-01-03', quantity=2),
        E('sale', 'sell', '2024-01-04', quantity=1, price=6, currency='EUR', charges_known=True),
        E('checkpoint', 'checkpoint', '2024-01-05', quantity=3, currency='EUR'),
        E('cost', 'cost', '2024-01-06', amount=12, currency='EUR'),
        E('note', 'note', '2024-01-07')),)


@pytest.mark.parametrize('factory', [lambda: activated().journal.securities,
                                   lambda: mixed_journal().securities, mixed_books])
@pytest.mark.parametrize('order', ['forward', 'backward', 'interleaved'])
def test_snapshots_equal_fresh_replay_across_query_orders(factory, order):
    books = factory()
    dates = sorted({e.date for b in books for e in b.records} |
                   {c.date for b in books for e in b.records for c in e.charges if c.date})
    queries = [*dates, '2026-12-31', None]
    if order == 'backward':
        queries.reverse()
    elif order == 'interleaved':
        queries = queries[::2] + queries[1::2]
    timeline = SecurityTimeline(books)
    saved = []
    for stamp in queries:
        state = timeline.at(stamp)
        assert state == replay_securities(books, as_of=stamp)
        saved.append((state, deepcopy(state)))
    assert all(state == original for state, original in saved)
    assert timeline.at(None) == replay_securities(books)


def test_failed_advance_preserves_earlier_states_and_retries_validation():
    b = book(opening('0', '0'),
        E('buy', 'buy', '2024-01-02', quantity=2, price=10, currency='EUR', charges_known=True),
        E('sell', 'sell', '2024-01-03', quantity=3, price=10, currency='EUR', charges_known=True))
    timeline = SecurityTimeline((b,))
    earlier = deepcopy(timeline.at('2024-01-02'))
    for _ in range(2):
        with pytest.raises(DataError):
            timeline.at(None)
        assert timeline.at('2024-01-02') == earlier


def test_daily_closes_do_not_reread_settled_cash_postings(monkeypatch):
    from portfolio_app.ledger.security_history import SecurityCash

    begin = date(2024, 1, 1)
    dates = [(begin + timedelta(days=i)).isoformat() for i in range(1, 91)]
    buys = tuple(E(f'buy-{i}', 'buy', stamp, quantity=1, price=10, currency='EUR',
                   charges_known=True) for i, stamp in enumerate(dates))
    timeline = SecurityTimeline((book(opening('0', '0'), *buys),))
    date_reads = 0
    original = SecurityCash.__getattribute__

    def counted(posting, field):
        nonlocal date_reads
        if field == 'date':
            date_reads += 1
        return original(posting, field)

    monkeypatch.setattr(SecurityCash, '__getattribute__', counted)
    for i, stamp in enumerate(dates):
        state = timeline.at(stamp)
        assert len(state.cash) == i + 1 and state.cash[-1].id == buys[i].id
    assert date_reads <= 2 * len(buys)


def test_future_charge_appears_on_its_own_date_in_original_posting_order():
    from portfolio_app.ledger.security_history import Charge

    purchase = E('buy', 'buy', '2024-01-02', quantity=2, price=10, currency='EUR',
                 charges_known=True, charges=(Charge(1, 'EUR', '2024-01-04'),))
    sale = E('sell', 'sell', '2024-01-03', quantity=1, price=12, currency='EUR', charges_known=True)
    timeline = SecurityTimeline((book(opening('0', '0'), purchase, sale),))
    before = timeline.at('2024-01-03')
    after = timeline.at('2024-01-04')
    assert [p.id for p in before.cash] == ['buy', 'sell']
    assert [p.id for p in after.cash] == ['buy', 'buy:charge:0', 'sell']


def test_daily_queries_construct_each_purchase_lot_once(monkeypatch):
    begin = date(2024, 1, 1)
    purchases = tuple(E(f'buy-{n}', 'buy', (begin+timedelta(days=n*5)).isoformat(),
                       quantity=1, price=10, currency='EUR', charges_known=True) for n in range(1, 25))
    b = book(opening('0', '0'), *purchases)
    calls = []
    original = SecurityLot.__post_init__
    def counted(self):
        calls.append(self.id)
        original(self)
    monkeypatch.setattr(SecurityLot, '__post_init__', counted)
    timeline = SecurityTimeline((b,))
    for day in range(125):
        state = timeline.at((begin+timedelta(days=day)).isoformat()).positions[b.position_id]
        assert state.quantity == min(day//5, 24)
    assert len(calls) == len(purchases)
    assert sum(D(c['amount']) for c in state.costs) == 240


def test_fresh_replay_still_honors_same_day_sequence_cutoff():
    first = E('first', 'buy', '2024-01-02', sequence=1, quantity=2, price=10, currency='EUR', charges_known=True)
    second = replace(first, id='second', sequence=2, quantity=3)
    b = book(opening('0', '0'), first, second)
    assert replay_securities((b,), as_of='2024-01-02', sequence=1).positions[b.position_id].quantity == 2
    assert SecurityTimeline((b,)).at('2024-01-02').positions[b.position_id].quantity == 5


def test_full_cash_then_forward_chart_keeps_bounded_snapshots_and_two_passes(monkeypatch):
    from portfolio_app.ledger.security_history import Charge
    begin = date(2024, 1, 1)
    count = SecurityTimeline.MAX_SNAPSHOTS * 3
    events = tuple(E(f'invented-{n}', 'buy', (begin+timedelta(days=n)).isoformat(),
        quantity=1, price=10, currency='EUR', charges_known=True,
        charges=(Charge(D(1), 'EUR', date=(begin+timedelta(days=n+3)).isoformat()),)) for n in range(1, count+1))
    books = (book(opening('0', '0'), *events),)
    original, calls = SecurityLot.__post_init__, []
    def counted(self):
        calls.append(self.id)
        original(self)
    monkeypatch.setattr(SecurityLot, '__post_init__', counted)
    timeline = SecurityTimeline(books)
    complete = timeline.at(None)
    snapshots = []
    for n in range(count+4):
        stamp = (begin+timedelta(days=n)).isoformat()
        state = timeline.at(stamp)
        assert timeline.at(None) is complete  # Full cash reuse never moves the cursor.
        if n % 37 == 0:
            snapshots.append((stamp, state))
        assert len(timeline.snapshots) <= timeline.MAX_SNAPSHOTS
        assert timeline._cursors.keys() <= timeline.snapshots.keys()
    assert len(calls) <= 2*count
    for stamp, state in snapshots:
        assert state == replay_securities(books, as_of=stamp)
    assert complete == replay_securities(books)
    # An evicted query resumes from an earlier state. Subsequent advances remain
    # correct even when separately dated charges extend beyond the event day.
    for n in (1, 2, count//2, count+1, 0, count+3):
        stamp = (begin+timedelta(days=n)).isoformat()
        assert timeline.at(stamp) == replay_securities(books, as_of=stamp)
        assert len(timeline.snapshots) <= timeline.MAX_SNAPSHOTS
        assert timeline._cursors.keys() <= timeline.snapshots.keys()


def test_backwards_seek_resumes_inventory_with_its_pending_cash(monkeypatch):
    from portfolio_app.ledger.security_history import Charge
    start = date(2024, 1, 1)
    def day(offset):
        return (start + timedelta(days=offset)).isoformat()
    buys = tuple(E(f'buy-{n}', 'buy', day(n), quantity=1, price=10, currency='EUR',
                   charges=(Charge(D(2), 'EUR', day(85)),) if n == 79 else (),
                   charges_known=True) for n in range(1, 101))
    books = (book(opening('0', '0'), *buys),)
    expected = {n: replay_securities(books, as_of=day(n)) for n in (80, 85, 95)}
    timeline = SecurityTimeline(books)
    earlier = timeline.at(day(80))
    timeline.at(day(90))
    timeline.at(day(100))
    lots = []
    original = SecurityLot.__post_init__
    def count(lot):
        lots.append(lot.id)
        original(lot)
    monkeypatch.setattr(SecurityLot, '__post_init__', count)
    assert timeline.at(day(85)) == expected[85]
    assert lots == [f'buy-{n}' for n in range(81, 86)]
    lots.clear()
    assert timeline.at(day(95)) == expected[95]
    assert lots == [f'buy-{n}' for n in range(91, 96)]
    assert earlier == expected[80]


def test_closing_quantities_match_fifo_after_eviction_without_replaying_for_each_scope(monkeypatch):
    start = date(2024, 1, 1)
    dates = [(start + timedelta(days=n)).isoformat() for n in range(SecurityTimeline.MAX_SNAPSHOTS + 12)]
    purchases = tuple(E(f'buy-{i}', 'buy', stamp, quantity=D('1.00'), price=10,
                        currency='EUR', charges_known=True) for i, stamp in enumerate(dates[1:]))
    source = book(opening('0', '0'), *purchases,
                  E('move', 'transfer', dates[-2], sequence=2, quantity=1, other_position_id='destination'),
                  E('unresolved', 'note', dates[-2], sequence=3),
                  E('replacement', 'checkpoint', dates[-1], sequence=3, quantity=2, currency='EUR'))
    destination = replace(book(E('dest-open', 'opening', dates[0])), position_id='destination')
    books = source, destination
    timeline = SecurityTimeline(books)
    expected = {day: replay_securities(books, as_of=day).positions for day in dates}
    timeline.at(dates[11])
    timeline.at(None)
    # Category queries and a backwards search for the last supported price both
    # consume only closing facts. Rebuilding even a single lot here is unnecessary.
    def unexpected_replay(*args, **kwargs):
        pytest.fail('a validated closing quantity replayed the FIFO inventory')
    monkeypatch.setattr(timeline, 'at', unexpected_replay)
    for members in [(source.position_id,), (destination.position_id,), (source.position_id, destination.position_id)]:
        for day in reversed(dates):
            result = timeline.closing_positions(members, day)
            for identity in members:
                state = expected[day][identity]
                assert result[identity].quantity.as_tuple() == state.quantity.as_tuple()
                assert result[identity].boundary == state.boundary
                assert result[identity].has_issues == bool(state.issues)
    assert len(timeline._closings.by_position[destination.position_id]) == 2
    assert len(timeline._closings.by_position[source.position_id]) == len(dates)


def test_closing_quantities_validate_unrelated_books_and_discard_failed_batches():
    valid = book(opening(), E('buy', 'buy', '2024-01-04', quantity=1, price=10, currency='EUR'))
    invalid = replace(book(E('empty', 'opening', '2024-01-01'),
                           E('oversale', 'sell', '2024-01-05', quantity=1, price=10, currency='EUR')),
                      position_id='unrelated')
    timeline = SecurityTimeline((valid, invalid))
    members = (valid.position_id,)
    assert timeline.closing_positions(members, '2024-01-02')[valid.position_id].quantity == 10
    for _ in range(2):
        with pytest.raises(DataError, match='exceeds'):
            timeline.closing_positions(members, '2024-01-06')
        assert [stamp for stamp, _ in timeline._closings.by_position[valid.position_id]] == ['2024-01-01']
    assert timeline.closing_positions(members, '2024-01-04')[valid.position_id].quantity == 11


def test_closes_include_fees_on_another_position_even_at_an_opening():
    from portfolio_app.ledger.security_history import AssetCharge
    payer = replace(book(E('payer-open', 'opening', '2024-01-01', quantity=D('5.000'))),
                    position_id='fee-payer')
    source = book(E('open', 'opening', '2024-01-02', quantity=D('1.00'),
                    asset_charges=(AssetCharge(payer.position_id, D('.125')),)),
                  E('swap', 'swap', '2024-01-03', quantity=D('.5'), other_position_id='destination',
                    received_quantity=D('2.500'), valuation_amount=10, valuation_currency='EUR',
                    asset_charges=(AssetCharge(payer.position_id, D('.25')),)))
    destination = replace(book(E('other-open', 'opening', '2024-01-01')),
                          position_id='destination', instrument_id='other-invented')
    books = source, payer, destination
    timeline = SecurityTimeline(books)
    timeline.at(None)
    for day in ('2024-01-03', '2024-01-01', '2024-01-02'):
        full = replay_securities(books, as_of=day)
        closes = timeline.closing_positions(tuple(full.positions), day)
        for identity, state in full.positions.items():
            if state.boundary:
                assert closes[identity].quantity.as_tuple() == state.quantity.as_tuple()
                assert closes[identity].boundary == state.boundary
            else:
                assert identity not in closes
