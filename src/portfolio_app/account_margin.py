"""Declared collateral coverage, independent of broker buying-power models.

Security reservations are share/unit quantities. Cash reservations are native
amounts. Neither is a new asset. Haircuts affect requirement coverage only;
foreign collateral is never automatically converted to settlement cash.
"""
from dataclasses import dataclass, replace
from decimal import Decimal

from portfolio_app.account_journal import Event, HoldingLink, append_event, replay
from portfolio_app.derivative_types import CURRENCIES, ZERO, currency, decimal
from portfolio_app.holdings import DataError


def _holding_rows(holdings):
    if 'position_id' not in holdings or holdings.position_id.duplicated().any():
        raise DataError('Collateral requires holdings with unique position IDs.')
    return {row['position_id']: row for row in holdings.to_dict('records')}


def bind_holding(journal, holdings, position_id, account_id):
    rows = _holding_rows(holdings)
    if position_id not in rows or account_id not in {a.id for a in journal.accounts}:
        raise DataError('Choose an existing holding and account.')
    existing = next((link for link in journal.holding_links if link.position_id == position_id), None)
    if existing:
        if existing.account_id != account_id:
            raise DataError('This holding already belongs to another account.')
        return journal
    cash_owner = next((link.account_id for link in journal.cash_links if link.position_id == position_id), None)
    if cash_owner and cash_owner != account_id:
        raise DataError('This cash holding already belongs to another account.')
    return replace(journal, holding_links=(*journal.holding_links, HoldingLink(position_id, account_id)))


def validate_reservations(journal, holdings):
    """Validate ownership and aggregate reserved shares at every journal save."""
    rows = _holding_rows(holdings)
    state = replay(journal)
    owners = {link.position_id: link.account_id for link in journal.holding_links}
    cash_ids = {link.position_id for link in journal.cash_links}
    reserved = {}
    for item in state.reservations.values():
        if not item.holding_id:
            continue
        row = rows.get(item.holding_id)
        if row is None or owners.get(item.holding_id) != item.account_id:
            raise DataError('Security collateral must reference an existing holding owned by this account.')
        if item.holding_id in cash_ids or row.get('instrument_type') == 'cash':
            raise DataError('Cash must be reserved from its canonical account balance, not as a security.')
        reserved[item.holding_id] = reserved.get(item.holding_id, ZERO)+item.amount
    for key, quantity in reserved.items():
        available = decimal(rows[key]['shares'], 'Owned quantity')
        if available < 0 or quantity > available:
            raise DataError('Reserved security quantity exceeds the shares/units currently owned.')


def reserve_collateral(journal, event, holdings):
    if event.kind not in ('reserve', 'release'):
        raise DataError('Collateral command needs a reserve or release event.')
    result = append_event(journal, event)
    validate_reservations(result, holdings)
    return result


def adopt_liability(journal, liability, account_id, day, event_id):
    """Explicit EUR cutover; retain original settings and mark their consumption."""
    if liability.id in journal.adopted_liability_ids:
        raise DataError('This legacy liability has already been adopted.')
    if liability.balance_eur is None:
        raise DataError('Enter the existing liability balance before adopting it.')
    event = Event(event_id, day, 'debt_open', account_id=account_id, currency='EUR',
                  amount=liability.balance_eur, reference='legacy:'+liability.id,
                  source='legacy leverage settings', note='Existing liability adopted; no cash movement.')
    result = append_event(journal, event)
    return replace(result, adopted_liability_ids=(*result.adopted_liability_ids, liability.id))


def conversion_rate(rates, source, target, reporting_currency):
    """Accept native-to-reporting scalars or explicit (source,target) observations.

    A supplied mapping is a valuation snapshot, not a provider. Missing paths
    return None; invalid or contradictory rates reject the calculation.
    """
    currency(source); currency(target); currency(reporting_currency)
    if source == target:
        return Decimal(1)
    edges = {}
    for key, raw in (rates or {}).items():
        if isinstance(key, str):
            pair = key, reporting_currency
        elif isinstance(key, tuple) and len(key) == 2:
            pair = key
        else:
            raise DataError('FX keys must be currencies or currency pairs.')
        for value in pair:
            currency(value)
        value = raw.get('rate') if isinstance(raw, dict) else raw
        if value is None:
            continue
        value = decimal(value, 'FX rate')
        if value <= 0 or pair[0] == pair[1] and value != 1:
            raise DataError('FX rates must be positive and same-currency rates must equal one.')
        if pair in edges and abs(edges[pair]-value) > Decimal('1e-10')*max(value, Decimal(1)):
            raise DataError('Conflicting reciprocal FX observations.')
        edges[pair] = value
        reverse = pair[1], pair[0]
        inverse = 1/value
        if reverse in edges and abs(edges[reverse]-inverse) > Decimal('1e-10')*max(inverse, Decimal(1)):
            raise DataError('Conflicting reciprocal FX observations.')
        edges[reverse] = inverse
    direct = edges.get((source, target))
    indirect = []
    for middle in CURRENCIES:
        if middle in (source, target):
            continue
        if (source, middle) in edges and (middle, target) in edges:
            indirect.append(edges[source, middle]*edges[middle, target])
    candidates = ([direct] if direct is not None else [])+indirect
    if not candidates:
        return None
    if any(abs(candidate-candidates[0]) > Decimal('1e-8')*max(candidates[0], Decimal(1)) for candidate in candidates):
        raise DataError('Conflicting FX cross rates; review the observations.')
    return candidates[0]


@dataclass(frozen=True)
class CollateralRow:
    id: str
    holding_id: str
    reserved: Decimal
    haircut: Decimal
    owned_value: Decimal | None
    eligible_value: Decimal | None
    currency: str
    issue: str = ''


@dataclass(frozen=True)
class MarginCoverage:
    account_id: str
    currency: str
    eligible_collateral: Decimal | None
    initial_requirement: Decimal | None
    maintenance_requirement: Decimal | None
    settled_cash: Decimal
    unreserved_cash: Decimal
    pending_cash: Decimal
    loans: Decimal
    collateral: tuple[CollateralRow, ...]
    issues: tuple[str, ...]

    @property
    def initial_headroom(self):
        if self.eligible_collateral is None or self.initial_requirement is None:
            return None
        return self.eligible_collateral-self.initial_requirement

    @property
    def maintenance_headroom(self):
        if self.eligible_collateral is None or self.maintenance_requirement is None:
            return None
        return self.eligible_collateral-self.maintenance_requirement


def margin_coverage(journal, holdings, valued, account_id, denomination, *, reporting_currency='EUR', fx_rates=None):
    """Entered requirement coverage; loans and liquidity remain separate facts.

    Current asset declines cap eligibility pro rata across reservations. Missing
    valuation or FX suppresses the aggregate rather than counting an unknown as
    zero. Bank/transit cash is never included as pledged broker collateral.
    """
    currency(denomination); currency(reporting_currency)
    if account_id not in {account.id for account in journal.accounts}:
        raise DataError('Unknown margin account.')
    state = replay(journal)
    holdings_rows, valued_rows = _holding_rows(holdings), _holding_rows(valued)
    owners = {link.position_id: link.account_id for link in journal.holding_links}
    reservations = [item for item in state.reservations.values() if item.account_id == account_id]
    totals = {}
    for item in reservations:
        key = ('holding', item.holding_id) if item.holding_id else ('cash', item.currency)
        totals[key] = totals.get(key, ZERO)+item.amount
    rows, issues = [], []
    for item in reservations:
        issue = ''
        value = None
        if item.holding_id:
            held, marked = holdings_rows.get(item.holding_id), valued_rows.get(item.holding_id)
            if held is None or owners.get(item.holding_id) != account_id:
                issue = 'Missing holding or incompatible account ownership.'
            elif marked is None:
                issue = 'Missing collateral valuation.'
            else:
                try:
                    owned = decimal(held['shares'])
                    current = decimal(marked['current_value_reporting'])
                    if owned < 0 or current < 0:
                        raise DataError('Invalid holding valuation.')
                    total = totals['holding', item.holding_id]
                    backing = min(Decimal(1), owned/total) if total else ZERO
                    rate = conversion_rate(fx_rates, reporting_currency, denomination, reporting_currency)
                    if rate is None:
                        issue = 'Missing FX for collateral valuation.'
                    else:
                        value = current*(item.amount/owned)*backing*rate if owned else ZERO
                        if total > owned:
                            issue = 'Reserved quantity exceeds current ownership; eligibility capped.'
                except (DataError, KeyError, ZeroDivisionError):
                    issue = 'Missing or invalid collateral valuation.'
        else:
            balance = max(ZERO, state.cash.get((account_id, item.currency), ZERO))
            total = totals['cash', item.currency]
            backing = min(Decimal(1), balance/total) if total else ZERO
            rate = conversion_rate(fx_rates, item.currency, denomination, reporting_currency)
            if (account_id, item.currency) in state.unknown_cash:
                issue = f'{item.currency} cash is incomplete; supply missing transaction amounts.'
            elif rate is None:
                issue = 'Missing FX for cash collateral.'
            else:
                value = item.amount*backing*rate
                if total > balance:
                    issue = 'Reserved cash exceeds current cash; eligibility capped.'
        if issue:
            issues.append(issue)
        rows.append(CollateralRow(item.id, item.holding_id, item.amount, item.haircut,
                                  value, value*(1-item.haircut) if value is not None else None, denomination, issue))
    collateral = sum((row.eligible_value for row in rows), ZERO) if all(row.eligible_value is not None for row in rows) else None
    requirements = [r for r in journal.margin_requirements if r.account_id == account_id]
    initial = maintenance = ZERO if requirements else None
    for requirement in requirements:
        rate = conversion_rate(fx_rates, requirement.currency, denomination, reporting_currency)
        if rate is None:
            initial = maintenance = None
            issues.append('Missing FX for an entered margin requirement.')
            break
        initial += requirement.initial*rate
        maintenance += requirement.maintenance*rate
    return MarginCoverage(account_id, denomination, collateral, initial, maintenance,
        state.cash.get((account_id, denomination), ZERO), state.available_cash(account_id, denomination),
        sum((item.amount for item in state.pending.values() if item.account_id == account_id and item.currency == denomination), ZERO),
        sum((amount for (owner, unit, _), amount in state.loans.items() if owner == account_id and unit == denomination), ZERO),
        tuple(rows), tuple(dict.fromkeys(issues)))
