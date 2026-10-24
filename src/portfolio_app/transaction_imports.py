"""Consorsbank drafts -> existing retained history, with revision/dedup guards."""
from dataclasses import replace
from datetime import date
from uuid import uuid4

from portfolio_app.history_commands import save_history, book_for
from portfolio_app.holdings import DataError
from portfolio_app.security_history import Charge, SecurityEvent, append_evidence, current_records

REVIEW_CHARGES = 'Confirm fees and taxes are complete before importing.'


def source_id(draft):
    return 'consors_pdf:'+draft.fingerprint


def _text(row, field):
    value = str(row.get(field, '')).strip()
    return '' if value.lower() in ('nan', 'none', '<na>') else value


def existing_transactions(book):
    """Keep pending corrections and their still-applied originals in duplicate checks."""
    identities = {event.id for event in current_records(book)} | set(book.active_ids)
    return tuple(event for event in book.records if event.id in identities)


def draft_problems(draft, row, journal):
    problems = [p for p in draft.issues if p != REVIEW_CHARGES]
    if draft.duplicate_of is not None:
        problems.append('Duplicate file in this batch.')
    if any(event.source == source_id(draft) for book in journal.securities for event in existing_transactions(book)):
        problems.append('Already imported.')
    if row.get('instrument_type') in ('cash', 'future'):
        problems.append('Choose a stock or ETF position.')
    for field in ('isin', 'wkn'):
        expected, actual = _text(row, field).upper(), getattr(draft, field)
        if expected and actual and expected != actual:
            problems.append('Instrument does not match this position.')
    if draft.trade_date and draft.trade_date > date.today().isoformat():
        problems.append('Trade date is in the future.')
    return tuple(dict.fromkeys(problems))


def identity_review_required(draft, row):
    """A stored identifier must be comparable; names are never an identity match."""
    return not any(_text(row, field) and getattr(draft, field) for field in ('isin', 'wkn'))


def as_event(draft, *, sequence=0, charges_known=False):
    if any(issue != REVIEW_CHARGES for issue in draft.issues):
        raise DataError('Resolve statement errors before importing.')
    if draft.kind not in ('buy', 'sell') or not draft.trade_date or not draft.quantity or not draft.gross:
        raise DataError('Complete the transaction before importing.')
    charges = tuple(Charge(m.amount, m.currency, kind=kind)
                    for kind, amounts in (('fee', draft.fees), ('tax', draft.taxes)) for m in amounts)
    # The gross statement amount is authoritative. Do not manufacture an exact
    # unit price from a rounded quote, or count fees inside gross a second time.
    return SecurityEvent(source_id(draft), draft.kind, draft.trade_date, sequence=sequence,
        quantity=draft.quantity, amount=draft.gross.amount, currency=draft.gross.currency,
        charges=charges, charges_known=charges_known or draft.statement_charges_known or draft.zero_statement_charges, source=source_id(draft),
        value_date=draft.settlement_date or '',
        note='Consorsbank PDF. Settlement: '+(
            f'{draft.settlement.amount} {draft.settlement.currency}' if draft.settlement else 'unknown')+
            '. Quoted unit price: '+(f'{draft.unit_price.amount} {draft.unit_price.currency}' if draft.unit_price else 'unknown')+
            ('. Zero statement charges: trade value equals debit; no unexplained monetary rows.' if draft.zero_statement_charges else '')+
            ('. Statement charges verified: all financial rows parsed and settlement reconciles exactly.'
             if draft.statement_charges_known and not draft.zero_statement_charges else '')+
            ('. Supplementary statement rows excluded from transaction values.' if draft.review_notes else '')+
            ('. Trade time: '+draft.trade_time if draft.trade_time else '')+
            ('. Sources: '+'; '.join(f'{ref.field}: page {ref.page}, row {ref.row}' for ref in draft.provenance)
             if draft.provenance else ''))


def possible_duplicate(draft, events):
    """A heuristic, not proof: callers ask the user to distinguish real repeats."""
    if not draft.gross:
        return False
    return any(event.kind == draft.kind and event.date == draft.trade_date and
               event.quantity == draft.quantity and event.gross == draft.gross.amount and
               event.currency == draft.gross.currency for event in events if event.kind in ('buy', 'sell'))


def save_pdf_drafts(directory, row, journal, revision, snapshot, drafts, *,
                    charges_known=False, allow_similar=False, identity_confirmed=False,
                    confirmed_charge_ids=(), confirmed_similar_ids=()):
    if not drafts:
        raise DataError('Select transactions to import.')
    # Revalidate all selected rows at the write boundary, not only in the UI.
    book = book_for(journal, row)
    known = list(existing_transactions(book))
    sources = set()
    events = []
    for draft in drafts:
        problems = draft_problems(draft, row, journal)
        if problems:
            raise DataError(problems[0])
        if identity_review_required(draft, row) and not identity_confirmed:
            raise DataError('Check the statement instrument identifiers before importing.')
        if source_id(draft) in sources:
            raise DataError('Duplicate file in this batch.')
        if possible_duplicate(draft, known) and not (allow_similar or draft.fingerprint in confirmed_similar_ids):
            raise DataError('Similar transactions already exist. Confirm they are distinct trades.')
        # Use the statement's time to order trades within the same day. When
        # absent, retain explicit upload order for later review, never infer it.
        used = {e.sequence for e in known if e.date == draft.trade_date}
        if draft.trade_time:
            h, m, sec = map(int, draft.trade_time.split(':'))
            sequence = (h*3600+m*60+sec)*100000
        else:
            sequence = max(used, default=-1)+1
        while sequence in used:
            sequence += 1
        event = as_event(draft, sequence=sequence,
                         charges_known=charges_known or draft.fingerprint in confirmed_charge_ids)
        if any(old.id == event.id for old in book.records):
            event = replace(event, id=uuid4().hex)
        known.append(event); events.append(event); sources.add(source_id(draft))
    candidate = append_evidence(book, events)
    save_history(directory, candidate, expected_journal_revision=revision,
                 expected_holdings_revision=snapshot.revision)
    return replace(candidate, active_ids=book.active_ids)
