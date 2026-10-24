"""Quantity diagnostics for saved evidence, independent of activation and cost.

These are signed changes, never balances inferred from an unconfirmed zero.
Undated movements may contribute a known quantity without acquiring a date.
"""
from collections import defaultdict
from decimal import Decimal, InvalidOperation, localcontext


def link_transfer_deductions(rows, links):
    """Explicitly reviewed source/event links to separately evidenced deductions.

    Return normalized corrections, preserving raw evidence. This is not fuzzy
    matching: callers must prove the link from source provenance. The charge's
    classification/value stays unknown; its known units belong to inventory_out.
    Persist changed facts through the ordinary correction review.
    """
    from dataclasses import replace
    from portfolio_app.holdings import DataError
    from portfolio_app.transaction_csv import normalize
    rows = tuple(rows)
    indexed = {r.key: r for r in rows}
    if len(indexed) != len(rows) or len(set(links.values())) != len(links):
        raise DataError('Review unique transaction and deduction identities before linking quantities.')
    output = []
    for row in rows:
        if row.key not in links:
            output.append(row)
            continue
        deduction = indexed.get(links[row.key])
        f, d = row.facts, deduction.facts if deduction else {}
        if (row.issues or deduction is None or deduction.issues
                or f['event_type'] not in ('transfer', 'stake', 'unstake')
                or f['fee_amount'] or not f['fee_asset_id']
                or d['event_type'] != 'inventory_out' or d.get('flow_classification') != 'unresolved'
                or (f['account_id'], f['fee_asset_id']) != (d['account_id'], d['asset_id'])
                or f['timestamp_utc'] != d['timestamp_utc']):
            raise DataError('A linked deduction must evidence the same dated paying location and asset, with unresolved classification.')
        # The linked inventory record now owns the entire evidenced deduction.
        # Zero here means no *additional* charge on the principal event, not
        # that the source's unresolved deduction was free or classified as fee.
        facts = {**f, 'schema_version': '2', 'fee_asset_id': '', 'fee_amount': '0',
                 'note': (f.get('note', '')+'; Known unit deduction recorded separately in '+
                          '/'.join(deduction.key)+'. Charge classification remains unresolved.').lstrip('; ')}
        normalized = normalize(facts)
        if normalized.issues:
            raise DataError('; '.join(normalized.issues))
        output.append(replace(normalized, raw=row.raw))
    if set(links) - indexed.keys():
        raise DataError('A linked source transaction is missing.')
    return tuple(output)


def asset_movement_totals(rows, owned_accounts=None):
    """Sum known signed legs across owned locations, without assuming an opening.

    With no location filter every recorded location participates. Holdings and
    ending observations deliberately are not inputs to this calculation.
    """
    owned = None if owned_accounts is None else set(owned_accounts)
    totals = defaultdict(lambda: dict(quantity=Decimal(0), undated=0, unknown=0))
    with localcontext() as context:
        context.prec = 60
        for item in movement_totals(rows):
            if owned is not None and item['account'] not in owned:
                continue
            total = totals[item['asset']]
            total['quantity'] += Decimal(item['known_unit_change'])
            total['undated'] += item['undated_movements']
            total['unknown'] += item['unknown_asset_charges']
    return tuple(dict(asset=asset, known_unit_change=str(total['quantity']),
        undated_movements=total['undated'], unknown_asset_charges=total['unknown'])
        for asset, total in sorted(totals.items()))


def compare_asset_changes(rows, references, owned_accounts=None):
    """Arithmetic comparison, explicitly not reconciliation from a known opening.

    References supply quantity, scope and optional observation date separately.
    No reference quantity is ever fed back into normalization or replay.
    """
    totals = {t['asset']: t for t in asset_movement_totals(rows, owned_accounts)}
    output = []
    with localcontext() as context:
        context.prec = 60
        for asset in sorted(totals.keys() | references.keys()):
            total, reference = totals.get(asset), references.get(asset)
            quantity = Decimal(reference['quantity']) if reference else None
            change = Decimal(total['known_unit_change']) if total else None
            output.append(dict(asset=asset, known_unit_change=str(change) if change is not None else None,
                reference_quantity=str(quantity) if quantity is not None else None,
                reference_minus_net=str(quantity-change) if quantity is not None and change is not None else None,
                reference_date=reference.get('date') or 'Unknown' if reference else '',
                reference_scope=reference.get('scope', '') if reference else '',
                unknown_asset_charges=total['unknown_asset_charges'] if total else 0))
    return tuple(output)


def recorded_asset_changes(rows, asset, owned_accounts=None):
    """Dated cumulative net changes; undated quantities stay in totals only."""
    rows = tuple(rows)
    identities = defaultdict(set)
    for row in rows:
        identities[row.key].add(row.identity)
    seen, changes = set(), defaultdict(lambda: Decimal(0))
    with localcontext() as context:
        context.prec = 60
        for row in rows:
            if row.identity in seen or len(identities[row.key]) != 1:
                continue
            seen.add(row.identity)
            if not row.facts['timestamp_utc']:
                continue
            for item in asset_movement_totals((row,), owned_accounts):
                if item['asset'] == asset:
                    changes[row.date] += Decimal(item['known_unit_change'])
        total, points = Decimal(0), []
        for stamp, change in sorted(changes.items()):
            total += change
            points.append(dict(date=stamp, net_change=total))
    return tuple(points)


def movement_totals(rows):
    rows = tuple(rows)
    keys = defaultdict(set)
    for row in rows:
        keys[row.key].add(row.identity)
    seen = set()
    changes = defaultdict(lambda: dict(quantity=Decimal(0), undated=0, unknown_charges=0, events=0))
    with localcontext() as context:
        context.prec = 60
        for row in rows:
            if row.identity in seen or len(keys[row.key]) != 1:
                continue
            seen.add(row.identity)
            f = row.facts
            kind = f['event_type']
            if kind not in {'buy','sell','reward','swap','transfer','stake','unstake','fee','inventory_in','inventory_out'}:
                continue
            try:
                quantity = Decimal(f['quantity'])
                if not quantity.is_finite() or quantity <= 0:
                    continue
            except (InvalidOperation, ValueError):
                continue
            if not f['account_id'] or not f['asset_id']:
                continue

            def add(account, asset, amount):
                if not account or not asset:
                    return
                item = changes[account,asset]
                item['quantity'] += amount
                item['events'] += 1
                item['undated'] += int(not f['timestamp_utc'])

            add(f['account_id'],f['asset_id'],quantity if kind in {'buy','reward','inventory_in'} else -quantity)
            if kind in {'transfer','stake','unstake'}:
                add(f['counter_account_id'],f['asset_id'],quantity)
            elif kind == 'swap':
                try:
                    received = Decimal(f['received_quantity'])
                    if received.is_finite() and received > 0:
                        add(f['account_id'],f['received_asset_id'],received)
                except (InvalidOperation, ValueError):
                    pass
            if f['fee_asset_id']:
                if not f['fee_amount']:
                    changes[f['account_id'],f['fee_asset_id']]['unknown_charges'] += 1
                else:
                    try:
                        fee = Decimal(f['fee_amount'])
                        if fee.is_finite() and fee > 0:
                            add(f['account_id'],f['fee_asset_id'],-fee)
                        elif fee != 0:
                            changes[f['account_id'],f['fee_asset_id']]['unknown_charges'] += 1
                    except (InvalidOperation, ValueError):
                        changes[f['account_id'],f['fee_asset_id']]['unknown_charges'] += 1
    return tuple(dict(account=account,asset=asset,known_unit_change=str(item['quantity']),
        undated_movements=item['undated'],unknown_asset_charges=item['unknown_charges'],movements=item['events'])
        for (account,asset),item in sorted(changes.items()))
