"""Named cash accounts share the actual-account journal and its dated cash postings."""
from dataclasses import replace
from decimal import Decimal
from io import StringIO
from uuid import uuid4

import pandas as pd

from portfolio_app.ledger import account_journal as ledger
from portfolio_app.ledger.history_publication import publish_account_documents
from portfolio_app.portfolio.actual_portfolio import validate_links
from portfolio_app.portfolio.allocation import (
    Bucket, allocation_content, load_allocation, migration_preview, validate_allocation,
)
from portfolio_app.portfolio.holdings import DataError, parse_holdings
from portfolio_app.portfolio.positions import EMPTY_CSV, read_snapshot
from portfolio_app.workspace.storage import revision
from portfolio_app.workspace.workspace_lock import workspace_lock


def bank_position_ids(journal):
    linked = {(link.account_id, link.currency): link.position_id for link in journal.cash_links}
    return {linked.get((a.id, a.denomination), f'account-cash:{a.id}:{a.denomination}'): a
            for a in journal.accounts if a.cash_type}


def bank_opening(journal, account):
    return next((e for e in ledger.effective_events(journal) if e.kind == 'cash_open'
                 and e.account_id == account.id and e.currency == account.denomination), None)


def bank_detail_position(journal, identity):
    positions = bank_position_ids(journal)
    if identity in positions:
        return identity
    if positions and isinstance(identity, str) and identity.startswith('account-pending:'):
        claim = ledger.replay(journal).pending.get(identity.removeprefix('account-pending:'))
        if claim:
            return next((key for key, account in positions.items() if account.id == claim.account_id), None)
    return None


def save_bank_account(directory, account, *, opening=None, day=None, adopt='', create_category=False,
                      holdings_revision, journal_revision, allocation_revision):
    path = directory / 'holdings.csv'
    with workspace_lock(directory):
        snapshot = read_snapshot(path)
        journal, stamp = ledger.load_journal(directory)
        if (snapshot.revision, stamp, revision(directory/'allocation.yaml')) != (
                holdings_revision, journal_revision, allocation_revision):
            raise DataError('The portfolio changed. Reopen the cash account form before saving.')
        config = load_allocation(directory/'allocation.yaml')
        # A workspace consisting entirely of bank accounts must also open through
        # the normal workspace chooser and backup/restore validation.
        changes = {'holdings.csv': (EMPTY_CSV, None)} if snapshot.revision is None else {}
        holdings = snapshot.holdings
        previous = next((a for a in journal.accounts if a.id == account.id), None)
        if not account.cash_type:
            raise DataError('Choose a cash account type.')
        if previous and (not previous.cash_type or previous.denomination != account.denomination):
            raise DataError('The selected cash account changed.')
        raw = None
        if create_category or (config is None and account.bucket_id):
            if config is None:
                config, preview = migration_preview(holdings)
                if not holdings.empty and preview.bucket_id.ne('').any():
                    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
                    raw['bucket_id'] = preview.bucket_id
                    # Legacy whole-portfolio targets have a different denominator.
                    raw['within_bucket_target'] = ''
            if create_category:
                if any(b.name.strip().casefold() == 'cash' for b in config.buckets):
                    raise DataError('Choose the existing Cash category.')
                bucket = Bucket(uuid4().hex, 'Cash')
                config = replace(config, buckets=(*config.buckets, bucket))
                account = replace(account, bucket_id=bucket.id)
            changes['allocation.yaml'] = allocation_content(config), allocation_revision
        if account.bucket_id and (config is None or account.bucket_id not in config.descendants()):
            raise DataError('Select an existing category.')
        # An adopted holding already owns its category in holdings.csv. Keep
        # the normal category editor and the account dialog on that same field.
        linked_id = next((link.position_id for link in journal.cash_links if link.account_id == account.id), adopt)
        if linked_id:
            if linked_id not in set(holdings.position_id):
                raise DataError('The selected cash holding is no longer available.')
            mask = holdings.position_id.eq(linked_id)
            source = raw if raw is not None else holdings
            old_bucket = source.loc[mask, 'bucket_id'].iloc[0] if 'bucket_id' in source else ''
            if old_bucket != account.bucket_id:
                raw = raw if raw is not None else pd.read_csv(path, dtype=str, keep_default_na=False)
                raw.loc[mask, 'bucket_id'] = account.bucket_id
                if config:
                    buckets = tuple(replace(b,
                        buy_priority=tuple(i for i in b.buy_priority if i != linked_id),
                        sell_priority=tuple(i for i in b.sell_priority if i != linked_id))
                        if b.id == old_bucket else b for b in config.buckets)
                    if buckets != config.buckets:
                        config = replace(config, buckets=buckets)
                        changes['allocation.yaml'] = allocation_content(config), allocation_revision
            account = replace(account, bucket_id='')
        if previous:
            if adopt:
                raise DataError('An existing cash account cannot adopt another holding.')
            event = bank_opening(journal, previous)
            if event is None and (day is None or opening is None):
                raise DataError('Enter the missing opening balance and date.')
            opening_day = day or event.date
            amount = opening if opening is not None else event.amount
            if event is None or opening_day != event.date or Decimal(str(amount)) != event.amount:
                if any(e != event and e.date < opening_day and account.id in (e.account_id, e.other_account_id)
                       for e in ledger.effective_events(journal)):
                    raise DataError('Choose an opening date on or before the first account transaction.')
                if event:
                    updated = replace(event, id=uuid4().hex, date=opening_day, amount=amount,
                                      replaces=event.id, source='manual correction')
                else:
                    updated = ledger.Event(uuid4().hex, opening_day, 'cash_open', account.id, account.denomination, amount)
                journal = ledger.append_event(journal, updated)
            journal = replace(journal, accounts=tuple(account if a.id == account.id else a for a in journal.accounts))
        else:
            if adopt:
                if adopt in {link.position_id for link in (*journal.cash_links, *journal.holding_links)}:
                    raise DataError('This holding already belongs to an account.')
                row = holdings.set_index('position_id').loc[adopt]
                if row.get('instrument_type') != 'cash':
                    raise DataError('Choose a cash holding to adopt.')
                if 'position_key' not in holdings:
                    raw = raw if raw is not None else pd.read_csv(path, dtype=str, keep_default_na=False)
                    raw['position_key'] = holdings.position_id
            journal = ledger.add_account(replace(journal, version=max(7, journal.version)), account)
            event = ledger.Event(uuid4().hex, day, 'cash_open', account.id, account.denomination, opening)
            journal = ledger.append_event(journal, event)
            if adopt:
                journal = replace(journal, cash_links=(*journal.cash_links, ledger.CashLink(account.id, account.denomination, adopt)))
        if raw is not None:
            content = raw.to_csv(index=False)
            holdings = parse_holdings(content)
            changes['holdings.csv'] = content, holdings_revision
        if config:
            validate_allocation(config, holdings)
        validate_links(journal, holdings)
        publish_account_documents(directory, journal, journal_revision, changes)


def cash_statement(state, journal, account):
    """Both ends of transfers appear once, on the day the cash actually posts."""
    events = {e.id: e for e in journal.events}
    names = {a.id: a.name for a in journal.accounts}
    balance = Decimal(0)
    unknown_since = None
    if (account.id, account.denomination) in state.unknown_cash:
        from portfolio_app.ledger.security_history import cash_postings
        unknown_since = min((e.date for e in cash_postings(journal) if e.account_id == account.id
                             and e.currency == account.denomination and e.kind == 'security_unknown'), default='')
    rows = []
    from portfolio_app.ledger.account_activity import ACTIONS
    for movement in state.cash_movements:
        if movement.account_id != account.id:
            continue
        balance += movement.amount
        event = events.get(movement.event_id)
        action = ACTIONS.get(movement.kind, 'Security transaction')
        if movement.kind == 'cash_flow':
            action = 'Deposit' if movement.amount >= 0 else 'Withdrawal'
        if movement.kind == 'transfer' and event:
            transfer = events.get(event.reference, event) if event.kind == 'receive' else event
            action = ('From ' + names[transfer.account_id] if movement.amount > 0 else
                      'To ' + names[transfer.other_account_id])
        known = unknown_since is None or movement.date < unknown_since
        rows.append({'entry_id': movement.event_id, 'Date': movement.date, 'Transaction': action, 'Amount': movement.amount,
                     'Balance': balance if known else None, 'Note': event.note if event else ''})
    return rows[::-1]
