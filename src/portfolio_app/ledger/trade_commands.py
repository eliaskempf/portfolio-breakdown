"""Ordinary trades publish holdings and history together, without reconciliation."""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from uuid import uuid4

import pandas as pd

from portfolio_app.ledger.account_journal import Journal, load_journal, replay, save_journal
from portfolio_app.ledger.history_commands import book_for, proposed_journal, record_reviews
from portfolio_app.ledger.history_publication import publish_positions_and_history
from portfolio_app.ledger.security_history import (
    SecurityBook, SecurityEvent, append_evidence, duplicate_candidates, effective_holdings, selected_events,
)
from portfolio_app.performance.cost_basis import active_components
from portfolio_app.portfolio.holdings import DataError, parse_holdings
from portfolio_app.portfolio.positions import EMPTY_CSV, position_content, read_snapshot
from portfolio_app.workspace.storage import revision as file_revision
from portfolio_app.workspace.workspace_lock import workspace_lock


def prepare_trade(journal: Journal, holdings: pd.DataFrame, position_id: str, event: SecurityEvent, *,
                  opening_date='', new_position=False, accept_duplicate=False, opening_row=None) -> Journal:
    """Keep pending historical evidence separate from this explicitly saved trade."""
    if event.kind not in ('buy', 'sell') or not event.date or event.quantity <= 0 or event.replaces:
        raise DataError('Supply a dated buy or sell with a positive quantity.')
    row = holdings.loc[holdings.position_id.eq(position_id)]
    if len(row) != 1:
        raise DataError('Choose an existing position.')
    row = row.iloc[0]
    if row.get('instrument_type') == 'cash' or any(x.position_id == position_id for x in journal.cash_links):
        raise DataError('Record cash movements in Accounts & futures.')
    book = book_for(journal, row)
    if any(e.id == event.id for b in journal.securities for e in b.records):
        raise DataError('This trade has already been saved. Reopen the position for another trade.')
    if not book.active:
        if new_position:
            if event.kind != 'buy':
                raise DataError('Start a new position with a purchase.')
            try:
                opening_date = (date.fromisoformat(event.date) - timedelta(days=1)).isoformat()
            except OverflowError:
                raise DataError('The purchase date needs a preceding day.') from None
        else:
            confirmed = row.get('holdings_confirmed_on', '')
            if confirmed and opening_date and confirmed != opening_date:
                raise DataError('Use the saved holding snapshot date. Add earlier history for overlapping trades.')
            opening_date = confirmed or opening_date
            if not opening_date:
                raise DataError('Enter when the existing holding was measured, before this trade.')
        if opening_date >= event.date:
            raise DataError('This trade overlaps the holding snapshot. Use Add earlier history to reconcile it.')
        source = opening_row if opening_row is not None else row
        quantity = Decimal(str(source['shares']))
        opening = SecurityEvent(uuid4().hex, 'opening', opening_date,
            quantity=quantity, costs=tuple(active_components(source)) if quantity else (),
            source='confirmed_zero_start' if new_position else 'holding_snapshot',
            note='Zero units before the first purchase.' if new_position else 'Existing holding before new trades.')
        book = append_evidence(book, (opening,))
        book = replace(book, active_ids=(opening.id,))
    boundaries = [e.date for e in selected_events(book) if e.kind in ('opening', 'checkpoint')]
    if boundaries and event.date <= max(boundaries):
        raise DataError('This trade overlaps the holding snapshot. Use Add earlier history to reconcile it.')
    if book.account_id:
        from portfolio_app.ledger.account_journal import effective_events
        openings = {e.currency: e for e in effective_events(journal)
                    if e.account_id == book.account_id and e.kind == 'cash_open'}
        payments = [(event.currency, event.value_date or event.date)] + [
            (c.currency, c.date or event.date) for c in event.charges if not c.external]
        for currency, stamp in payments:
            boundary = openings.get(currency)
            if boundary and (stamp < boundary.date or stamp == boundary.date and
                    not (boundary.amount == 0 and boundary.source == 'confirmed_zero_start')):
                raise DataError('This trade overlaps the linked cash snapshot. Use Add earlier history to reconcile it.')
    candidate = append_evidence(book, (event,))
    candidate = replace(candidate, active_ids=(*book.active_ids, event.id))
    if not accept_duplicate and any(event.id in pair for pair in duplicate_candidates(candidate)):
        raise DataError('A matching trade already exists. Confirm this is a separate trade before saving.')
    proposed = proposed_journal(journal, (candidate,))
    from portfolio_app.ledger.history_order import validate_order
    validate_order(journal, proposed.securities)
    effective_holdings(holdings, proposed.securities)
    replay(proposed)  # Includes linked cash coverage and overselling validation.
    return proposed


def save_trade(directory: Path, event: SecurityEvent, *, expected_holdings_revision, expected_journal_revision,
               position_id=None, fields=None, opening_date='', accept_duplicate=False,
               expected_allocation_revision=..., validate=None) -> str:
    """Revision guards and recoverable two-document publication prevent half saves."""
    directory = Path(directory)
    with workspace_lock(directory):
        snapshot = read_snapshot(directory/'holdings.csv')
        journal, revision = load_journal(directory)
        if snapshot.revision != expected_holdings_revision or revision != expected_journal_revision:
            raise DataError('History or holdings changed. Reopen the form before saving.')
        if expected_allocation_revision is not ... and file_revision(directory/'allocation.yaml') != expected_allocation_revision:
            raise DataError('Categories or targets changed. Reopen the form before saving.')
        content = (directory/'holdings.csv').read_text(encoding='utf-8-sig') if snapshot.revision else EMPTY_CSV
        raw = pd.read_csv(StringIO(content), dtype=str, keep_default_na=False)
        frozen = 'position_key' not in raw
        if frozen:
            raw['position_key'] = snapshot.holdings.position_id.tolist()
            content = raw.to_csv(index=False)
        new_position = position_id is None
        if new_position:
            if fields is None:
                raise DataError('Choose an investment for the purchase.')
            values = {**fields, 'shares': '0', 'acquisition_price': '', 'acquisition_currency': ''}
            # Matching identities must be selected explicitly; no name-based merging.
            if values.get('id'):
                matches = snapshot.holdings.loc[snapshot.holdings.id.eq(values['id']) &
                    snapshot.holdings.account.eq(values.get('account', '')) &
                    snapshot.holdings.portfolio.eq(values.get('portfolio', ''))]
                if not matches.empty:
                    raise DataError('This position already exists. Choose its purchase destination instead.')
            content, selected = position_content(content, values, validate=validate)
            position_id = selected.position_id
        holdings = parse_holdings(content) if new_position else snapshot.holdings
        # Preserve exact CSV quantities/costs when creating the starting boundary.
        if new_position:
            raw = pd.read_csv(StringIO(content), dtype=str, keep_default_na=False)
        matching = holdings.loc[holdings.position_id.eq(position_id)]
        if len(matching) != 1:
            raise DataError('The selected position no longer exists. Reopen the form.')
        from portfolio_app.portfolio.csv_summary import CostRow, source_for
        holding = matching.iloc[0]
        opening_row = CostRow(holding.to_dict(), source_for(holding))
        exact = raw.loc[raw.position_key.eq(position_id)].iloc[0]
        for column in ('shares', 'acquisition_price'):
            if column in exact:
                opening_row[column] = exact[column]
        proposed = prepare_trade(journal, holdings, position_id, event,
            opening_date=opening_date, new_position=new_position, accept_duplicate=accept_duplicate,
            opening_row=opening_row)
        from portfolio_app.ledger.transaction_import_commands import _validate_categories
        _validate_categories(directory, holdings)
        proposed = record_reviews(proposed, {position_id}, date.today().isoformat(), resolution='record')
        if new_position or frozen:
            publish_positions_and_history(directory, content, proposed, snapshot.revision, revision)
        else:
            save_journal(directory, proposed, revision)
        return position_id


def select_trade_correction(book: SecurityBook, candidate: SecurityBook, identity: str) -> SecurityBook:
    """Apply only this correction, retaining pending evidence and real snapshots."""
    if candidate == book:
        return book
    correction = candidate.records[-1]
    candidate = replace(candidate, active_ids=tuple(correction.id if i == identity else i for i in book.active_ids))
    # The synthetic zero boundary follows a corrected first-purchase date;
    # a real snapshot never moves implicitly.
    events = selected_events(candidate)
    zeros = [e for e in events if e.source == 'confirmed_zero_start' and e.kind == 'opening']
    if zeros:
        boundary = min(zeros, key=lambda e: e.date)
        earliest = min(e.date for e in events if e.kind not in ('opening', 'checkpoint'))
        if earliest <= boundary.date:
            try:
                stamp = (date.fromisoformat(earliest) - timedelta(days=1)).isoformat()
            except OverflowError:
                raise DataError('The first purchase date needs a preceding day.') from None
            moved = replace(boundary, id=uuid4().hex, date=stamp, replaces=boundary.id)
            candidate = append_evidence(candidate, (moved,))
            candidate = replace(candidate, active_ids=tuple(moved.id if i == boundary.id else i for i in candidate.active_ids))
    return candidate
