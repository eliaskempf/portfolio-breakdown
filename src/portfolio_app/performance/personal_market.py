"""Opt-in market observations for personal history; never reconstruct trades."""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from portfolio_app.portfolio.holdings import DataError
from portfolio_app.performance.history_observations import Observation, active_observations


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
    from portfolio_app.market.currencies import quote_unit
    metadata = instrument.history_metadata
    currency, multiplier = quote_unit(metadata.get('currency', ''))
    from portfolio_app.performance.history_valuation import listing_calendar, exchange_calendar
    from datetime import datetime, timezone
    calendar, zone = listing_calendar(metadata)
    now = datetime.now(timezone.utc)
    def completed(stamp):
        if stamp < now.date().isoformat():
            return True
        if not calendar:
            return False
        exchange = exchange_calendar(calendar, now.year)
        return exchange.is_session(stamp) and exchange.session_close(stamp).to_pydatetime() <= now
    return tuple(Observation(uuid4().hex, 'price', position_id, stamp, amount*Decimal(str(multiplier)), currency,
        source='Yahoo Close, split normalization reversed', listing=ticker, calendar=calendar, exchange_timezone=zone)
        for stamp, amount in as_traded_prices(frame) if stamp <= end and completed(stamp))


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


class _DownloadMerge:
    """Stage each source atomically, sharing lookups until the download is published."""
    def __init__(self, store):
        self.store = store
        self.by_id = None
        self.by_key = None

    def add(self, observations):
        additions, changes = {}, {}
        explicit_correction = False
        for observation in observations:
            if self.by_id is None:
                self.by_key = {o.key: o for o in active_observations(self.store)}
                self.by_id = {o.id: o for o in self.store.observations}
            key = observation.key
            previous = changes.get(key) or self.by_key.get(key)
            if previous:
                if previous.source == 'manual' or previous.source.startswith('manual: '):
                    continue
                if (previous.amount == observation.amount and previous.listing == observation.listing
                        and previous.calendar == observation.calendar and previous.exchange_timezone == observation.exchange_timezone):
                    continue
                observation = replace(observation, replaces=previous.id)
            existing = additions.get(observation.id) or self.by_id.get(observation.id)
            if existing:
                if existing != observation:
                    raise DataError('Observation ID already has different evidence.')
                continue
            if not isinstance(observation, Observation):
                raise DataError('Invalid or duplicate historical evidence.')
            if previous is None and observation.replaces:
                # A supplied link can move an observation to a different key.
                # Validate the entire chain before committing this source.
                explicit_correction = True
            additions[observation.id] = observation
            changes[key] = observation
        if not additions:
            return
        if explicit_correction:
            verified = replace(self.store, observations=(*self.by_id.values(), *additions.values()))
            self.by_key = {o.key: o for o in active_observations(verified)}
            self.store = verified
        else:
            self.by_key.update(changes)
        self.by_id.update(additions)

    def active_rows(self):
        return self.by_key.values() if self.by_key is not None else active_observations(self.store)

    def result(self):
        if self.by_id is None or len(self.by_id) == len(self.store.observations):
            return self.store
        return replace(self.store, observations=tuple(self.by_id.values()))


def merge_download(store, observations):
    batch = _DownloadMerge(store)
    batch.add(observations)
    return batch.result()


def download_scope_observations(directory, journal, scope, store, start, end, target):
    """Fetch evidence through the requested end, independently of valued endpoints.

    Currencies come from saved events and observed prices, never a guessed default.
    Existing manual observations win; downloads do not certify history coverage.
    """
    from portfolio_app.portfolio.positions import read_snapshot
    from portfolio_app.ledger.security_history import selected_events, affected_positions
    from portfolio_app.derivatives.settlement_archive import SettlementArchive
    holdings = read_snapshot(directory / 'holdings.csv').holdings.set_index('position_id')
    batch = _DownloadMerge(store)
    errors, currencies = [], set()
    identities = set(scope.positions) | {b.position_id for b in journal.securities if b.account_id in scope.accounts}
    for identity in sorted(identities):
        if identity not in holdings.index or not holdings.loc[identity].ticker:
            continue
        try:
            prices = fetch_security_prices(holdings.loc[identity].ticker, identity, start, end)
            if not prices:
                raise DataError('No historical prices returned.')
            batch.add(prices)
        except Exception as exc:
            errors.append(f'{identity}: {type(exc).__name__}; retry historical prices')
    subjects = set(scope.positions)
    for book in journal.securities:
        if book.position_id in scope.positions or book.account_id in scope.accounts:
            subjects.update((book.position_id, book.instrument_id))
            for event in selected_events(book):
                currencies.add(event.currency)
                currencies.update(c.currency for c in event.charges)
                currencies.update(c['currency'] for c in event.costs)
                currencies.update(c['currency'] for lot in event.lots for c in lot.costs)
    for book in journal.securities:
        for event in selected_events(book):
            if affected_positions(book,event).intersection(identities):
                currencies.add(event.valuation_currency)
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
        batch.add(values)
    currencies.update(o.currency for o in batch.active_rows()
                      if o.subject in subjects or o.subject == scope.key)
    for currency in sorted(currencies - {'', target}):
        try:
            rates = fetch_fx(currency, target, start, end)
            if not rates:
                raise DataError('No historical FX returned.')
            batch.add(rates)
        except Exception as exc:
            errors.append(f'{currency}/{target}: {type(exc).__name__}; retry historical FX')
    return batch.result(), errors
