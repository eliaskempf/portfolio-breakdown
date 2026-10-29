"""Invented ledgers compare incremental snapshots with independent fresh replays."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from portfolio_app.holdings import DataError
from portfolio_app.security_history import SecurityEvent as E, SecurityLot, SecurityTimeline, replay_securities
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
@pytest.mark.parametrize('reverse', [False, True])
def test_snapshots_equal_fresh_replay_in_both_directions(factory, reverse):
    books = factory()
    dates = sorted({e.date for b in books for e in b.records} |
                   {c.date for b in books for e in b.records for c in e.charges if c.date})
    queries = [dates[0], *dates, '2026-12-31', None]
    timeline = SecurityTimeline(books)
    saved = []
    for stamp in reversed(queries) if reverse else queries:
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
