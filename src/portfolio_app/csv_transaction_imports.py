"""Pure batch reconstruction of retained CSV evidence into existing security books."""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal

from portfolio_app.holdings import DataError
from portfolio_app.security_history import (
    AssetCharge, Charge, SecurityEvent, append_evidence, affected_positions,
    duplicate_candidates, effective_holdings, replay_securities, selected_events,
)
from portfolio_app.history_commands import book_for
from portfolio_app.transaction_csv import ImportReceipt, digest


@dataclass(frozen=True)
class ImportPreview:
    journal: object
    added: int
    duplicates: int
    applied: int
    comparisons: tuple[dict, ...]
    cash_changes: tuple[dict, ...]
    warnings: tuple[str, ...]
    checkpoints: tuple[dict, ...] = ()


def event_for(row, mapping, *, replaces=''):
    f = row.facts
    if row.issues:
        raise DataError('; '.join(row.issues))
    if not f['fee_amount'] and f['fee_asset_id']:
        raise DataError('Unknown asset fee quantity: retain evidence until the movement is known.')
    boundary = f['event_type'] in ('opening', 'checkpoint')
    stamp = datetime.fromisoformat((f.get('effective_date')+'T00:00:00+00:00') if boundary or not f['timestamp_utc'] else f['timestamp_utc'].replace('Z', '+00:00'))
    # Legacy timestamps retain their precision; date-only entries use the reviewed order.
    micros = ((stamp.hour*60+stamp.minute)*60+stamp.second)*1000000+stamp.microsecond
    sequence = (86400000000 if boundary else micros)*1000000+int(f['sequence'] or '0')
    def position(account, asset):
        try:
            return mapping[account, asset]
        except KeyError:
            raise DataError(f'Map the custody position for {account} / {asset}.') from None
    asset_charges, charges = (), ()
    if f['fee_asset_id'] and f['fee_amount'] != '0':
        asset_charges = (AssetCharge(position(f['account_id'], f['fee_asset_id']), Decimal(f['fee_amount'])),)
    elif f['fee_currency'] and f['fee_amount'] != '0':
        charges = (Charge(Decimal(f['fee_amount']) if f['fee_amount'] else None, f['fee_currency']),)
    kind = {'stake': 'transfer', 'unstake': 'transfer', 'fee': 'asset_fee', 'cash_fee': 'fee'}.get(f['event_type'], f['event_type'])
    other = (position(f['counter_account_id'], f['asset_id']) if kind == 'transfer' else
             position(f['account_id'], f['received_asset_id']) if kind == 'swap' else '')
    costs = ()
    if f.get('schema_version') in ('2', '3') and kind in ('opening', 'checkpoint', 'inventory_in', 'transfer_in'):
        from portfolio_app.cost_basis import component
        cost_quantity = f.get('cost_quantity') or f['quantity']
        part = component(cost_quantity, Decimal(f['acquisition_cost']) if f['acquisition_cost'] else None, f['acquisition_currency'])
        if f['acquisition_cost']:
            part.update(quality=f['cost_quality'], source=f['cost_source'])
        costs = (part,)
        if Decimal(cost_quantity) < Decimal(f['quantity']):
            from decimal import localcontext
            with localcontext() as context:
                context.prec = 60
                costs += (component(Decimal(f['quantity'])-Decimal(cost_quantity), None, f['acquisition_currency']),)
    return SecurityEvent(row.identity, kind, stamp.date().isoformat(), sequence,
        quantity=Decimal(f['quantity']), amount=Decimal(f['gross_amount']) if f['gross_amount'] else (Decimal(f['valuation_amount']) if kind in ('transfer_in', 'transfer_out') and f['valuation_amount'] else None),
        currency=f['currency'] or (f['valuation_currency'] if kind in ('transfer_in', 'transfer_out') else ''), charges=charges, charges_known=bool(f['fee_amount']),
        other_position_id=other, replaces=replaces, source='transaction_csv_v'+f['schema_version'],
        received_quantity=Decimal(f['received_quantity'] or '0'),
        valuation_amount=Decimal(f['valuation_amount']) if f['valuation_amount'] else None,
        valuation_currency=f['valuation_currency'], asset_charges=asset_charges,
        timestamp_utc='' if boundary else f['timestamp_utc'], group_id=f['group_id'],
        note=f.get('note') or f['event_type'], costs=costs,
        observed_at=f['timestamp_utc'] if boundary else '',
        flow_classification=f.get('flow_classification',''),
        valuation_quality=f.get('valuation_quality') or 'recorded',
        valuation_source=f.get('valuation_source',''), estimated=f.get('estimated') == 'true')


def _previous_event(receipt, receipts, books, primary):
    """Resolve source revisions through pending and manually corrected events."""
    previous = receipt.replaces
    owner = None
    while previous:
        owner = next((b for b in books.values() if any(e.id == previous for e in b.records)), None)
        if owner:
            break
        previous = receipts[previous].replaces
    if owner and owner.position_id != primary:
        raise DataError('Correction cannot change its source position. Retain it for separate reconciliation.')
    if owner:
        descendants = {e.replaces: e.id for e in owner.records if e.replaces and e.id != receipt.id}
        while previous in descendants:
            previous = descendants[previous]
    return previous if owner else ''


def prepare_import(journal, holdings, rows, mapping, *, cash_accounts=None, selected_ids=(),
                   starts_at_zero=False, allow_incomplete=False, correction_ids=(), accept_duplicates=False,
                   target='EUR', historical=None, aggregate_accounts=()):
    """Retain every row; only selected, explicitly interpretable rows become active.

    A correction replaces the whole economic event. Existing history outside the
    selected import remains authoritative. Source snapshots are never additive.
    """
    from portfolio_app.account_journal import replay
    cash_accounts = cash_accounts or {}
    selected_ids, correction_ids = set(selected_ids), set(correction_ids)
    rows = tuple(rows)
    aggregate_accounts = tuple(sorted(set(aggregate_accounts)))
    aggregate_id = 'aggregate:'+digest(aggregate_accounts) if aggregate_accounts else ''
    if aggregate_accounts:
        if any(cash_accounts.values()):
            raise DataError('Aggregate asset imports use outside tracked cash; keep linked cash in custody imports.')
        if any(a not in aggregate_accounts for r in rows for a, _ in r.pairs):
            raise DataError('Review every source location in the aggregate scope; do not exclude earlier activity.')
        by_asset = {}
        for (account, asset), pos in mapping.items():
            if by_asset.setdefault(asset, pos) != pos:
                raise DataError('An aggregate asset must map to one reporting position across owned locations.')
        if len(set(by_asset.values())) != len(by_asset):
            raise DataError('Different source assets require different aggregate reporting positions.')
        # Existing constituent balances must be resolved separately, not hidden
        # or silently combined with a second authoritative aggregate balance.
        aggregate_positions = set(mapping.values())
        current_rows = effective_holdings(holdings, journal.securities)
        for pos in aggregate_positions:
            instrument = current_rows.loc[current_rows.position_id.eq(pos), 'id']
            if instrument.empty:
                raise DataError('A mapped holding is missing.')
            others = current_rows.loc[current_rows.id.eq(instrument.iloc[0]) & ~current_rows.position_id.isin(aggregate_positions)]
            if any(Decimal(str(q)) != 0 for q in others.shares) or any(b.active and b.position_id in set(others.position_id) for b in journal.securities):
                raise DataError('Resolve active constituent positions before applying an aggregate holding.')
    if selected_ids - {r.identity for r in rows}:
        raise DataError('Selected evidence no longer matches this preview.')
    input_keys = {}
    for row in rows:
        if all(row.key) and input_keys.setdefault(row.key, row.identity) != row.identity:
            raise DataError('The same source/event ID has conflicting rows within this upload.')
    receipts = list(journal.transaction_imports)
    known = {r.id: r for r in receipts}
    latest = {(r.facts['source_id'], r.facts['event_id']): r for r in receipts}
    books = {b.position_id: b for b in journal.securities}
    indexed = holdings.set_index('position_id', drop=False)
    added = duplicates = applied = 0
    warnings, touched = [], set()
    instruments = {}
    retained_mapping = {(r.facts['source_id'], a, asset): pos for r in receipts for a, asset, pos in r.positions}
    for row in rows:
        selected = row.identity in selected_ids
        old = latest.get(row.key)
        existing = known.get(row.identity)
        pairs = row.pairs if not row.issues else ()
        positions = []
        for account, asset in pairs:
            pos = mapping.get((account, asset)) or retained_mapping.get((row.key[0], account, asset))
            if not pos:
                if selected:
                    raise DataError(f'Map {account} / {asset} before applying its movements.')
                continue
            if pos not in indexed.index:
                raise DataError('A mapped holding is missing. Reload the position mapping.')
            previous = retained_mapping.get((row.key[0], account, asset))
            if previous and previous != pos:
                raise DataError('A retained source custody mapping cannot silently change.')
            instrument = indexed.loc[pos, 'id']
            if instruments.setdefault((row.key[0], asset), instrument) != instrument:
                raise DataError('The same source asset must map to the same instrument across custody accounts.')
            positions.append((account, asset, pos))
        resolved = {(a, asset): pos for a, asset, pos in positions}
        if existing:
            duplicates += 1
            if existing.positions and resolved and any(resolved.get((a, asset), pos) != pos for a, asset, pos in existing.positions):
                raise DataError('Reimport mapping differs from retained evidence.')
        elif old and all(row.key) and row.identity not in correction_ids:
            raise DataError('An existing source/event ID has different facts. Review it as an explicit correction.')
        else:
            receipt = ImportReceipt(row.identity, row.raw, row.facts, row.issues, tuple(positions) if len(positions) == len(pairs) else (), old.id if old and all(row.key) else '')
            receipts.append(receipt); known[receipt.id] = receipt; latest[row.key] = receipt
            added += 1
        if not selected:
            if not row.issues and len(resolved) == len(pairs) and pairs:
                primary = resolved[row.facts['account_id'], row.facts['asset_id']]
                book = books.get(primary) or book_for(journal, indexed.loc[primary].to_dict())
                if not any(e.id == row.identity or e.replaces == row.identity for e in book.records):
                    try:
                        draft = event_for(row, resolved, replaces=_previous_event(known[row.identity], known, books, primary))
                    except DataError:
                        pass  # Retain unrepresentable source evidence for later correction.
                    else:
                        books[primary] = append_evidence(book, (draft,))
            continue
        if row.issues:
            raise DataError('; '.join(row.issues))
        if aggregate_accounts and row.facts['event_type'] in ('opening', 'checkpoint'):
            raise DataError('A custody balance cannot establish aggregate inventory. Record the aggregate boundary in History or import it separately.')
        if row.incomplete and not allow_incomplete:
            raise DataError('Review missing monetary evidence before applying known movements.')
        if existing and latest[row.key].id != existing.id:
            warnings.append('Superseded evidence skipped; the latest correction remains selected.')
            continue
        if len(resolved) != len(pairs):
            raise DataError('Map all positions touched by the event.')
        if existing and not existing.positions:
            bound = replace(existing, positions=tuple(positions))
            receipts[receipts.index(existing)] = bound
            known[bound.id] = bound
        primary = resolved[row.facts['account_id'], row.facts['asset_id']]
        for account, asset, pos in positions:
            if pos not in books:
                books[pos] = book_for(journal, indexed.loc[pos].to_dict())
            book = books[pos]
            if aggregate_accounts and book.account_id:
                raise DataError('Aggregate asset imports cannot adopt an existing linked cash book.')
            cash = cash_accounts.get(account, book.account_id)
            if book.active and book.account_id != cash:
                raise DataError('An active cash binding cannot change during import.')
            custody = book.custody_id or aggregate_id or account
            if book.custody_id and book.custody_id != custody:
                raise DataError('Different custody accounts cannot be mapped onto the same holding.')
            books[pos] = replace(book, account_id=cash, custody_id=custody)
        replaces = _previous_event(known[row.identity], known, books, primary)
        event = event_for(row, resolved, replaces=replaces)
        book = books[primary]
        if book.account_id:
            from portfolio_app.derivative_types import money
            posted = ([event.gross] if event.kind in ('buy','sell') else []) + [c.amount for c in event.charges]
            if any(value is not None and money(value) != value for value in posted):
                raise DataError('Linked cash cannot post sub-cent evidence exactly. Supply the recorded settlement amount before activation.')
        if event.id in book.active_ids or any(e.replaces == event.id for e in book.records):
            continue  # An identical source never undoes a reviewed edit or removal.
        book = append_evidence(book, (event,))
        ancestors = set()
        by_id = {e.id: e for e in book.records}
        previous = replaces
        while previous:
            ancestors.add(previous)
            touched.update(affected_positions(book, by_id[previous]))
            previous = by_id[previous].replaces
        book = replace(book, active_ids=tuple(i for i in book.active_ids if i not in ancestors) + (event.id,))
        books[primary] = book
        touched.update(affected_positions(book, event)); applied += 1
        warnings.extend(row.incomplete)
        if row.facts.get('cost_quality') == 'estimated' or row.facts.get('valuation_quality') == 'estimated':
            warnings.append('Supplied estimates retained; affected gains are approximate')
        if event.kind == 'checkpoint':
            warnings.append('Balance checkpoint replaces units and remaining cost; it does not certify earlier history')
            if not event.observed_at:
                warnings.append('Observation date unknown; effective_date is the reporting boundary, not an acquisition or observation date')
        if event.flow_classification == 'unresolved':
            warnings.append('Inventory origin or purpose unresolved; no income or external flow assumed')
    # Start boundaries for destination-only histories too. An explicit confirmation
    # is necessary even when the current summary happens to contain zero units.
    all_events = [(b, e) for b in books.values() for e in selected_events(b)]
    for pos in touched:
        book = books[pos]
        relevant = [e for b, e in all_events if pos in affected_positions(b, e) and e.kind not in ('opening', 'checkpoint')]
        earliest = min((e.date for e in relevant), default=None)
        openings = [e for e in selected_events(book) if e.kind in ('opening', 'checkpoint')]
        if not relevant:
            continue
        if not openings:
            if not starts_at_zero:
                raise DataError('Confirm zero holdings before the first transaction, or supply opening inventory in History.')
            boundary = (datetime.fromisoformat(earliest).date()-timedelta(days=1)).isoformat()
            opening = SecurityEvent('import-zero:'+digest((pos, boundary)), 'opening', boundary,
                                    source='confirmed_zero_start')
            book = append_evidence(book, (opening,))
            books[pos] = replace(book, active_ids=(opening.id, *book.active_ids))
        elif earliest <= min(e.date for e in openings):
            raise DataError('Imported history precedes its opening boundary. Review the starting inventory first.')
    # Dependent events require an unambiguous order regardless of their source.
    order = {}
    boundaries = set()
    def internal_principal(book, event):
        return (book.custody_id.startswith('aggregate:') and event.kind == 'transfer'
                and event.other_position_id == book.position_id and event.charges_known
                and not event.asset_charges and not event.charges)
    for book in books.values():
        for e in selected_events(book):
            if not affected_positions(book, e).intersection(touched):
                continue
            if e.kind in ('opening', 'checkpoint'):
                key = book.position_id, e.date, e.sequence
                if key in boundaries:
                    raise DataError('Balances on the same day need distinct explicit sequence values.')
                boundaries.add(key)
                continue
            if internal_principal(book, e):
                continue  # No inventory/cost/cash effect and no ordering to infer.
            key = e.date, e.sequence
            affected = affected_positions(book, e)
            for other_positions, other in order.get(key, ()):
                if affected & other_positions :
                    raise DataError('Simultaneous dependent events need distinct explicit sequence values.')
            order.setdefault(key, []).append((affected, e))
        economic_book = replace(book, active_ids=tuple(e.id for e in selected_events(book) if not internal_principal(book, e)))
        if duplicate_candidates(economic_book) and book.position_id in touched and not accept_duplicates:
            raise DataError('Matching economic transactions require review as distinct executions.')
    version = max(journal.version, 5 if any(r.facts.get('schema_version') == '3' for r in receipts) else 4 if any(r.facts.get('schema_version') == '2' for r in receipts) else 3)
    proposed = replace(journal, version=version, securities=tuple(books.values()), transaction_imports=tuple(receipts))
    from portfolio_app.history_commands import history_comparisons
    comparisons = [dict(c, before_cost=None if c['before_cost'] is None else str(c['before_cost']),
                        after_cost=None if c['after_cost'] is None else str(c['after_cost']))
        for c in history_comparisons(journal, proposed, holdings, touched, target=target, historical=historical)]
    before, after = replay(journal), replay(proposed)
    keys = set(before.cash) | set(after.cash) | set(before.unknown_cash) | set(after.unknown_cash)
    cash_changes = []
    for account, currency in sorted(keys):
        key = account, currency
        old = None if key in before.unknown_cash else before.cash.get(key, Decimal(0))
        new = None if key in after.unknown_cash else after.cash.get(key, Decimal(0))
        if old != new:
            cash_changes.append(dict(account=account, currency=currency, before=old, after=new))
    checkpoints = []
    for book in proposed.securities:
        for event in selected_events(book):
            if event.kind != 'checkpoint' or event.id not in selected_ids:
                continue
            earlier = replay_securities(proposed.securities, as_of=event.date, sequence=event.sequence-1).positions.get(book.position_id)
            from decimal import localcontext
            with localcontext() as context:
                context.prec = 60
                known = earlier is not None and bool(earlier.boundary)
                checkpoints.append(dict(position_id=book.position_id, effective_date=event.date,
                    observed_at=event.observed_at or 'Unknown', explained_units=str(earlier.quantity) if known else None,
                    checkpoint_units=str(event.quantity),
                    unresolved_difference=str(event.quantity-earlier.quantity) if known else None))
    return ImportPreview(proposed, added, duplicates, applied, tuple(comparisons), tuple(cash_changes),
                         tuple(dict.fromkeys(warnings)), tuple(checkpoints))
