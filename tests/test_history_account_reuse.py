"""Synthetic mixed ledgers verify reuse against independent dated replay."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from portfolio_app.account_journal import Account, Journal, replay
from portfolio_app.history_observations import ObservationStore
from portfolio_app.holdings import DataError
from portfolio_app.personal_performance import HistoryCalculation
from portfolio_app.security_history import Charge, SecurityBook, SecurityEvent, cash_postings
from test_account_journal import invented, trade, event


def mixed_journal():
    original = SecurityEvent('pdf:purchase', 'buy', '2026-01-02', quantity=2, price=10, currency='EUR', charges_known=True)
    correction = replace(original, id='pdf:purchase:corrected', replaces=original.id,
                         charges=(Charge(D(1), 'EUR', '2026-01-04'),))
    records = (SecurityEvent('opening', 'opening', '2026-01-01'), original, correction,
        SecurityEvent('pdf:sale', 'sell', '2026-01-04', quantity=1, price=12, currency='EUR',
                      value_date='2026-01-06', charges_known=True),
        SecurityEvent('unknown', 'distribution', '2026-01-10', amount=1, currency='EUR'))
    book = SecurityBook('invented-stock', 'SYNTH', ('0', '', ''), account_id='broker',
        records=records, active_ids=tuple(e.id for e in records if e.id != original.id))
    return replace(invented(), version=2, securities=(book,), events=(
        event('cash', 'cash_open', date='2026-01-01', account_id='broker', currency='EUR', amount=1000),
        trade('future-open', 1, 100, date='2026-01-02'),
        event('settle', 'settlement', date='2026-01-03', position_id='future', price=101, value_date='2026-01-05'),
        event('receipt', 'receive', date='2026-01-07', reference='settle'),
        event('loan', 'borrow', date='2026-01-08', account_id='broker', currency='EUR', amount=100, reference='loan'),
        event('sale-receipt', 'receive', date='2026-01-09', reference='security:pdf:sale'),
        event('repay', 'repay', date='2026-01-09', account_id='broker', currency='EUR', amount=30, reference='loan'),
    ))


def test_reuse_preserves_corrections_fees_debt_and_explicit_receipts(monkeypatch):
    import portfolio_app.personal_performance as module
    import portfolio_app.account_journal as ledger
    journal = mixed_journal()
    calculation = HistoryCalculation(journal, ObservationStore())
    postings_calls, replay_calls = [], []
    def counted_postings(*args, **kwargs):
        postings_calls.append(True)
        return cash_postings(*args, **kwargs)
    original = ledger._replay_states
    def counted_replay(*args, **kwargs):
        if kwargs.get('snapshots'):
            replay_calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, 'cash_postings', counted_postings)
    monkeypatch.setattr(ledger, '_replay_states', counted_replay)
    # Out-of-order queries must not leak a later receipt/charge into earlier days.
    for day in (12, 6, 1, 9, 5, 2, 7, 4, 11, 3, 8, 10):
        stamp = f'2026-01-{day:02}'
        assert calculation.accounts(stamp) == replay(journal, as_of=stamp)
    assert len(postings_calls) == 1
    assert len(replay_calls) == 1  # One forward account pass, even out of order.
    assert calculation.accounts('2026-01-02').cash['broker', 'EUR'] == 980
    before_receipt = calculation.accounts('2026-01-06')
    assert before_receipt.cash['broker', 'EUR'] == 979
    assert sum(item.amount for item in before_receipt.pending.values()) == 22
    assert calculation.accounts('2026-01-07').cash['broker', 'EUR'] == 989
    end = calculation.accounts('2026-01-09')
    assert end.cash['broker', 'EUR'] == 1071 and not end.pending
    assert sum(end.loans.values()) == 70
    assert ('broker', 'EUR') in calculation.accounts('2026-01-10').unknown_cash
    assert calculation.accounts(None) == replay(journal)
    assert len(postings_calls) == 1 and len(replay_calls) == 1


def test_full_replay_still_validates_adopted_liabilities_after_dated_query():
    journal = Journal(accounts=(Account('a', 'Invented'),), adopted_liability_ids=('invented-loan',))
    calculation = HistoryCalculation(journal, ObservationStore())
    assert calculation.accounts('2026-01-01').loans == {}
    with pytest.raises(DataError, match='adopted liability'):
        calculation.accounts(None)
    with pytest.raises(DataError, match='adopted liability'):
        calculation.accounts(None)  # Retrying must not consume and hide a failure.


def test_dense_timeline_is_one_pass_bounded_and_prior_movements_stay_immutable(monkeypatch):
    from datetime import date, timedelta
    import portfolio_app.account_journal as ledger
    from portfolio_app.account_timeline import AccountTimeline
    start = date(2020, 1, 1)
    events = tuple(ledger.Event(str(i), (start+timedelta(days=i)).isoformat(),
        'cash_open' if not i else 'interest', 'a', 'EUR', D(1000) if not i else D(1)) for i in range(1001))
    journal = Journal(accounts=(Account('a', 'Invented'),), events=events)
    calls = []
    original = ledger._replay_states
    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(ledger, '_replay_states', counted)
    timeline = AccountTimeline(journal, ())
    first = timeline.at(events[0].date)
    for i, event in enumerate(events):
        state = timeline.at(event.date)
        assert state.cash['a', 'EUR'] == 1000+i
        assert len(state.cash_movements) == i+1
    assert len(calls) == 1
    assert len(timeline._states) <= 128
    assert first.cash['a', 'EUR'] == 1000 and len(first.cash_movements) == 1
    assert first.cash_movements[-1].event_id == '0'
    assert timeline.at(events[0].date) == first  # Evicted old date safely replays.
    assert len(calls) == 2
    assert timeline.at(None) == replay(journal)


def test_posting_ids_keep_longest_matching_event_prefix_and_correction_root():
    journal = mixed_journal()
    # An event ID that is itself a prefix must not steal another event's postings.
    prefix = SecurityEvent('pdf', 'fee', '2026-01-04', amount=3, currency='EUR', charges_known=True)
    book = journal.securities[0]
    journal = replace(journal, securities=(replace(book, records=(*book.records, prefix), active_ids=(*book.active_ids, prefix.id)),))
    postings = {entry.id: entry for entry in cash_postings(journal)}
    assert postings['security:pdf:purchase'].amount == -20
    assert postings['security:pdf:purchase:charge:0'].amount == -1
    assert postings['security:pdf'].amount == -3
    assert postings['security:unknown:unknown-charges'].kind == 'security_unknown'
    assert len(postings) == 6
