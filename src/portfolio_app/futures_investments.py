"""Persistent futures investment identity and capital presentation.

An investment owns a declared portion of account cash plus its recorded net
trading cash flows and open claims. These are attributions of existing wealth,
not deposits, reservations, loans or another futures accounting ledger.
"""
from collections import defaultdict
from dataclasses import replace
from decimal import Decimal

import pandas as pd

from portfolio_app.account_journal import InvestmentSpec, effective_events, replay
from portfolio_app.derivative_types import ZERO
from portfolio_app.holdings import DataError

ATTRIBUTED_KINDS = frozenset(('trade', 'settlement', 'final_settlement', 'fee', 'tax', 'interest'))


def roll_position_ids(journal, identity):
    """Existing explicit roll links only; never infer ownership from product names."""
    groups = defaultdict(set)
    for event in effective_events(journal):
        if event.kind == 'trade' and event.group_id and event.position_id:
            groups[event.group_id].add(event.position_id)
    result = {identity}
    while True:
        expanded = result.union(*(members for members in groups.values() if members & result))
        if expanded == result:
            return tuple(p.id for p in journal.positions if p.id in result)
        result = expanded


def _posted(state, members):
    amounts = defaultdict(lambda: ZERO)
    for movement in state.cash_movements:
        if movement.position_id in members and movement.kind in ATTRIBUTED_KINDS:
            amounts[movement.currency] += movement.amount
    return amounts


def assign_investment(journal, investment: InvestmentSpec, position_ids):
    """Explicit creation/allocation edit, validating ownership and available cash.

    Allocation changes do not create cash or P&L. Existing reference capital is
    immutable; correcting journal transactions recomputes attributed gains.
    """
    members = set(position_ids)
    known = {p.id: p for p in journal.positions}
    if not members or not members <= known.keys():
        raise DataError('Choose existing contract positions for the investment.')
    if any(known[p].investment_id not in ('', investment.id) for p in members):
        raise DataError('A contract position already belongs to another investment.')
    existing = next((i for i in journal.investments if i.id == investment.id), None)
    if existing and (existing.account_id, existing.currency, existing.reference_capital) != (
            investment.account_id, investment.currency, investment.reference_capital):
        raise DataError('Keep the original account, currency and initial reference capital.')
    if existing and {p.id for p in journal.positions if p.investment_id == investment.id} != members:
        raise DataError('An existing investment must retain its recorded contract history.')
    investments = tuple(investment if i.id == investment.id else i for i in journal.investments)
    if existing is None:
        investments += (investment,)
    candidate = replace(journal, investments=investments,
        positions=tuple(replace(p, investment_id=investment.id) if p.id in members else p for p in journal.positions))
    state = replay(candidate)
    # Check only new/increased attribution. A withdrawal from the real account
    # can leave an allocation shortfall; naming an investment must still work.
    if existing is None or investment.allocated_capital > existing.allocated_capital:
        key = investment.account_id, investment.currency
        attributed = ZERO
        for item in candidate.investments:
            if item.account_id != investment.account_id:
                continue
            owned = {p.id for p in candidate.positions if p.investment_id == item.id}
            attributed += _posted(state, owned)[investment.currency]
            if item.currency == investment.currency:
                attributed += item.allocated_capital
        if attributed > state.cash.get(key, ZERO):
            raise DataError('Allocated capital exceeds unallocated account cash after recorded investment cash flows. Record funding or reduce the allocation.')
    return candidate


def investment_positions(valued, journal):
    """Display projection only. Keep exact-contract input for exposure and risk.

    Convert every attributed native amount at current reporting FX. This reports
    recorded trading P&L, including fees/taxes, not a historical FX return on
    allocated capital. Missing FX/marks propagate instead of inventing zeroes.
    """
    if not journal.investments:
        return valued.copy()
    state = replay(journal)
    rates = valued.attrs.get('account_fx_rates', {})
    indexed = valued.set_index('position_id', drop=False)
    links = {(link.account_id, link.currency): link.position_id for link in journal.cash_links}
    display = valued.copy()
    removed, rows, membership, source_map, cash_map = set(), [], {}, {}, {}
    cash_changes = defaultdict(lambda: ZERO)

    def conversion(amount, currency):
        if amount == 0:
            return 0.
        rate = rates.get(currency)
        return float(amount) * float(rate) if rate is not None else float('nan')

    for investment in journal.investments:
        members = tuple(p.id for p in journal.positions if p.investment_id == investment.id)
        if not members:
            continue
        membership[investment.id] = members
        cash = _posted(state, set(members))
        paid = dict(cash)
        cash[investment.currency] += investment.allocated_capital
        claims = [p for p in state.pending.values() if p.position_id in members and p.kind in ATTRIBUTED_KINDS]
        extra_pending = [f'account-pending:{p.id}' for p in claims if p.currency != investment.currency]
        source_ids = (*members, *extra_pending)
        removed.update(source_ids)
        source_map.update({identity: investment.id for identity in source_ids})
        cash_map[investment.id] = {}
        for currency, amount in cash.items():
            if not amount:
                continue
            identity = links.get((investment.account_id, currency), f'account-cash:{investment.account_id}:{currency}')
            if identity not in indexed.index:
                raise DataError('An investment cash attribution has no recorded account balance.')
            cash_changes[identity] += amount
            cash_map[investment.id][identity] = {'native': float(amount), 'reporting': conversion(amount, currency)}
        # Contract rows already contain native pending claims once. Cross-currency
        # claims are independent raw rows and join the investment here.
        claim_value = sum(float(indexed.loc[identity, 'current_value_reporting']) for identity in source_ids)
        posted_gain = sum(conversion(amount, currency) for currency, amount in paid.items())
        gain = claim_value + posted_gain
        principal = conversion(investment.allocated_capital, investment.currency)
        reference = conversion(investment.reference_capital, investment.currency)
        value = principal + gain
        rate = rates.get(investment.currency)
        template = indexed.loc[members[-1]].to_dict()
        complete = all(state.positions[p].history_complete for p in members)
        note = ('Recorded net futures P&L including settled and open gains, fees and entered taxes; '
                'return uses fixed initial allocated capital. Allocation changes are not gains. '
                'Amounts use current FX; historical FX gains on capital are not included.')
        if not complete:
            note += ' Since recorded snapshot only; earlier performance is unknown.'
        if not investment.reference_capital:
            note += ' Percentage return is unavailable without initial reference capital.'
        notional = sum(float(indexed.loc[p, 'signed_notional_reporting']) for p in members)
        template.update(position_id=investment.id, id=investment.id, name=investment.name,
            short_name='', investment_id=investment.id, instrument_type='future', shares=1.,
            contracts=sum(state.positions[p].quantity for p in members),
            current_value_reporting=value,
            current_value_native=value / rate if rate else float('nan'),
            current_price=value / rate if rate else float('nan'),
            cost_basis=float(investment.reference_capital), cost_basis_reporting=reference,
            unrealized_gain=gain / rate if rate else float('nan'), unrealized_gain_reporting=gain,
            return_pct=100 * gain / reference if reference > 0 else float('nan'),
            native_return_pct=100 * gain / reference if reference > 0 else float('nan'),
            signed_notional_reporting=notional, history_complete=complete,
            reference_capital_native=float(investment.reference_capital),
            allocated_capital_native=float(investment.allocated_capital),
            net_funding_native=float(investment.allocated_capital-investment.reference_capital),
            investment_gain_native=gain / rate if rate else float('nan'),
            investment_cash_reporting=sum(v['reporting'] for v in cash_map[investment.id].values()),
            performance_note=note, valuation_note='Allocated capital plus recorded net futures P&L; attributed account cash is counted once.',
            contract_id='', mark_price=float('nan'), unit_exposure_reporting=float('nan'))
        rows.append(template)
    for identity, amount in cash_changes.items():
        mask = display.position_id.eq(identity)
        original = indexed.loc[identity]
        currency = original.quote_currency
        remainder = Decimal(str(original.current_value_native)) - amount
        display.loc[mask, 'current_value_native'] = float(remainder)
        display.loc[mask, 'current_price'] = float(remainder)
        display.loc[mask, 'current_value_reporting'] = conversion(remainder, currency)
        display.loc[mask, 'name'] = f'{original.account} · unallocated {currency} cash'
        # This is a signed allocation remainder, not another debt or cash asset.
        display.loc[mask, 'instrument_type'] = 'cash'
        if remainder == 0:
            removed.add(identity)
        display.loc[mask, 'valuation_note'] = ('Account cash after investment attribution. A negative remainder indicates an allocation shortfall; it is not an additional loan.')
    display = display.loc[~display.position_id.isin(removed)].copy()
    if rows:
        display = pd.concat([display, pd.DataFrame(rows)], ignore_index=True)
    display.attrs = dict(valued.attrs, investment_members=membership,
        investment_source_map=source_map, investment_cash=cash_map)
    return display
