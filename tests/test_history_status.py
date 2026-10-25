"""Synthetic history diagnostics distinguish evidence from return eligibility."""
from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pandas as pd

from portfolio_app.account_journal import Journal, Account, Event
from portfolio_app.history_observations import ObservationStore, Observation, CoverageReview, HistoryScope
from portfolio_app.history_status import position_history_statuses
from portfolio_app.security_history import SecurityBook, SecurityEvent as E, history_fingerprint

TODAY = date(2025, 1, 5)  # Sunday: never require an invented Sunday stock quote.


def make_book(identity, *, active=True, pending=False):
    opening = E(identity+'-open', 'opening', '2024-01-01', quantity=2, amount=20, currency='EUR')
    events = (opening,)
    if pending:
        events += (E(identity+'-draft', 'buy', '2024-06-01', quantity=1, price=10, currency='EUR', charges_known=True),)
    return SecurityBook(identity, 'SYNTH-'+identity, ('2', '10', 'EUR'), records=events,
                        active_ids=(opening.id,) if active else ())


def fixture():
    books = tuple(make_book(identity, active=identity!='unapplied', pending=identity=='pending')
                  for identity in ('unapplied', 'pending', 'unconfirmed', 'prices', 'fx', 'ready', 'snapshot'))
    journal = Journal(version=2, securities=books)
    observations, reviews = [], []
    for book in books:
        identity = book.position_id
        if identity != 'prices':
            currency = 'USD' if identity == 'fx' else 'EUR'
            stamps = ['2024-01-01'] if identity == 'snapshot' else ['2024-01-01', '2025-01-03']
            observations += [Observation(identity+stamp, 'price', identity, stamp, D(12), currency) for stamp in stamps]
        if identity != 'unconfirmed':
            reviews.append(CoverageReview(identity, HistoryScope((identity,)), '2024-01-01', '2025-01-03', history_fingerprint(journal)))
    holdings = pd.DataFrame([dict(position_id=identity, instrument_type='stock')
                            for identity in ('none', *(b.position_id for b in books))])
    return holdings, journal, ObservationStore(tuple(observations), tuple(reviews))


def test_statuses_distinguish_missing_history_pending_confirmation_prices_and_fx():
    holdings, journal, observations = fixture()
    statuses = {s.position_id:s for s in position_history_statuses(holdings, journal, observations, today=TODAY)}
    assert statuses['none'].transactions == 'No history' and statuses['none'].action == 'Add transactions'
    assert statuses['unapplied'].transactions == 'Not applied' and statuses['unapplied'].screen == 'review'
    assert statuses['pending'].transactions == 'Unapplied changes'
    assert statuses['unconfirmed'].transactions == 'Not confirmed' and statuses['unconfirmed'].action == 'Confirm history'
    assert statuses['prices'].market == 'Prices missing' and statuses['prices'].needs_attention
    assert statuses['fx'].market == 'FX / cash inputs missing' and statuses['fx'].needs_attention
    assert statuses['ready'].transactions == 'Confirmed' and statuses['ready'].market == 'Available'
    assert not statuses['ready'].needs_attention and statuses['ready'].end == '2025-01-03'
    assert statuses['snapshot'].market == 'Starting value only' and not statuses['snapshot'].needs_attention


def test_latest_price_without_fx_is_not_hidden_behind_an_older_supported_endpoint():
    holdings, journal, observations = fixture()
    observations = replace(observations, observations=(*observations.observations,
        Observation('fx-start', 'fx', 'USD/EUR', '2024-01-01', D('.9'), 'USD', target='EUR')))
    row = next(s for s in position_history_statuses(holdings, journal, observations, today=TODAY) if s.position_id == 'fx')
    assert row.market == 'FX / cash inputs missing' and row.end == '2025-01-03'


def test_no_chart_points_and_one_shared_calculation_for_all_positions(monkeypatch):
    import portfolio_app.history_status as module
    holdings, journal, observations = fixture()
    original = module.history_view
    contexts = []
    def counted(*args, **kwargs):
        assert kwargs['include_points'] is False
        contexts.append(kwargs['calculation'])
        return original(*args, **kwargs)
    monkeypatch.setattr(module, 'history_view', counted)
    result = position_history_statuses(holdings, journal, observations, today=TODAY)
    assert len(result) == len(holdings)
    assert len(contexts) == 6 and len({id(context) for context in contexts}) == 1


def test_account_history_and_missing_cash_are_not_security_purchase_gaps():
    from portfolio_app.account_journal import CashLink
    journal = Journal(accounts=(Account('cash-account', 'Invented account'),),
        cash_links=(CashLink('cash-account', 'EUR', 'linked'),),
        events=(Event('cash-open', '2024-01-01', 'cash_open', 'cash-account', 'EUR', D(100)),))
    holdings = pd.DataFrame([dict(position_id=identity, instrument_type='cash') for identity in ('linked', 'unlinked')])
    statuses = position_history_statuses(holdings, journal, ObservationStore(), today=TODAY)
    assert all(status.target == 'accounts' and status.action == 'Open account journal' for status in statuses)
    assert statuses[0].transactions == 'Not confirmed'
    assert statuses[1].transactions == 'No account history'


def test_applied_removal_does_not_leave_permanent_unapplied_changes():
    holdings, journal, observations = fixture()
    book = next(b for b in journal.securities if b.position_id == 'ready')
    buy = E('removed-buy', 'buy', '2024-06-01', quantity=1, price=10, currency='EUR', charges_known=True)
    void = E('removal', 'void', buy.date, replaces=buy.id)
    book = replace(book, records=(*book.records, buy, void))
    journal = replace(journal, securities=tuple(book if b.position_id == book.position_id else b for b in journal.securities))
    row = next(s for s in position_history_statuses(holdings, journal, observations, today=TODAY) if s.position_id == 'ready')
    assert row.transactions == 'Not confirmed'  # Changed facts need review, not reapplying a deleted transaction.


def test_unknown_fees_require_transaction_review_even_with_matching_balances():
    holdings, journal, observations = fixture()
    book = next(b for b in journal.securities if b.position_id == 'ready')
    buy = E('unreviewed-fees', 'buy', '2024-06-01', quantity=1, price=10, currency='EUR')
    book = replace(book, records=(*book.records, buy), active_ids=(*book.active_ids, buy.id))
    journal = replace(journal, securities=tuple(book if b.position_id == book.position_id else b for b in journal.securities))
    row = next(s for s in position_history_statuses(holdings, journal, observations, today=TODAY) if s.position_id == 'ready')
    assert row.transactions == 'Needs transaction details' and row.screen == 'review'
    assert row.needs_attention


def test_method_restriction_is_separate_from_confirmed_transactions_and_prices():
    from portfolio_app.history_diagnostics import HistoryIssue, issue_kind
    reason = HistoryIssue('Arbitrary translated text', 'irr_multiple')
    assert issue_kind(reason) == 'irr_multiple'
    assert str(reason) == 'Arbitrary translated text'
    holdings, journal, observations = fixture()
    book = next(b for b in journal.securities if b.position_id == 'ready')
    trade = E('purchase', 'buy', '2024-06-01', quantity=1, price=12, currency='EUR', charges_known=True)
    book = replace(book, records=(*book.records, trade), active_ids=(*book.active_ids, trade.id))
    journal = replace(journal, securities=tuple(book if b.position_id == 'ready' else b for b in journal.securities))
    observations = replace(observations, reviews=(CoverageReview('new-review', HistoryScope(('ready',)),
        '2024-01-01', '2025-01-03', history_fingerprint(journal)),))
    row = next(s for s in position_history_statuses(holdings, journal, observations, today=TODAY) if s.position_id == 'ready')
    assert row.transactions == 'Confirmed' and row.market == 'Prices missing'
    assert row.action == 'Review market data'
    assert row.money_weighted == 'Available' and row.time_weighted == 'Unavailable'
    assert row.needs_attention


def test_missing_snapshot_cost_fx_does_not_unconfirm_transactions():
    from portfolio_app.cost_basis import component
    holdings,journal,store=fixture()
    book=next(b for b in journal.securities if b.position_id=='ready')
    opening=replace(book.records[0],costs=(component(2,20,'USD','2024-01-01'),))
    book=replace(book,records=(opening,))
    journal=replace(journal,securities=tuple(book if b.position_id=='ready' else b for b in journal.securities))
    store=replace(store,reviews=(CoverageReview('review',HistoryScope(('ready',)),'2024-01-01','2025-01-03',history_fingerprint(journal)),))
    row=next(s for s in position_history_statuses(holdings,journal,store,today=TODAY) if s.position_id=='ready')
    assert row.transactions=='Confirmed'
    assert row.market=='FX / cash inputs missing'
    assert row.money_weighted==row.time_weighted=='Available'
