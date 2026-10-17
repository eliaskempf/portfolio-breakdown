"""Reviewed history saves with one journal commit and existing workspace guards."""
from dataclasses import replace
from datetime import date
from pathlib import Path
from uuid import uuid4

from portfolio_app.account_journal import load_journal, save_journal, replay
from portfolio_app.actual_portfolio import freeze_position_keys
from portfolio_app.cost_basis import fingerprint
from portfolio_app.holdings import DataError
from portfolio_app.positions import read_snapshot
from portfolio_app.security_history import (
    SecurityBook, HistoryReview, effective_holdings, reconcile, replay_securities,
)
from portfolio_app.workspace_lock import workspace_lock


def replace_book(journal, book):
    books = tuple(book if b.position_id == book.position_id else b for b in journal.securities)
    if book.position_id not in {b.position_id for b in books}:
        books = (*books, book)
    return replace(journal, version=2, securities=books)


def book_for(journal, row):
    existing = next((b for b in journal.securities if b.position_id == row['position_id']), None)
    if existing:
        return existing if existing.active else replace(existing, source_summary=tuple(fingerprint(row)))
    return SecurityBook(row['position_id'], row['id'], tuple(fingerprint(row)))


def preview_activation(journal, book, selected_ids, row, *, target='EUR', historical=None):
    candidate = replace(book, active_ids=tuple(selected_ids))
    others = tuple(b for b in journal.securities if b.position_id != book.position_id)
    comparison = reconcile(candidate, row, other_books=others, target=target, historical=historical)
    proposed = replace_book(journal, candidate)
    before, after = replay(journal), replay(proposed)
    keys = sorted(set(before.cash) | set(after.cash) | set(before.unknown_cash) | set(after.unknown_cash))
    cash = [{'Account': account, 'Currency': currency,
             'Before': None if (account, currency) in before.unknown_cash else before.cash.get((account, currency)),
             'After': None if (account, currency) in after.unknown_cash else after.cash.get((account, currency))}
            for account, currency in keys]
    return candidate, comparison, cash


def save_history(directory, book, *, expected_journal_revision, expected_holdings_revision,
                 activate=False, resolution='accept', complete_from='', complete_through='',
                 accept_balance_change=False, accept_cash_change=False, accept_duplicates=False):
    """Evidence can be incomplete; activation requires explicit resolution.

    Identity freezing changes no balances. Financial facts and their selected
    authority are committed together in one atomic journal replacement.
    """
    directory = Path(directory)
    with workspace_lock(directory):
        journal, revision = load_journal(directory)
        snapshot = read_snapshot(directory / 'holdings.csv')
        if revision != expected_journal_revision or snapshot.revision != expected_holdings_revision:
            raise DataError('History or holdings changed. Reload and review again.')
        holdings = effective_holdings(snapshot.holdings, journal.securities)
        rows = holdings.loc[holdings.position_id == book.position_id]
        if len(rows) != 1:
            raise DataError('The selected holding no longer exists.')
        previous = next((b for b in journal.securities if b.position_id == book.position_id), None)
        if previous:
            if book.records[:len(previous.records)] != previous.records:
                raise DataError('Retain original evidence; append corrections instead of overwriting records.')
            if previous.active and (book.source_summary, book.instrument_id) != (previous.source_summary, previous.instrument_id):
                raise DataError('History identity cannot change.')
            if previous.active and book.account_id != previous.account_id:
                raise DataError('Use an explicit transfer to change an active holding account.')
        if activate:
            from portfolio_app.security_history import duplicate_candidates
            if duplicate_candidates(book) and not accept_duplicates:
                raise DataError('Matching transactions require acknowledgment that they are distinct events.')
            from portfolio_app.history_observations import ObservationFX, load_observations
            from portfolio_app.portfolio_settings import load_settings
            target = load_settings(directory).reporting_currency
            candidate, comparison, cash = preview_activation(journal, book, book.active_ids, rows.iloc[0],
                target=target, historical=ObservationFX(load_observations(directory)[0]))
            if (not comparison.quantity_matches or comparison.cost_matches is False) and not accept_balance_change:
                raise DataError('Resolve the quantity or cost discrepancy explicitly before activation.')
            if any(c['Before'] != c['After'] for c in cash) and not accept_cash_change:
                raise DataError('Review and accept the linked cash changes before activation.')
            state = replay_securities((*[b for b in journal.securities if b.position_id != book.position_id], candidate)).positions[book.position_id]
            if complete_from and (complete_from < state.boundary or complete_through < complete_from):
                raise DataError('Confirm completeness only from the latest opening boundary.')
            from portfolio_app.security_history import accounting_fingerprint
            facts = accounting_fingerprint(replace_book(journal, candidate))
            review = HistoryReview(uuid4().hex, candidate.active_ids, date.today().isoformat(), resolution,
                                   complete_from, complete_through, facts)
            book = replace(candidate, reviews=(*candidate.reviews, review))
        else:
            book = replace(book, active_ids=previous.active_ids if previous else (), reviews=previous.reviews if previous else ())
        snapshot = freeze_position_keys(directory, snapshot.revision)
        proposed = replace_book(journal, book)
        effective_holdings(snapshot.holdings, proposed.securities)
        save_journal(directory, proposed, revision)
        return proposed


def reconstruction_candidate(book, selected_ids, *, starts_at_zero=False):
    """Prepare a review without saving facts or changing current balances.

    A zero boundary is only created after the user confirms ownership began with
    these transactions. Its date is the end of the preceding day; no acquisition
    or pre-history price is inferred.
    """
    from datetime import timedelta
    from uuid import NAMESPACE_URL, uuid5
    from portfolio_app.security_history import SecurityEvent, append_evidence, selected_events
    candidate = replace(book, active_ids=tuple(selected_ids))
    events = selected_events(candidate)
    if not events:
        raise DataError('Add or select transactions to review.')
    if any(not event.date for event in events):
        raise DataError('Supply dates for the selected records before reviewing the holding.')
    if any(event.kind == 'opening' for event in events):
        if starts_at_zero:
            raise DataError('Select either a starting balance or history from zero, not both.')
        return candidate
    if not starts_at_zero:
        raise DataError('Starting holding unknown. Confirm history from the first purchase or add a starting balance.')
    earliest = min([event.date for event in events] + [c.date for e in events for c in e.charges if c.date])
    try:
        boundary = (date.fromisoformat(earliest)-timedelta(days=1)).isoformat()
    except OverflowError:
        raise DataError('The first transaction date needs a preceding day for the zero starting balance.') from None
    opening = SecurityEvent(uuid5(NAMESPACE_URL, f'confirmed-zero:{book.position_id}:{boundary}').hex,
        'opening', boundary,
        source='confirmed_zero_start', note='User confirmed zero units before the first recorded purchase.')
    candidate = append_evidence(candidate, (opening,))
    return replace(candidate, active_ids=(opening.id, *candidate.active_ids))


def correct_transaction(book, event_id, **changes):
    """Retain an editable correction; selected authority changes only on review."""
    from portfolio_app.security_history import append_evidence
    original = next((e for e in book.records if e.id == event_id), None)
    if original is None or original.source == 'confirmed_zero_start' or original.kind == 'void':
        raise DataError('Choose a saved transaction to edit.')
    if any(e.replaces == event_id for e in book.records):
        raise DataError('This transaction already has a correction. Edit the latest version.')
    allowed = {'date', 'quantity', 'price', 'amount', 'currency', 'charges', 'charges_known', 'value_date', 'note'}
    if set(changes) - allowed:
        raise DataError('This transaction field cannot be changed in the editor.')
    candidate = replace(original, **changes)
    if candidate == original:
        return book
    balance_changed = (candidate.quantity, candidate.amount, candidate.currency) != (original.quantity, original.amount, original.currency)
    if balance_changed and original.lots:
        raise DataError('This starting balance has individual lots. Edit those lots in the advanced editor before changing its quantity or cost.')
    if balance_changed and original.costs:
        candidate = replace(candidate, costs=())
    return append_evidence(book, (replace(candidate, id=uuid4().hex, replaces=original.id),))
