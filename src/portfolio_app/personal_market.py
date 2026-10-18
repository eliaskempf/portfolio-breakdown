"""Opt-in market observations for personal history; never reconstruct trades."""
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from portfolio_app.holdings import DataError
from portfolio_app.history_observations import Observation, active_observations, add_observation


def as_traded_prices(frame):
    """Undo the provider's split-only Close normalization using its full actions.

    Fetch through the present adjustment anchor, even for a past viewing window.
    Dividend adjustments and provider repair are deliberately disabled.
    """
    if 'Close' not in frame or 'Stock Splits' not in frame:
        raise DataError('Price history lacks its split-adjustment evidence.')
    result, factor = [], Decimal(1)
    for stamp, row in frame.sort_index(ascending=False).iterrows():
        close = Decimal(str(row['Close']))
        split = Decimal(str(row['Stock Splits']))
        if split.is_nan():
            raise DataError('Unknown split adjustment; supply dated prices manually.')
        if close.is_finite() and close > 0:
            result.append((stamp.date().isoformat(), close * factor))
        if split and split != 1:
            if not split.is_finite() or split <= 0:
                raise DataError('Invalid split adjustment.')
            factor *= split
    return tuple(reversed(result))


def fetch_security_prices(ticker, position_id, start, end, *, factory=None):
    if factory is None:
        import yfinance as yf
        factory = yf.Ticker
    instrument = factory(ticker)
    frame = instrument.history(start=start, end=(date.today()+timedelta(days=1)).isoformat(),
        auto_adjust=False, back_adjust=False, actions=True, repair=False, timeout=10, raise_errors=True)
    from portfolio_app.currencies import quote_unit
    currency, multiplier = quote_unit(instrument.history_metadata.get('currency', ''))
    return tuple(Observation(uuid4().hex, 'price', position_id, stamp, amount*Decimal(str(multiplier)), currency,
        source='Yahoo Close, split normalization reversed') for stamp, amount in as_traded_prices(frame) if stamp <= end)


def fetch_fx(source, target, start, end, *, factory=None):
    if source == target:
        return ()
    if factory is None:
        import yfinance as yf
        factory = yf.Ticker
    frame = factory(f'{source}{target}=X').history(start=start, end=(date.fromisoformat(end)+timedelta(days=1)).isoformat(),
        auto_adjust=False, actions=False, repair=False, timeout=10, raise_errors=True)
    result = []
    for stamp, value in frame.Close.items():
        amount = Decimal(str(value))
        if amount.is_finite() and amount > 0:
            result.append(Observation(uuid4().hex, 'fx', f'{source}/{target}', stamp.date().isoformat(),
                amount, source, target=target, source='Yahoo historical FX'))
    return tuple(result)


def merge_download(store, observations):
    active = {o.key: o for o in active_observations(store)}
    from dataclasses import replace
    for observation in observations:
        previous = active.get(observation.key)
        if previous and (previous.source == 'manual' or previous.amount == observation.amount):
            continue
        if previous:
            observation = replace(observation, replaces=previous.id)
        store = add_observation(store, observation)
        active[observation.key] = observation
    return store


def download_scope_observations(directory, journal, scope, store, start, end, target):
    """Fetch evidence through the requested end, independently of valued endpoints.

    Currencies come from saved events and observed prices, never a guessed default.
    Existing manual observations win; downloads do not certify history coverage.
    """
    from portfolio_app.positions import read_snapshot
    from portfolio_app.security_history import selected_events
    from portfolio_app.settlement_archive import SettlementArchive
    holdings = read_snapshot(directory / 'holdings.csv').holdings.set_index('position_id')
    updated, errors, currencies = store, [], set()
    identities = set(scope.positions) | {b.position_id for b in journal.securities if b.account_id in scope.accounts}
    for identity in sorted(identities):
        if identity not in holdings.index or not holdings.loc[identity].ticker:
            continue
        try:
            prices = fetch_security_prices(holdings.loc[identity].ticker, identity, start, end)
            if not prices:
                raise DataError('No historical prices returned.')
            updated = merge_download(updated, prices)
        except Exception as exc:
            errors.append(f'{identity}: {type(exc).__name__}; supply observations manually')
    subjects = set(scope.positions)
    for book in journal.securities:
        if book.position_id in scope.positions or book.account_id in scope.accounts:
            subjects.update((book.position_id, book.instrument_id))
            for event in selected_events(book):
                currencies.add(event.currency)
                currencies.update(c.currency for c in event.charges)
                currencies.update(c['currency'] for c in event.costs)
                currencies.update(c['currency'] for lot in event.lots for c in lot.costs)
    for event in journal.events:
        if event.account_id in scope.accounts or event.other_account_id in scope.accounts:
            currencies.update((event.currency, event.other_currency))
    for contract in journal.contracts:
        if not any(p.account_id in scope.accounts and p.contract_id == contract.id for p in journal.positions):
            continue
        subjects.add(contract.id)
        currencies.add(contract.currency)
        values = [Observation(uuid4().hex, 'price', contract.id, stamp, value.price, contract.currency,
            source='Accepted settlement archive') for stamp, value in SettlementArchive(directory).entries(contract).items()
            if start <= stamp <= end]
        updated = merge_download(updated, values)
    currencies.update(o.currency for o in active_observations(updated)
                      if o.subject in subjects or o.subject == scope.key)
    for currency in sorted(currencies - {'', target}):
        try:
            rates = fetch_fx(currency, target, start, end)
            if not rates:
                raise DataError('No historical FX returned.')
            updated = merge_download(updated, rates)
        except Exception as exc:
            errors.append(f'{currency}/{target}: {type(exc).__name__}; supply observations manually')
    return updated, errors
