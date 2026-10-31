"""Reviewed history saves with one journal commit and existing workspace guards."""
from dataclasses import replace
from datetime import date
from decimal import Decimal
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
    return replace(journal, version=max(5, journal.version), securities=books)


def book_for(journal, row):
    existing = next((b for b in journal.securities if b.position_id == row['position_id']), None)
    if existing:
        return existing if existing.active else replace(existing, source_summary=tuple(fingerprint(row)))
    return SecurityBook(row['position_id'], row['id'], tuple(fingerprint(row)))


def preview_activation(journal, book, selected_ids, row, *, target='EUR', historical=None, other_changes=()):
    candidate = replace(book, active_ids=tuple(selected_ids))
    from portfolio_app.history_order import validate_order
    merged = {b.position_id: b for b in journal.securities}
    merged.update({b.position_id: b for b in (candidate, *other_changes)})
    validate_order(journal, merged.values())
    proposed = proposed_journal(journal, (candidate, *other_changes))
    others = tuple(b for b in proposed.securities if b.position_id != book.position_id)
    comparison = reconcile(candidate, row, other_books=others, target=target, historical=historical)
    before, after = replay(journal), replay(proposed)
    keys = sorted(set(before.cash) | set(after.cash) | set(before.unknown_cash) | set(after.unknown_cash))
    cash = [{'Account': account, 'Currency': currency,
             'Before': None if (account, currency) in before.unknown_cash else before.cash.get((account, currency)),
             'After': None if (account, currency) in after.unknown_cash else after.cash.get((account, currency))}
            for account, currency in keys]
    return candidate, comparison, cash


def changed_positions(journal, books):
    """All legs of changed economic events, including removed/replaced legs."""
    from portfolio_app.security_history import affected_positions
    old = {b.position_id: b for b in journal.securities}
    touched = set()
    for book in books:
        previous = old.get(book.position_id)
        before = {e.id for e in previous.records if e.id in previous.active_ids and e.kind != 'void'} if previous else set()
        after = {e.id for e in book.records if e.id in book.active_ids and e.kind != 'void'}
        changed = before.symmetric_difference(after)
        if changed:
            touched.add(book.position_id)
        for owner in (previous, book):
            if owner:
                for event in owner.records:
                    if event.id in changed:
                        touched.update(affected_positions(owner, event))
    return touched


def proposed_journal(journal, books):
    replacements = {b.position_id: b for b in books}
    merged = {b.position_id: b for b in journal.securities}
    merged.update(replacements)
    return replace(journal, version=max(5, journal.version), securities=tuple(merged.values()))


def history_comparisons(journal, proposed, holdings, positions, *, target='EUR', historical=None):
    from portfolio_app.cost_basis import active_components, resolve_cost
    current = effective_holdings(holdings, journal.securities).set_index('position_id')
    states = replay_securities(proposed.securities).positions
    comparisons = []
    for identity in sorted(positions):
        if identity not in current.index or identity not in states:
            raise DataError('Every affected holding needs an explicit starting balance before applying.')
        row, state = current.loc[identity], states[identity]
        old = resolve_cost(active_components(row), target, historical).exact_amount
        new = resolve_cost(list(state.costs), target, historical).exact_amount if state.costs else 0
        comparisons.append(dict(position_id=identity, name=row['name'], account=row['account'],
            before_quantity=str(row.shares), after_quantity=str(state.quantity),
            before_cost=old, after_cost=new, currency=target))
    return tuple(comparisons)


def record_reviews(proposed, positions, comparison_date, *, resolution='accept'):
    """Shared audit record of the balances accepted during application."""
    from portfolio_app.security_history import security_history_fingerprint
    books = []
    for book in proposed.securities:
        if book.position_id in positions:
            review = HistoryReview(uuid4().hex, book.active_ids, comparison_date, resolution,
                facts_fingerprint=security_history_fingerprint(proposed, book.position_id))
            book = replace(book, reviews=(*book.reviews, review))
        books.append(book)
    return replace(proposed, securities=tuple(books))


def save_history(directory, book, *, expected_journal_revision, expected_holdings_revision,
                 activate=False, resolution='accept',
                 accept_balance_change=False, accept_cash_change=False, accept_duplicates=False,
                 other_changes=()):
    """Publish all legs together; provenance never changes transaction permissions."""
    directory = Path(directory)
    with workspace_lock(directory):
        journal, revision = load_journal(directory)
        snapshot = read_snapshot(directory/'holdings.csv')
        if revision != expected_journal_revision or snapshot.revision != expected_holdings_revision:
            raise DataError('History or holdings changed. Reload and review again.')
        old = {b.position_id: b for b in journal.securities}
        books = []
        for candidate in (book, *other_changes):
            if snapshot.holdings.position_id.eq(candidate.position_id).sum() != 1:
                raise DataError('The selected holding no longer exists.')
            previous = old.get(candidate.position_id)
            if previous:
                if candidate.records[:len(previous.records)] != previous.records:
                    raise DataError('Retain original evidence; append corrections instead of overwriting records.')
                if previous.active and (candidate.source_summary, candidate.instrument_id, candidate.account_id) != (previous.source_summary, previous.instrument_id, previous.account_id):
                    raise DataError('History identity and active cash binding cannot change.')
            if not activate:
                candidate = replace(candidate, active_ids=previous.active_ids if previous else (),
                                    reviews=previous.reviews if previous else ())
            books.append(candidate)
        if activate:
            from portfolio_app.history_order import validate_order
            merged = {b.position_id: b for b in journal.securities}
            merged.update({b.position_id: b for b in books})
            validate_order(journal, merged.values())
        proposed = proposed_journal(journal, books)
        if activate:
            from portfolio_app.security_history import duplicate_candidates
            if not accept_duplicates and any(
                    first not in (old[b.position_id].active_ids if b.position_id in old else ())
                    or second not in (old[b.position_id].active_ids if b.position_id in old else ())
                    for b in books for first, second in duplicate_candidates(b)):
                raise DataError('Matching transactions require acknowledgment that they are distinct events.')
            from portfolio_app.history_observations import ObservationFX, load_observations
            from portfolio_app.portfolio_settings import load_settings
            target = load_settings(directory).reporting_currency
            touched = changed_positions(journal, books) | {b.position_id for b in books if b.active}
            comparisons = history_comparisons(journal, proposed, snapshot.holdings, touched,
                target=target, historical=ObservationFX(load_observations(directory)[0]))
            if not accept_balance_change and any(
                    abs(Decimal(c['before_quantity'])-Decimal(c['after_quantity'])) > Decimal('1e-9') or
                    c['before_cost'] is not None and c['after_cost'] is not None and abs(c['before_cost']-c['after_cost']) > Decimal('.01')
                    for c in comparisons):
                raise DataError('Resolve the quantity or cost discrepancy explicitly before activation.')
            before, after = replay(journal), replay(proposed)
            if (before.cash, before.unknown_cash) != (after.cash, after.unknown_cash) and not accept_cash_change:
                raise DataError('Review and accept the linked cash changes before activation.')
            proposed = record_reviews(proposed, touched, date.today().isoformat(), resolution=resolution)
        snapshot = freeze_position_keys(directory, snapshot.revision)
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
    removed = tuple(e.id for e in book.records if e.kind == 'void' and e.id in book.active_ids)
    candidate = replace(book, active_ids=tuple(dict.fromkeys((*selected_ids, *removed))))
    events = selected_events(candidate)
    if not events:
        raise DataError('Add or select transactions to review.')
    if any(not event.date for event in events):
        raise DataError('Supply dates for the selected records before reviewing the holding.')
    if any(event.kind in ('opening', 'checkpoint') for event in events):
        if starts_at_zero:
            raise DataError('Select either a starting balance or history from zero, not both.')
        return candidate
    if not starts_at_zero:
        raise DataError('Starting holding unknown. Choose history from the first purchase or add a starting balance.')
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
    from portfolio_app.security_history import current_records
    if event_id not in {e.id for e in current_records(book)}:
        raise DataError('This transaction already has a correction. Edit the latest version.')
    allowed = {'date', 'quantity', 'price', 'amount', 'currency', 'charges', 'charges_known', 'value_date', 'note', 'sequence', 'other_position_id', 'received_quantity',
               'valuation_amount', 'valuation_currency', 'valuation_quality', 'valuation_source',
               'asset_charges', 'costs', 'flow_classification', 'estimated'}
    if set(changes) - allowed:
        raise DataError('This transaction field cannot be changed in the editor.')
    if changes.get('date', original.date) != original.date:
        changes['timestamp_utc'] = ''
        changes['order_domain'] = changes['order_time'] = ''
    if changes.get('sequence', original.sequence) != original.sequence:
        changes['order_domain'] = 'reviewed'
    candidate = replace(original, **changes)
    if candidate == original:
        return book
    balance_changed = (candidate.quantity, candidate.amount, candidate.currency) != (original.quantity, original.amount, original.currency)
    if balance_changed and original.lots:
        raise DataError('This starting balance has individual lots. Edit those lots in the advanced editor before changing its quantity or cost.')
    if balance_changed and original.costs and 'costs' not in changes:
        candidate = replace(candidate, costs=())
    return append_evidence(book, (replace(candidate, id=uuid4().hex, replaces=original.id),))


def remove_transactions(book, event_ids):
    """Prepare explicit removals; retain evidence and unrelated pending changes.

    Removing a pending correction also removes its applied ancestor. The caller
    must review and activate any resulting balance changes through save_history.
    """
    from portfolio_app.security_history import SecurityEvent, append_evidence, current_records
    chosen = set(event_ids)
    available = {event.id: event for event in current_records(book)}
    if not chosen or not chosen <= available.keys():
        raise DataError('Choose current transactions to remove. Reload if the list changed.')
    if any(available[identity].kind == 'opening' for identity in chosen):
        raise DataError('Edit the starting balance separately; select only transactions to remove.')
    known = {event.id: event for event in book.records}
    removed = set(chosen)
    additions = []
    for event in current_records(book):
        if event.id not in chosen:
            continue
        additions.append(SecurityEvent(uuid4().hex, 'void', event.date, replaces=event.id,
                                       note='Removed by user. Original evidence retained.'))
        ancestor = event.replaces
        while ancestor:
            removed.add(ancestor)
            ancestor = known[ancestor].replaces
    candidate = append_evidence(book, additions)
    return replace(candidate, active_ids=tuple(identity for identity in book.active_ids if identity not in removed) + tuple(e.id for e in additions))


def exclude_transactions(book, event_ids, *, accepted_ids=()):
    """Resolve reviewed exclusions without removing an applied predecessor.

    Stable void identities keep repeated previews and their approvals identical.
    The original evidence remains available for a later explicit correction.
    """
    from uuid import NAMESPACE_URL, uuid5
    from portfolio_app.security_history import SecurityEvent, append_evidence
    chosen = set(event_ids)
    if not chosen:
        return book
    records = {e.id: e for e in book.records}
    if not chosen <= records.keys():
        raise DataError('Excluded transactions no longer match the reviewed evidence.')
    if chosen & set(accepted_ids):
        raise DataError('Remove applied transactions through balance review instead of excluding them.')
    restored = set()
    for identity in chosen:
        ancestor = records[identity].replaces
        while ancestor:
            if ancestor in accepted_ids:
                restored.add(ancestor)
                break
            ancestor = records[ancestor].replaces
    additions = tuple(SecurityEvent(uuid5(NAMESPACE_URL, 'history-exclusion:'+book.position_id+':'+identity).hex,
        'void', records[identity].date, replaces=identity, source='review_exclusion',
        note='Excluded during review. Original evidence retained.') for identity in sorted(chosen))
    candidate = append_evidence(book, additions)
    return replace(candidate, active_ids=tuple(dict.fromkeys(
        [identity for identity in book.active_ids if identity not in chosen] + sorted(restored) + [e.id for e in additions])))
