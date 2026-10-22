"""Approximate asset-level performance, independent of custody and FIFO.

Closing units anchor a conditional reconstruction of the supplied movements;
they do not certify complete history. Internal principal movements cancel.
Daily Dietz uses an explicit midpoint convention for external flows.
No missing market prices or financial facts are filled in.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, localcontext

from portfolio_app.holdings import DataError

ZERO = Decimal(0)


@dataclass(frozen=True)
class AssetMovement:
    id: str
    asset: str
    date: str
    quantity: Decimal
    kind: str  # flow, reward, expense, unresolved
    amount: Decimal | None = None  # signed contribution, positive reward value
    currency: str = ''
    note: str = ''


@dataclass(frozen=True)
class AssetProjection:
    movements: tuple[AssetMovement, ...]
    issues: tuple[str, ...]
    ignored_internal: int


def project_asset_movements(rows, owned_accounts, *, assets=None):
    """Collapse explicitly owned locations; never infer ownership from labels.

    Identical rows deduplicate. Conflicting revisions must be resolved by the
    caller (the journal caller selects latest receipts). Fees stay with the
    paying account/asset even when its principal movement cancels.
    """
    owned = set(owned_accounts)
    wanted = set(assets) if assets is not None else None
    seen, output, issues, ignored = {}, [], [], 0

    def add(row, asset, quantity, kind, amount=None, currency='', suffix='', note=''):
        if wanted is None or asset in wanted:
            output.append(AssetMovement(row.identity+suffix, asset, row.date,
                quantity, kind, amount, currency, note))

    for row in rows:
        if row.key in seen:
            if seen[row.key] != row.identity:
                raise DataError('Resolve conflicting source/event IDs before calculating asset history.')
            continue
        seen[row.key] = row.identity
        f = row.facts
        inside, other = f['account_id'] in owned, f['counter_account_id'] in owned
        kind, asset = f['event_type'], f['asset_id']
        if not inside and not (other and kind in ('transfer', 'stake', 'unstake')):
            continue
        touched = {asset, f['received_asset_id'], f['fee_asset_id']} - {''}
        if wanted is not None and not touched & wanted:
            continue
        if row.issues:
            issues.append('Invalid or undated movements require review: '+ '; '.join(row.issues))
            continue
        if kind in ('opening', 'checkpoint'):
            # A custody balance is not a new portfolio contribution. The
            # explicitly selected aggregate balance supplies the anchor.
            continue
        q = Decimal(f['quantity'])
        gross = Decimal(f['gross_amount']) if f['gross_amount'] else None
        valuation = Decimal(f['valuation_amount']) if f['valuation_amount'] else None
        if kind in ('transfer', 'stake', 'unstake'):
            if inside and other:
                ignored += 1
            else:
                add(row, asset, -q if inside else q, 'flow', note='Transfer across selected locations; valued at daily close')
        elif kind in ('buy', 'sell'):
            sign = 1 if kind == 'buy' else -1
            add(row, asset, sign*q, 'flow', None if gross is None else sign*gross, f['currency'],
                note='Trade consideration missing' if gross is None else '')
        elif kind == 'swap':
            add(row, asset, -q, 'flow', -valuation if valuation is not None else None,
                f['valuation_currency'], note='Swap valuation missing' if valuation is None else '')
            add(row, f['received_asset_id'], Decimal(f['received_quantity']), 'flow', valuation,
                f['valuation_currency'], ':received', 'Swap valuation missing' if valuation is None else '')
        elif kind == 'reward':
            add(row, asset, q, 'reward', valuation, f['valuation_currency'])
        elif kind == 'fee':
            add(row, asset, -q, 'expense')
        elif kind in ('inventory_in', 'inventory_out'):
            sign = 1 if kind == 'inventory_in' else -1
            classification = 'flow' if f.get('flow_classification') == 'external' else 'unresolved'
            add(row, asset, sign*q, classification,
                None if valuation is None else sign*valuation, f['valuation_currency'])
        else:
            issues.append('Unsupported movement type: '+kind)
        if inside:
            fee = Decimal(f['fee_amount']) if f['fee_amount'] else None
            if fee is None and (wanted is None or f['fee_asset_id'] in wanted
                                or (not f['fee_asset_id'] and asset in wanted)):
                # Unknown units cannot be reconciled by guessing zero. Unknown
                # fiat charges also cannot be silently excluded from returns.
                issues.append('A fee paid inside the selected locations is unknown')
            elif fee:
                if f['fee_asset_id']:
                    add(row, f['fee_asset_id'], -fee, 'expense', suffix=':fee')
                elif f['fee_currency']:
                    # Paying an expense externally is additional funding,
                    # with no additional retained value: it reduces gain once.
                    add(row, asset, ZERO, 'flow', fee, f['fee_currency'], ':fee')
                else:
                    issues.append('Fee denomination missing')
    return AssetProjection(tuple(sorted(output, key=lambda m: (m.date, m.id))),
                           tuple(dict.fromkeys(issues)), ignored)


@dataclass(frozen=True)
class AssetHistory:
    points: tuple[dict, ...]
    reasons: tuple[str, ...]
    partial: bool
    opening_quantity: Decimal


def asset_history(projection, asset, anchor_date, anchor_quantity, prices, *,
                  currency='EUR', start=None, end=None):
    """Daily closes plus midpoint-weighted external flows; no FIFO necessary.

    An unclassified movement is neutralized at its supplied/daily value when
    reporting *known* gain. It is neither asserted to be funding nor income;
    the result stays partial. Missing prices/amounts break cumulative results.
    Reward value is a gain breakdown only, never added twice to total gain.
    """
    with localcontext() as context:
        context.prec = 60
        return _asset_history(projection, asset, anchor_date, Decimal(anchor_quantity),
                              prices, currency=currency, start=start, end=end)


def _asset_history(projection, asset, anchor_date, anchor_quantity, prices, *, currency, start, end):
    anchor = date.fromisoformat(anchor_date)
    if not anchor_quantity.is_finite() or anchor_quantity < 0:
        raise DataError('Supply a nonnegative ending quantity.')
    movements = [m for m in projection.movements if m.asset == asset]
    for p in prices.values():
        if not p.is_finite() or p <= 0:
            raise DataError('Daily prices must be finite and positive.')
    earliest = min((date.fromisoformat(m.date) for m in movements), default=anchor)
    beginning = min(earliest-timedelta(days=1), anchor)
    start = date.fromisoformat(start) if start else beginning
    end = date.fromisoformat(end) if end else anchor
    if start < beginning or end < start:
        raise DataError('Invalid asset history period.')
    opening = anchor_quantity - sum((m.quantity for m in movements if m.date <= anchor_date), ZERO)
    by_day = defaultdict(list)
    for movement in movements:
        by_day[movement.date].append(movement)
    quantity = opening + sum((m.quantity for m in movements if m.date <= start.isoformat()), ZERO)
    reasons = list(projection.issues)
    partial = bool(reasons)
    if opening < 0:
        reasons.append('Supplied closing balance and movements imply negative starting units')
        partial = True
    cash, rewards, factor = ZERO, ZERO, Decimal(1)
    capital = None
    previous_value = None
    complete_gain = not projection.issues and opening >= 0
    complete_return = complete_gain
    reward_complete = True
    points = []
    day = start
    while day <= end:
        stamp = day.isoformat()
        flow, income = ZERO, ZERO
        price = prices.get(stamp)
        day_ok = True
        if day > start:
            for m in by_day[stamp]:
                quantity += m.quantity
                amount = m.amount
                if amount is not None and amount != 0 and m.currency != currency:
                    amount = None
                    if m.kind in ('flow', 'unresolved'):
                        day_ok = False
                    reasons.append('A movement needs dated currency conversion')
                if m.kind in ('flow', 'unresolved'):
                    if amount is None:
                        if m.note.endswith('missing') or m.currency:
                            day_ok = False
                            reasons.append('Recorded trade or swap value missing')
                        elif price is not None:
                            amount = m.quantity * price
                        else:
                            day_ok = False
                            reasons.append('A movement needs its dated market price')
                    if amount is not None:
                        flow += amount
                    if m.kind == 'unresolved':
                        partial = True
                        reasons.append('Unclassified movements excluded from gain at market value; their origin remains unresolved')
                elif m.kind == 'reward':
                    if amount is None:
                        amount = m.quantity * price if price is not None and not m.currency else None
                    if amount is None:
                        reward_complete = False
                        reasons.append('Some reward values are unavailable')
                    else:
                        income += amount
        value = quantity*price if price is not None and quantity >= 0 else ZERO if quantity == 0 else None
        if quantity < 0:
            reasons.append('Reconstructed units become negative; review missing or conflicting movements')
            day_ok = False
        if day == start:
            capital = value
            complete_gain &= value is not None
            complete_return &= value is not None
        else:
            cash += flow
            rewards += income
            complete_gain &= day_ok
            if complete_return and day_ok and value is not None and previous_value is not None:
                denominator = previous_value + flow/2
                period_gain = value-previous_value-flow
                if denominator > 0 and 1+period_gain/denominator >= 0:
                    factor *= 1+period_gain/denominator
                elif denominator == 0 and period_gain == 0:
                    pass
                else:
                    complete_return = False
                    reasons.append('Daily return has no positive capital denominator')
            else:
                complete_return = False
        gain = value-capital-cash if complete_gain and value is not None and capital is not None else None
        points.append(dict(date=stamp, quantity=quantity, value=value,
            capital=capital+cash if complete_gain and capital is not None else None,
            gain=gain, income=rewards if reward_complete else None,
            return_pct=100*(factor-1) if complete_return else None))
        previous_value = value
        day += timedelta(days=1)
    if any(p['value'] is None for p in points):
        reasons.append('Daily market prices missing; no interpolation or forward filling used')
    reasons.insert(0, 'Approximate: quantities reconstructed from the supplied ending balance and listed movements; daily closing prices and midpoint flow timing')
    return AssetHistory(tuple(points), tuple(dict.fromkeys(reasons)),
                        partial or not complete_gain or not complete_return or not reward_complete, opening)
