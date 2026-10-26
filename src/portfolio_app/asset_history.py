"""Approximate asset-level performance, independent of custody and FIFO.

An explicit opening starts forward replay; ending observations never determine
earlier units. Internal principal movements cancel.
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
            # Balances are boundaries, never economic movements. A custody
            # opening cannot establish inventory across all owned locations.
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


def asset_history(projection, asset, opening_date, opening_quantity, prices, *,
                  currency='EUR', start=None, end=None):
    """Daily closes plus midpoint-weighted external flows; no FIFO necessary.

    Unclassified movements retain known units but invalidate dependent gains
    and returns. Missing prices/amounts break cumulative results.
    Reward value is a gain breakdown only, never added twice to total gain.
    """
    with localcontext() as context:
        context.prec = 60
        return _asset_history(projection, asset, opening_date, Decimal(opening_quantity),
                              prices, currency=currency, start=start, end=end)


def _asset_history(projection, asset, opening_date, opening, prices, *, currency, start, end):
    beginning = date.fromisoformat(opening_date)
    if not opening.is_finite() or opening < 0:
        raise DataError('Supply a nonnegative independently established opening quantity.')
    movements = [m for m in projection.movements if m.asset == asset]
    for p in prices.values():
        if not p.is_finite() or p <= 0:
            raise DataError('Daily prices must be finite and positive.')
    if any(m.date <= opening_date for m in movements):
        raise DataError('Movements must follow the opening boundary; retain earlier evidence separately.')
    start = date.fromisoformat(start) if start else beginning
    end = date.fromisoformat(end) if end else max([beginning, *(date.fromisoformat(d) for d in prices), *(date.fromisoformat(m.date) for m in movements)])
    if start < beginning or end < start:
        raise DataError('Invalid asset history period.')
    by_day = defaultdict(list)
    for movement in movements:
        by_day[movement.date].append(movement)
    quantity = opening + sum((m.quantity for m in movements if m.date <= start.isoformat()), ZERO)
    reasons = list(projection.issues)
    partial = bool(reasons)
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
                if m.kind == 'unresolved':
                    day_ok = False
                    partial = True
                    reasons.append('Inventory origin remains unresolved; dependent gains and returns unavailable')
                if m.kind == 'flow':
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
    reasons.insert(0, 'Approximate: forward units from an explicit opening; daily closing prices and midpoint flow timing')
    return AssetHistory(tuple(points), tuple(dict.fromkeys(reasons)),
                        partial or not complete_gain or not complete_return or not reward_complete, opening)


def journal_asset_history(journal, position_id, prices, observations, *, currency='EUR', start=None, end=None):
    """Approximate prices/flow timing over the existing authoritative forward book.

    Reuse securities replay and scope-aware external flows, including manual
    follow-up events. Retained-only revisions never replace activated history.
    Checkpoints inside the measured interval break gains rather than become flow.
    """
    from dataclasses import replace
    from portfolio_app.history_observations import HistoryScope, Observation, lookup
    from portfolio_app.personal_performance import convert, external_flows
    from portfolio_app.security_history import replay_securities, selected_events, affected_positions
    books = journal.securities
    events = [(b, e) for b in books for e in selected_events(b)
              if position_id in affected_positions(b, e)]
    boundaries = [e.date for b, e in events if b.position_id == position_id and e.kind in ('opening', 'checkpoint')]
    if not boundaries:
        raise DataError('Opening inventory unknown. Review recorded net changes before activating forward history.')
    start = max(start or min(boundaries), min(boundaries))
    end = end or max(prices, default=start)
    if end < start:
        raise DataError('No supported forward history in this period.')
    # Daily prices may approximate a transfer value; they are not saved as exact
    # intraday observations and do not affect the existing TWR calculation.
    extra = tuple(Observation('approx:'+position_id+':'+d, 'price', position_id, d, p, currency,
                              source='approximate_daily_close') for d, p in prices.items()
                  if not lookup(observations, 'price', position_id, d))
    observations = replace(observations, observations=(*observations.observations, *extra))
    flows, issues = external_flows(journal, HistoryScope((position_id,)), observations, start, end, currency)
    movements = [AssetMovement(f.id, position_id, f.date, ZERO, 'flow', f.amount, currency,
                               'Recorded flow amount missing' if f.amount is None else '') for f in flows]
    issues = list(issues)
    initial = replay_securities(books, as_of=start).positions[position_id].quantity
    previous = initial
    with localcontext() as context:
        context.prec = 60
        for stamp in sorted({e.date for _, e in events if start < e.date <= end}):
            quantity = replay_securities(books, as_of=stamp).positions[position_id].quantity
            movements.append(AssetMovement('units:'+stamp, position_id, stamp, quantity-previous, 'units'))
            previous = quantity
    for book, event in events:
        if book.position_id != position_id or not start < event.date <= end:
            continue
        if event.kind in ('opening', 'checkpoint'):
            issues.append('A balance boundary interrupts this interval; its difference is not gain or funding')
        if event.kind in ('reward', 'distribution'):
            amount = event.valuation_amount if event.kind == 'reward' else event.amount
            denomination = event.valuation_currency if event.kind == 'reward' else event.currency
            value = convert(amount, denomination, currency, event.date, observations)
            movements.append(AssetMovement(event.id+':income', position_id, event.date, ZERO,
                                           'reward', value, currency))
        if event.kind == 'note':
            issues.append('Unresolved historical evidence affects this interval')
        if not event.charges_known and event.kind in ('buy', 'sell', 'distribution', 'reward', 'swap', 'asset_fee', 'transfer'):
            issues.append('Charges are incomplete; dependent gains and returns unavailable')
    result = asset_history(AssetProjection(tuple(movements), tuple(dict.fromkeys(issues)), 0),
                           position_id, start, initial, prices, currency=currency, start=start, end=end)
    # Unit reconciliation, dated values and cost/completeness attestations are
    # independent. Retain useful measured results with their qualifications.
    from portfolio_app.personal_performance import coverage_reasons
    qualifications = coverage_reasons(journal, HistoryScope((position_id,)), observations, start, end)
    if any(e.valuation_quality == 'estimated' or any(p.get('quality') == 'estimated' for p in e.costs)
           for _, e in events if e.date <= end):
        qualifications = (*qualifications, 'Supplied monetary estimates retained with source provenance')
    return replace(result, partial=result.partial or bool(qualifications),
                   reasons=tuple(dict.fromkeys((*result.reasons, *qualifications))))
