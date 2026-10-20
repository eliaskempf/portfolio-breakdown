"""Quantity diagnostics for saved evidence, independent of activation and cost.

These are signed changes, never balances inferred from an unconfirmed zero.
Undated movements may contribute a known quantity without acquiring a date.
"""
from collections import defaultdict
from decimal import Decimal, InvalidOperation, localcontext


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
