"""Prepare tax candidates and results without Streamlit or portfolio mutations."""
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json

from portfolio_app.tax_history import project_holdings, depot_key, TaxHolding
from portfolio_app.tax_evidence import prepare_inputs
from portfolio_app.vorabpauschale import TaxLot, calculate_fund, estimate_tax, ZERO


@dataclass(frozen=True)
class Candidate:
    instrument: str
    depot: str
    name: str
    account: str
    ticker: str
    holding: TaxHolding
    is_fund: bool
    current_price: Decimal | None = None
    quote_date: str = ''

    @property
    def key(self):
        return sha256(json.dumps((self.instrument, self.depot)).encode()).hexdigest()

    @property
    def label(self):
        return f'{self.name} · {self.account}'


def candidates(holdings, journal, as_of, funds=(), valued=None):
    """Include zero/closed positions. Current-value filters never define tax scope."""
    from portfolio_app.instruments import CATALOG_BY_TICKER
    rows = holdings.to_dict('records')
    identities = {r['id']: r.get('isin') or r['id'] for r in rows}
    by_position = {r['position_id']: r for r in rows}
    books = tuple(journal.securities) if journal else ()
    account_names = {a.id: a.name for a in journal.accounts} if journal else {}
    # An explicit custody identity wins over a display account. Keep distinct
    # account IDs apart even if their readable broker names happen to match.
    depots = {b.position_id: (b.custody_id or b.account_id or
              ('label:'+by_position[b.position_id]['account'] if b.position_id in by_position and by_position[b.position_id].get('account') else depot_key(b))) for b in books}
    projected = project_holdings(books, as_of, identities, depots)
    grouped = {(h.instrument, h.depot): h for h in projected}
    owners = {b.position_id: b for b in books}
    for row in rows:
        key = identities[row['id']], depots.get(row['position_id'], 'label:'+row['account'] if row.get('account') else 'position:'+row['position_id'])
        if key not in grouped:
            grouped[key] = TaxHolding(*key, (row['position_id'],), (), ('Applied ownership history missing',))
        elif row['position_id'] not in grouped[key].positions:
            from dataclasses import replace
            old = grouped[key]
            grouped[key] = replace(old, positions=(*old.positions, row['position_id']),
                                   issues=(*old.issues, 'Applied ownership history missing'))
    known_isins = {f.isin for f in funds}
    values = {} if valued is None else {r['position_id']: r for r in valued.to_dict('records')}
    result = []
    for (instrument, depot), holding in grouped.items():
        members = [by_position[p] for p in holding.positions if p in by_position]
        row = members[0] if members else {}
        listing = CATALOG_BY_TICKER.get(row.get('ticker', ''))
        kind = row.get('instrument_type', '')
        is_fund = kind == 'etf' or instrument in known_isins or bool(listing and listing.kind == 'ETF')
        if kind in ('crypto', 'etc', 'physical', 'future'):
            continue
        book = next((owners[p] for p in holding.positions if p in owners), None)
        account = account_names.get(book.account_id, '') if book else ''
        account = account or row.get('account') or 'Unassigned account'
        current = values.get(row.get('position_id'), {})
        price = current.get('current_price')
        current_price = None
        if current.get('quote_currency') == 'EUR' and price is not None:
            try:
                numeric = Decimal(str(price))
                if numeric.is_finite() and numeric > 0:
                    current_price = numeric
            except ArithmeticError:
                pass
        result.append(Candidate(instrument, depot, row.get('name') or 'Unlinked investment fund', account,
                                row.get('ticker', ''), holding, is_fund, current_price,
                                str(current.get('price_observed_at', ''))[:10]))
    return tuple(result)


def index_observations(observations):
    from collections import defaultdict
    from portfolio_app.history_observations import active_observations
    result = defaultdict(list)
    for observation in active_observations(observations):
        if observation.kind == 'price' and observation.currency == 'EUR' and observation.side == 'close':
            result[observation.subject].append(observation)
    return dict(result)


def observation_market(candidate, observations, year, as_of):
    """Reuse EUR dated closes, without treating observed absence as distributions."""
    indexed = observations if isinstance(observations, dict) else index_observations(observations)
    prices = sorted((o for p in candidate.holding.positions for o in indexed.get(p, ())
                     if f'{year}-01-01' <= o.date <= as_of), key=lambda o: o.date)
    if not prices:
        return {}
    def normalized(observation):
        amount = observation.amount
        for day, ratio in candidate.holding.splits:
            if day > observation.date:
                amount /= ratio
        return str(amount)
    first, last = prices[0], prices[-1]
    from datetime import date
    return {'start_price': normalized(first) if first.date <= f'{year}-01-10' else None,
            'end_price': normalized(last) if (date.fromisoformat(as_of)-date.fromisoformat(last.date)).days <= 10 else None,
            'start_date': first.date, 'end_date': last.date,
            'source': 'Recorded EUR price observations', 'splits': [(d, str(r)) for d, r in candidate.holding.splits]}


def result_for(candidate, year, manual, market, *, forecast=False, override=None, fingerprint=''):
    inputs = prepare_inputs(year, manual, market, forecast=forecast,
                            current_price=candidate.current_price if forecast else None)
    issues = list(candidate.holding.issues)
    lots = candidate.holding.lots
    if override:
        if override.get('fingerprint') != fingerprint:
            issues.append('Reviewed remaining lots are stale; review changed history')
        elif not any('Unsupported' in s or 'tax review' in s or 'split' in s.lower() for s in issues):
            lots = tuple(TaxLot(Decimal(l['quantity']), l.get('acquired')) for l in override['lots'])
            issues = []
    if not candidate.is_fund and not manual.get('is_fund'):
        issues.append('Investment-fund status needs review')
    provider_splits = tuple((day, Decimal(ratio)) for day, ratio in market.get('splits', ()))
    relevant = lambda events: tuple((day, ratio) for day, ratio in events
        if f'{year}-01-01' <= day <= f'{year}-12-31'
        and any(l.quantity and (not l.acquired or l.acquired < day) for l in lots))
    if relevant(provider_splits) != relevant(candidate.holding.splits):
        # Manual EUR prices/distributions may use the reviewed ending share unit.
        if not manual.get('split_units_reviewed'):
            issues.append('Reconcile split quantities and per-share input units')
    return inputs, calculate_fund(inputs, lots, ownership_issues=issues), lots


def combined(results, allowance=ZERO, church_rate=ZERO):
    available = [r for r in results if r.gross is not None]
    gross = sum((r.gross for r in available), ZERO)
    tax = estimate_tax(sum((r.taxable for r in available), ZERO), allowance, church_rate)
    return gross, tax, len(available), len(results)
