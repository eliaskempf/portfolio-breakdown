"""Versioned dated valuation evidence and coverage attestations, never cash posts."""
from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

from portfolio_app.derivative_types import decimal, identifier
from portfolio_app.holdings import DataError
from portfolio_app.security_history import day, denomination
from portfolio_app.storage import save_document

FILE = 'history-observations.json'


@dataclass(frozen=True)
class HistoryScope:
    positions: tuple[str, ...] = ()
    accounts: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()

    def __post_init__(self):
        for field in ('positions', 'accounts', 'unresolved'):
            values = tuple(sorted(set(getattr(self, field))))
            for value in values:
                identifier(value)
            object.__setattr__(self, field, values)

    @property
    def key(self):
        return sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class Observation:
    id: str
    kind: str
    subject: str
    date: str
    amount: Decimal
    currency: str
    target: str = ''
    side: str = 'close'
    boundary: str = ''
    source: str = 'manual'
    basis: str = 'as_traded'
    replaces: str = ''
    listing: str = ''
    calendar: str = ''
    exchange_timezone: str = ''

    def __post_init__(self):
        identifier(self.id); identifier(self.subject); identifier(self.source); day(self.date)
        denomination(self.currency)
        object.__setattr__(self, 'amount', decimal(self.amount, 'Observation'))
        if any(not isinstance(value, str) for value in (self.listing, self.calendar, self.exchange_timezone)):
            raise DataError('Invalid listing provenance.')
        if self.kind not in ('price', 'fx', 'value') or self.side not in ('close', 'before', 'after'):
            raise DataError('Invalid historical observation kind or boundary.')
        if self.kind in ('price', 'fx') and self.amount <= 0:
            raise DataError('Prices and FX rates must be positive.')
        if self.kind == 'fx':
            denomination(self.target)
            if self.target == self.currency:
                raise DataError('An FX observation needs different currencies.')
        if self.kind == 'price' and self.basis != 'as_traded':
            raise DataError('Personal history requires prices in the units traded on that date.')
        if (self.side != 'close') != bool(self.boundary):
            raise DataError('Flow-boundary valuations must identify their cash flow.')

    @property
    def key(self):
        return self.kind, self.subject, self.date, self.currency, self.target, self.side, self.boundary


@dataclass(frozen=True)
class CoverageReview:
    id: str
    scope: HistoryScope
    start: str
    end: str
    journal_fingerprint: str

    def __post_init__(self):
        identifier(self.id); day(self.start); day(self.end)
        if self.start > self.end or not isinstance(self.scope, HistoryScope):
            raise DataError('Invalid coverage interval or scope.')
        if len(self.journal_fingerprint) != 64:
            raise DataError('Coverage review must identify the reviewed journal.')


@dataclass(frozen=True)
class ObservationStore:
    observations: tuple[Observation, ...] = ()
    reviews: tuple[CoverageReview, ...] = ()
    classifications: tuple[tuple[str, str], ...] = ()
    version: int = 1

    def __post_init__(self):
        if type(self.version) is not int or self.version != 1:
            raise DataError('Unsupported historical observations version.')
        for name, cls in (('observations', Observation), ('reviews', CoverageReview)):
            values = tuple(getattr(self, name))
            if any(not isinstance(v, cls) for v in values) or len({v.id for v in values}) != len(values):
                raise DataError('Invalid or duplicate historical evidence.')
            object.__setattr__(self, name, values)
        values = tuple(tuple(v) for v in self.classifications)
        if any(len(v) != 2 or v[1] not in ('external', 'income', 'expense', 'boundary') for v in values):
            raise DataError('Invalid cash adjustment classification.')
        if len({v[0] for v in values}) != len(values):
            raise DataError('Duplicate cash adjustment classification.')
        object.__setattr__(self, 'classifications', values)
        active_observations(self)


def active_observations(store):
    if isinstance(store, _ObservationIndex):
        return store.active
    seen, replaced = {}, set()
    for observation in store.observations:
        if observation.replaces:
            if observation.replaces not in seen or observation.replaces in replaced:
                raise DataError('Observation correction must replace one active observation.')
            replaced.add(observation.replaces)
        seen[observation.id] = observation
    values = [o for o in store.observations if o.id not in replaced]
    if len({o.key for o in values}) != len(values):
        raise DataError('Correct the existing observation instead of adding a competing valuation.')
    return tuple(values)


class _ObservationIndex:
    """Read-only per-calculation index; never persisted or globally cached."""
    def __init__(self, store):
        self.observations, self.reviews, self.classifications = store.observations, store.reviews, store.classifications
        self.active = active_observations(store)
        self.values, self.fx = {}, {}
        prices = {}
        for observation in self.active:
            key = observation.kind, observation.subject, observation.date, observation.side, observation.boundary
            self.values.setdefault(key, []).append(observation)
            if observation.kind == 'price' and observation.side == 'close':
                prices.setdefault(observation.subject, {}).setdefault(observation.date, []).append(observation)
            if observation.kind == 'fx':
                self.fx.setdefault(observation.date, []).append(observation)

        self.price_dates = {subject: tuple(sorted(series)) for subject, series in prices.items()}
        self.price_series = {subject: tuple(series[stamp] for stamp in self.price_dates[subject])
                             for subject, series in prices.items()}

def index_observations(store):
    return store if isinstance(store, _ObservationIndex) else _ObservationIndex(store)


def from_dict(raw):
    return ObservationStore(
        tuple(Observation(**o) for o in raw.get('observations', ())),
        tuple(CoverageReview(**{**r, 'scope': HistoryScope(**r['scope'])}) for r in raw.get('reviews', ())),
        tuple(raw.get('classifications', ())), raw['version'])


def load_observations(directory):
    path = Path(directory) / FILE
    if not path.exists():
        return ObservationStore(), None
    try:
        content = path.read_bytes()
        return from_dict(json.loads(content)), sha256(content).hexdigest()
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise DataError(f'Invalid historical observations; file retained: {exc}') from exc


def save_observations(directory, store, expected_revision):
    content = json.dumps(asdict(store), default=str, indent=2, allow_nan=False) + '\n'
    from_dict(json.loads(content))
    save_document(Path(directory) / FILE, content, expected_revision)


def add_observation(store, observation):
    existing = next((o for o in store.observations if o.id == observation.id), None)
    if existing:
        if existing != observation:
            raise DataError('Observation ID already has different evidence.')
        return store
    return replace(store, observations=(*store.observations, observation))


def rate(store, source, target, stamp, *, side=None):
    if source == target:
        return Decimal(1)
    values = (store.fx.get(stamp, ()) if isinstance(store, _ObservationIndex) else
              [o for o in active_observations(store) if o.kind == 'fx' and o.date == stamp])
    if side is not None:
        values = [o for o in values if o.side == side]
    direct = [o for o in values if o.currency == source and o.target == target]
    inverse = [o for o in values if o.currency == target and o.target == source]
    if direct and inverse and abs(direct[0].amount*inverse[0].amount-1) > Decimal('1e-8'):
        raise DataError('Conflicting direct and inverse historical FX observations.')
    return direct[0].amount if direct else 1/inverse[0].amount if inverse else None


def lookup(store, kind, subject, stamp, *, side='close', boundary='', currency=None):
    values = (store.values.get((kind, subject, stamp, side, boundary), ()) if isinstance(store, _ObservationIndex)
              else active_observations(store))
    matches = [o for o in values if o.kind == kind and o.subject == subject
               and o.date == stamp and o.side == side and o.boundary == boundary
               and (currency is None or o.currency == currency)]
    return matches[0] if len(matches) == 1 else None


class ObservationFX:
    """Adapt explicit dated FX to the existing acquisition-cost interface."""
    def __init__(self, store, fallback=None):
        self.store, self.fallback = index_observations(store), fallback

    def get(self, source, target, stamp):
        from portfolio_app.fx import DatedRate
        value = rate(self.store, source, target, stamp)
        if value is not None:
            return DatedRate(float(value), stamp, 'Saved dated FX')
        return self.fallback.get(source, target, stamp) if self.fallback else DatedRate(note='Supply dated FX for cost comparison.')
