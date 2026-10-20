"""Historical equity, external flows and metric-specific coverage.

All observations are supplied explicitly. No current prices, current ETF weights,
latest FX or interpolation enter this module.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import math

from portfolio_app.account_journal import effective_events, replay
from portfolio_app.history_observations import HistoryScope, lookup, rate
from portfolio_app.security_history import history_fingerprint, replay_securities, selected_events, affected_positions

ZERO = Decimal(0)


@dataclass(frozen=True)
class AmountResult:
    amount: Decimal | None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExternalFlow:
    id: str
    date: str
    sequence: int
    amount: Decimal | None
    reason: str = ''


@dataclass(frozen=True)
class PersonalPerformance:
    opening_value: Decimal | None
    ending_value: Decimal | None
    net_contributions: Decimal | None
    invested_capital: Decimal | None
    gain: Decimal | None
    money_weighted: float | None
    time_weighted: float | None
    days: int
    value_reasons: tuple[str, ...]
    flow_reasons: tuple[str, ...]
    money_weighted_reasons: tuple[str, ...]
    time_weighted_reasons: tuple[str, ...]
    opening_capital: Decimal | None = None
    opening_known_gain: Decimal | None = None
    total_gain: Decimal | None = None
    capital_reasons: tuple[str, ...] = ()


def convert(amount, source, target, stamp, observations):
    if amount is None:
        return None
    if amount == 0:
        return ZERO
    fx = rate(observations, source, target, stamp)
    return amount*fx if fx is not None else None


def confirmed_empty_before(book, stamp):
    events = selected_events(book)
    return bool(events and events[0].kind == 'opening' and not events[0].quantity
                and events[0].source == 'confirmed_zero_start' and stamp < events[0].date)


def account_starts_on(journal, account, stamp):
    owners = {p.id: p.account_id for p in journal.positions}
    events = [e for e in effective_events(journal)
              if e.account_id == account or e.other_account_id == account or owners.get(e.position_id) == account]
    openings = [e for e in events if e.kind == 'cash_open']
    return bool(openings and all(e.date == stamp for e in openings) and not any(e.date < stamp for e in events))


def opening_value_at(journal, scope, observations, stamp, target):
    """Account cash openings precede same-day account trades/fees/flows.

    Security openings remain end-of-day snapshots. Their derived cash postings
    already omit payments covered by a same-day account cash opening. Do not
    apply that omission to separately recorded account fees or external funding.
    """
    if not any(account_starts_on(journal, a, stamp) for a in scope.accounts):
        return value_at(journal, scope, observations, stamp, target)
    total, reasons = ZERO, list(scope.unresolved)
    if scope.positions:
        value = value_at(journal, HistoryScope(scope.positions), observations, stamp, target)
        reasons.extend(value.reasons)
        if value.amount is not None:
            total += value.amount
    for account in scope.accounts:
        if account_starts_on(journal, account, stamp):
            for event in effective_events(journal):
                if event.account_id != account or event.date != stamp or event.kind not in ('cash_open', 'debt_open'):
                    continue
                value = convert(event.amount * (-1 if event.kind == 'debt_open' else 1), event.currency, target, stamp, observations)
                if value is None:
                    reasons.append(f'{account}: missing opening {event.currency}/{target} FX on {stamp}')
                else:
                    total += value
        else:
            value = value_at(journal, HistoryScope(accounts=(account,)), observations, stamp, target)
            reasons.extend(value.reasons)
            if value.amount is not None:
                total += value.amount
    return AmountResult(None if reasons else total, tuple(dict.fromkeys(reasons)))


def capital_at_start(journal, scope, observations, stamp, target):
    """Snapshot cost seeds known gain; it is never a return-opening valuation.

    Account equity already includes cash, debt and claims. Only securities whose
    explicit opening is on this date replace market value with recorded cost.
    Saved cost conversions are evidence; latest-FX estimates are not history.
    """
    from portfolio_app.cost_basis import resolve_cost
    from portfolio_app.history_observations import ObservationFX
    total, reasons = ZERO, list(scope.unresolved)
    states = replay_securities(journal.securities, as_of=stamp).positions
    books = {b.position_id: b for b in journal.securities}
    for identity in scope.positions:
        book, state = books.get(identity), states.get(identity)
        if book and confirmed_empty_before(book, stamp):
            continue
        if state and state.boundary == stamp:
            if not state.quantity:
                continue
            if any(c.get('conversions', {}).get(target, {}).get('method') == 'estimate' for c in state.costs):
                reasons.append(f'{identity}: estimated FX is not historical acquisition evidence')
                continue
            cost = resolve_cost(list(state.costs), target, ObservationFX(observations))
            if cost.amount is None:
                reasons.append(f'{identity}: {cost.note}')
            else:
                total += cost.exact_amount
        else:
            value = value_at(journal, HistoryScope((identity,)), observations, stamp, target)
            reasons.extend(value.reasons)
            if value.amount is not None:
                total += value.amount
    if scope.accounts:
        value = opening_value_at(journal, HistoryScope(accounts=scope.accounts), observations, stamp, target)
        reasons.extend(value.reasons)
        if value.amount is not None:
            total += value.amount
    return AmountResult(None if reasons else total, tuple(dict.fromkeys(reasons)))


def value_at(journal, scope, observations, stamp, target):
    """Exact dated equity: securities + cash - loans + futures/other claims."""
    if scope.unresolved:
        return AmountResult(None, scope.unresolved)
    supplied = lookup(observations, 'value', scope.key, stamp, currency=target)
    if supplied:
        return AmountResult(supplied.amount)
    securities = replay_securities(journal.securities, as_of=stamp)
    accounts = replay(journal, as_of=stamp)
    books = {b.position_id: b for b in journal.securities}
    total, reasons = ZERO, []

    def add(amount, denomination, identity):
        nonlocal total
        value = convert(amount, denomination, target, stamp, observations)
        if value is None:
            reasons.append(f'{identity}: missing value or {denomination}/{target} FX on {stamp}')
        else:
            total += value

    for identity in scope.positions:
        book = books.get(identity)
        state = securities.positions.get(identity)
        if book and confirmed_empty_before(book, stamp):
            continue
        if book is None or state is None or not state.boundary:
            reasons.append(f'{identity}: no opening quantity on {stamp}')
            continue
        if state.issues:
            reasons.append(f'{identity}: resolve historical events before valuing the reconstructed holding')
            continue
        if not state.quantity:
            continue
        price = lookup(observations, 'price', identity, stamp) or lookup(observations, 'price', book.instrument_id, stamp)
        if price is None:
            reasons.append(f'{identity}: missing price on {stamp}')
        else:
            add(state.quantity * price.amount, price.currency, identity)
    events = effective_events(journal)
    for account in scope.accounts:
        openings = [e for e in events if e.account_id == account and e.kind == 'cash_open']
        if not openings or any(e.date > stamp for e in openings):
            reasons.append(f'{account}: missing opening cash coverage on {stamp}')
        for (owner, currency), amount in accounts.cash.items():
            if owner == account:
                add(None if (owner, currency) in accounts.unknown_cash else amount, currency, account)
        for (owner, currency, loan), amount in accounts.loans.items():
            if owner == account:
                add(-amount, currency, loan)
        for identity, balance in accounts.positions.items():
            if balance.spec.account_id != account or not balance.quantity:
                continue
            price = lookup(observations, 'price', identity, stamp) or lookup(observations, 'price', balance.contract.id, stamp)
            if price is None or price.currency != balance.contract.currency:
                reasons.append(f'{identity}: missing exact-contract mark on {stamp}')
            else:
                add(balance.unsettled_pnl(price.amount), balance.contract.currency, identity)
        for identity, claim in accounts.pending.items():
            if claim.account_id == account:
                add(claim.amount, claim.currency, identity)
    return AmountResult(None if reasons else total, tuple(reasons))


def coverage_reasons(journal, scope, observations, start, end):
    """A quantity match cannot certify omitted offsetting events or income."""
    reasons = list(scope.unresolved)
    books = {b.position_id: b for b in journal.securities}
    fingerprint = history_fingerprint(journal)
    reviewed = any(set(scope.positions) <= set(r.scope.positions) and set(scope.accounts) <= set(r.scope.accounts)
                   and not r.scope.unresolved and r.start <= start and r.end >= end
                   and r.journal_fingerprint == fingerprint for r in observations.reviews)
    if not reviewed and not scope.accounts and len(scope.positions) == 1:
        from portfolio_app.security_history import accounting_fingerprint
        book = books.get(scope.positions[0])
        if book and book.reviews:
            review = book.reviews[-1]
            reviewed = (bool(review.complete_from) and review.complete_from <= start and review.complete_through >= end
                        and review.facts_fingerprint == accounting_fingerprint(journal))
    if not reviewed:
        reasons.append('Review complete scope membership, transactions, income and charges for this interval')
    for identity in scope.positions:
        book = books.get(identity)
        if book is None or not book.active:
            reasons.append(f'{identity}: history is not activated')
            continue
        events = selected_events(book)
        if not any(e.kind in ('opening', 'checkpoint') and e.date <= start for e in events) and not confirmed_empty_before(book, start):
            reasons.append(f'{identity}: missing opening snapshot')
        if any(e.kind in ('opening', 'checkpoint', 'note', 'cost') and start < e.date <= end
               and not (e.kind == 'opening' and not e.quantity and e.source == 'confirmed_zero_start') for e in events):
            reasons.append(f'{identity}: unresolved event or replacement boundary in interval')
        if any(e.kind in ('inventory_in','inventory_out') and e.flow_classification == 'unresolved'
               and start < e.date <= end for e in events):
            reasons.append(f'{identity}: inventory origin or purpose unresolved')
        if any(e.kind == 'checkpoint' and e.date <= end and not e.observed_at for e in events):
            reasons.append(f'{identity}: checkpoint observation date unknown')
        if any(e.kind in ('buy', 'sell', 'distribution', 'swap', 'reward', 'asset_fee', 'transfer') and start < e.date <= end and not e.charges_known and (e.source.startswith('transaction_csv_v') or e.kind in ('buy', 'sell', 'distribution')) for e in events):
            reasons.append(f'{identity}: charges have not been confirmed')
    for book in journal.securities:
        for event in selected_events(book):
            crosses = (event.kind in ('inventory_in','inventory_out') and event.flow_classification == 'external'
                       and book.position_id in scope.positions) or (
                       event.kind == 'swap' and (book.position_id in scope.positions) != (event.other_position_id in scope.positions))
            if crosses and event.valuation_quality == 'estimated' and start < event.date <= end:
                reasons.append('Estimated external-flow valuations do not establish exact percentage returns')
    active_ids = {e.id for b in journal.securities for e in selected_events(b)}
    superseded = {r.replaces for r in journal.transaction_imports if r.replaces}
    for receipt in journal.transaction_imports:
        if receipt.id in superseded or receipt.id in active_ids:
            continue
        stamp = receipt.facts.get('timestamp_utc', '')[:10]
        if (not stamp or start <= stamp <= end) and (not receipt.positions or any(p in scope.positions for _, _, p in receipt.positions)):
            reasons.append('Unapplied transaction evidence requires review')
    classes = dict(observations.classifications)
    for e in effective_events(journal):
        if e.account_id not in scope.accounts:
            continue
        if start < e.date <= end or (e.date == start and account_starts_on(journal, e.account_id, start)):
            if e.kind in ('cash_open', 'debt_open', 'snapshot') and e.date > start:
                reasons.append(f'{e.id}: account opening boundary inside interval')
            if e.kind == 'cash_adjust' and classes.get(e.id, 'boundary') == 'boundary':
                reasons.append(f'{e.id}: classify or resolve the cash adjustment')
    return tuple(dict.fromkeys(reasons))


def external_flows(journal, scope, observations, start, end, target):
    flows, reasons = [], []
    members, accounts = set(scope.positions), set(scope.accounts)

    def add(identity, stamp, sequence, amount, currency, *, include_start=False):
        if not (start < stamp <= end or (include_start and start == stamp)):
            return
        value = convert(amount, currency, target, stamp, observations)
        reason = '' if value is not None else f'{identity}: missing cash amount or dated FX'
        flows.append(ExternalFlow(identity, stamp, sequence, value, reason))
        if reason:
            reasons.append(reason)

    for c in replay_securities(journal.securities).cash:
        asset_inside = c.position_id in members
        cash_inside = c.account_id in accounts and not c.external
        if asset_inside == cash_inside:
            continue
        # Asset-only purchases are contributions; account-only payments out are withdrawals.
        amount = None if c.amount is None else -c.amount if asset_inside else c.amount
        add('security:'+c.id, c.date, c.sequence, amount, c.currency)
    for book in journal.securities:
        for e in selected_events(book):
            if e.kind in ('inventory_in', 'inventory_out') and book.position_id in members and start < e.date <= end:
                if e.flow_classification == 'external':
                    add(e.id, e.date, e.sequence,
                        None if e.valuation_amount is None else e.valuation_amount*(1 if e.kind == 'inventory_in' else -1),
                        e.valuation_currency)
                else:
                    reasons.append(f'{e.id}: inventory origin or purpose unresolved')
            if e.kind in ('transfer_in', 'transfer_out') and book.position_id in members and start < e.date <= end:
                price = lookup(observations, 'price', book.position_id, e.date) or lookup(observations, 'price', book.instrument_id, e.date)
                amount = e.amount if e.amount is not None else e.quantity*price.amount if price else None
                denomination = e.currency if e.amount is not None else price.currency if price else e.currency
                add(e.id, e.date, e.sequence, None if amount is None else amount*(1 if e.kind == 'transfer_in' else -1), denomination)
            if e.kind == 'swap' and start < e.date <= end:
                source_inside, dest_inside = book.position_id in members, e.other_position_id in members
                if source_inside != dest_inside:
                    add(e.id, e.date, e.sequence,
                        None if e.valuation_amount is None else e.valuation_amount*(-1 if source_inside else 1),
                        e.valuation_currency)
            if e.kind != 'transfer' or not start < e.date <= end:
                continue
            source_inside, dest_inside = book.position_id in members, e.other_position_id in members
            if source_inside == dest_inside:
                continue
            price = lookup(observations, 'price', book.position_id, e.date) or lookup(observations, 'price', book.instrument_id, e.date)
            if price is None:
                reasons.append(f'{e.id}: supply transfer-date market value')
                flows.append(ExternalFlow(e.id, e.date, e.sequence, None))
            else:
                add(e.id, e.date, e.sequence, e.quantity * price.amount * (-1 if source_inside else 1), price.currency)
    classes = dict(observations.classifications)
    for e in effective_events(journal):
        inside, other = e.account_id in accounts, e.other_account_id in accounts
        initial = (inside and account_starts_on(journal, e.account_id, start)
                   or other and account_starts_on(journal, e.other_account_id, start))
        if e.kind == 'cash_flow' and inside:
            add(e.id, e.date, e.sequence, e.amount, e.currency, include_start=initial)
        elif e.kind == 'cash_adjust' and inside:
            classification = classes.get(e.id, 'boundary')
            if classification == 'external':
                add(e.id, e.date, e.sequence, e.amount, e.currency, include_start=initial)
            elif (start < e.date <= end or initial and e.date == start) and (classification == 'boundary' or classification == 'income' and e.amount < 0 or classification == 'expense' and e.amount > 0):
                reasons.append(f'{e.id}: unresolved cash adjustment')
        elif e.kind in ('transfer', 'fx') and inside != (other or (e.kind == 'fx' and not e.other_account_id and inside)):
            if inside:
                add(e.id, e.date, e.sequence, -e.amount, e.currency, include_start=initial)
            elif other:
                add(e.id, e.date, e.sequence, e.other_amount if e.kind == 'fx' else e.amount,
                    e.other_currency if e.kind == 'fx' else e.currency, include_start=initial)
    return tuple(sorted(flows, key=lambda f: (f.date, f.sequence, f.id))), tuple(dict.fromkeys(reasons))


def xirr(opening, ending, flows, start, end):
    """Unique conventional-flow XIRR; do not select an arbitrary multiple root."""
    from scipy.optimize import brentq
    days = (date.fromisoformat(end)-date.fromisoformat(start)).days
    if days <= 0 or opening < 0 or ending < 0:
        return None, 'Annualization needs positive duration and nonnegative endpoint equity'
    amounts = {start: -opening}
    for flow in flows:
        amounts[flow.date] = amounts.get(flow.date, ZERO) - flow.amount
    amounts[end] = amounts.get(end, ZERO) + ending
    values = [(stamp, float(value)) for stamp, value in sorted(amounts.items()) if value]
    signs = [1 if amount > 0 else -1 for _, amount in values]
    if not signs or signs[0] != -1 or signs[-1] != 1 or sum(a != b for a, b in zip(signs, signs[1:])) != 1:
        return None, 'A unique XIRR cannot be established for these cash flows'
    times = [(date.fromisoformat(stamp)-date.fromisoformat(start)).days/365 for stamp, _ in values]
    amounts = [amount for _, amount in values]
    scale = max(abs(a) for a in amounts)
    def residual(log_rate):
        exponents = [-t*log_rate for t in times]
        shift = max(exponents)
        return math.fsum((a/scale)*math.exp(exponent-shift) for a, exponent in zip(amounts, exponents))
    try:
        root = brentq(residual, -500, 500, xtol=1e-13)
        result = math.expm1(root)
        if not math.isfinite(result) or result <= -1 or abs(residual(root)) > 1e-8:
            raise ValueError
        return result, ''
    except (ValueError, OverflowError):
        return None, 'No numerically verified XIRR solution in the supported range'


def time_weighted(opening, ending, flows, observations, scope, target):
    ordered = [(flow.date, flow.sequence) for flow in flows if flow.amount]
    if len(ordered) != len(set(ordered)):
        return None, 'Assign distinct ordering to same-day external flows before calculating exact time-weighted returns'
    previous, factor = opening, Decimal(1)
    invested = opening > 0
    if previous < 0 or ending < 0:
        return None, 'Time-weighted return is unavailable across negative equity'
    for flow in flows:
        if flow.amount == 0:
            continue
        before = lookup(observations, 'value', scope.key, flow.date, side='before', boundary=flow.id, currency=target)
        after = lookup(observations, 'value', scope.key, flow.date, side='after', boundary=flow.id, currency=target)
        if before is None or after is None:
            return None, f'{flow.id}: supply exact before/after flow valuations'
        if before.amount < 0 or after.amount < 0 or abs(after.amount-before.amount-flow.amount) > Decimal('.01'):
            return None, f'{flow.id}: flow valuations do not reconcile with nonnegative equity'
        if previous:
            factor *= before.amount/previous
        elif before.amount:
            return None, f'{flow.id}: value appeared without recorded funding'
        previous = after.amount
        invested |= previous > 0
    if not invested or (not previous and ending):
        return None, 'Time-weighted return needs a funded interval with supported boundaries'
    result = float(factor * (ending/previous if previous else 1)-1)
    return (result, '') if math.isfinite(result) else (None, 'Return exceeds supported numeric range')


def known_negative_equity_dates(journal, scope, observations, start, end, target='EUR', *, valuations=None):
    """Known total equity at recorded dates, not individual negative balances.

    Check event-day closes as well as supplied valuations and price/FX dates.
    Missing valuations stay unknown; never fill them to establish eligibility.
    Callers rendering many prefixes can reuse these dates and existing values.
    """
    from portfolio_app.history_observations import active_observations, index_observations
    observations = index_observations(observations)
    active = active_observations(observations)
    owners = {p.id: p.account_id for p in journal.positions}
    subjects = set(scope.positions) | {b.instrument_id for b in journal.securities if b.position_id in scope.positions}
    subjects |= {p.id for p in journal.positions if p.account_id in scope.accounts}
    subjects |= {p.contract_id for p in journal.positions if p.account_id in scope.accounts}
    dates = {e.date for b in journal.securities for e in selected_events(b) if affected_positions(b, e).intersection(scope.positions)}
    dates |= {e.date for e in effective_events(journal)
              if e.account_id in scope.accounts or e.other_account_id in scope.accounts
              or owners.get(e.position_id) in scope.accounts}
    dates |= {o.date for o in active if o.kind == 'fx' or o.kind == 'price' and o.subject in subjects
              or o.kind == 'value' and o.subject == scope.key}
    negative = {o.date for o in active if o.kind == 'value' and o.subject == scope.key
                and o.currency == target and start <= o.date <= end and o.amount < 0}
    for stamp in sorted(dates):
        if not start <= stamp <= end or stamp in negative:
            continue
        value = (valuations[stamp] if valuations is not None and stamp in valuations else
                 value_at(journal, scope, observations, stamp, target))
        if value.amount is not None and value.amount < 0:
            negative.add(stamp)
    return tuple(sorted(negative))


def calculate_history(journal, scope, observations, start, end, target='EUR', *, returns=True, negative_dates=None):
    days = (date.fromisoformat(end)-date.fromisoformat(start)).days
    if days < 0:
        from portfolio_app.holdings import DataError
        raise DataError('History end must not precede its start.')
    opening, ending = opening_value_at(journal, scope, observations, start, target), value_at(journal, scope, observations, end, target)
    coverage = coverage_reasons(journal, scope, observations, start, end)
    flows, missing_flows = external_flows(journal, scope, observations, start, end, target)
    flow_reasons = tuple(dict.fromkeys((*coverage, *missing_flows)))
    net = sum((f.amount for f in flows), ZERO) if not flow_reasons else None
    capital = capital_at_start(journal, scope, observations, start, target)
    invested = capital.amount + net if capital.amount is not None and net is not None else None
    gain = ending.amount - opening.amount - net if ending.amount is not None and opening.amount is not None and net is not None else None
    total_gain = ending.amount - invested if ending.amount is not None and invested is not None else None
    opening_gain = opening.amount - capital.amount if opening.amount is not None and capital.amount is not None else None
    reasons = tuple(dict.fromkeys((*opening.reasons, *ending.reasons, *flow_reasons)))
    mwr = twr = None
    mwr_reasons = twr_reasons = reasons
    if gain is not None and returns:
        # Empty inception and liquidation are legitimate. Negative equity remains
        # outside this return contract; zeroes are handled by each method.
        if negative_dates is None:
            negative_dates = known_negative_equity_dates(journal, scope, observations, start, end, target)
        negative = any(start <= stamp <= end for stamp in negative_dates)
        if negative or opening.amount < 0 or ending.amount < 0:
            mwr_reasons = twr_reasons = ('Percentage return is unavailable across negative equity',)
        else:
            mwr, reason = xirr(opening.amount, ending.amount, flows, start, end)
            mwr_reasons = (reason,) if reason else ()
            twr, reason = time_weighted(opening.amount, ending.amount, flows, observations, scope, target)
            twr_reasons = (reason,) if reason else ()
    return PersonalPerformance(opening.amount, ending.amount, net, invested, gain, mwr, twr, days,
        tuple(dict.fromkeys((*opening.reasons, *ending.reasons))), flow_reasons, mwr_reasons, twr_reasons,
        capital.amount, opening_gain, total_gain, capital.reasons)
