"""User-confirmed account marks and FX, independent of transaction posting."""
from dataclasses import asdict, dataclass
from datetime import date
from hashlib import sha256
import json
from pathlib import Path

from portfolio_app.derivative_types import PriceObservation, decimal, currency, iso_date, identifier
from portfolio_app.holdings import DataError
from portfolio_app.storage import save_document

FILE = 'account-observations.json'
BASIS_KINDS = frozenset({'snapshot', 'trade', 'settlement', 'final_settlement'})


@dataclass(frozen=True)
class BoundPriceObservation(PriceObservation):
    """An explicitly confirmed mark for one immutable effective clearing basis."""
    basis_fingerprint: str = ''

    def __post_init__(self):
        super().__post_init__()
        if (not isinstance(self.basis_fingerprint, str) or len(self.basis_fingerprint) != 64
                or any(c not in '0123456789abcdef' for c in self.basis_fingerprint)):
            raise DataError('A bound mark needs a valid clearing-basis fingerprint.')


def _basis_events(journal, position_id):
    from portfolio_app.account_journal import effective_events
    return tuple(e for e in effective_events(journal)
                 if e.position_id == position_id and e.kind in BASIS_KINDS)


def _fingerprint(events):
    content = json.dumps([asdict(e) for e in events], default=str, sort_keys=True, separators=(',', ':'))
    return sha256(content.encode()).hexdigest()


def bind_mark(journal, position_id, observation):
    """Confirm an observation against current facts; never post settlement cash."""
    position = next((p for p in journal.positions if p.id == position_id), None)
    if position is None or observation.contract_id != position.contract_id:
        raise DataError('The mark must identify the selected position and exact contract.')
    _validate_mark(observation)
    events = _basis_events(journal, position_id)
    if events and observation.date < max(e.date for e in events):
        raise DataError('The mark predates the latest effective clearing-basis event.')
    fields = {name: getattr(observation, name) for name in PriceObservation.__dataclass_fields__}
    return BoundPriceObservation(**fields, basis_fingerprint=_fingerprint(events))


def _validate_mark(mark):
    if not isinstance(mark, PriceObservation):
        raise DataError('Marks must be validated price observations.')
    if mark.date > date.today().isoformat():
        raise DataError('A saved market observation cannot be dated in the future.')


def _decode(raw):
    if (not isinstance(raw, dict) or type(raw.get('version')) is not int or raw['version'] not in (1, 2)
            or not isinstance(raw.get('marks'), dict) or not isinstance(raw.get('fx'), list)):
        raise DataError('Unsupported observations format/version.')
    marks = {}
    for key, value in raw['marks'].items():
        identifier(key, 'Position ID')
        cls = BoundPriceObservation if 'basis_fingerprint' in value else PriceObservation
        mark = cls(**value)
        _validate_mark(mark)
        marks[key] = mark
    rates = {}
    for value in raw['fx']:
        source, target = currency(value['source_currency']), currency(value['target_currency'])
        stamp = iso_date(value['date'])
        rate = decimal(value['rate'])
        if source == target or rate <= 0 or stamp > date.today().isoformat():
            raise DataError('Invalid FX observation.')
        identifier(value['source'], 'FX source')
        if (source, target) in rates:
            raise DataError('Duplicate currency pair in saved observations.')
        rates[source, target] = dict(value, rate=str(rate))
    return marks, rates


def load_observations(directory):
    path = Path(directory) / FILE
    if not path.exists():
        return {}, {}, None
    try:
        content = path.read_bytes()
        marks, rates = _decode(json.loads(content))
        return marks, rates, sha256(content).hexdigest()
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise DataError(f'Invalid account observations: {exc}') from exc


def save_observations(directory, marks, rates, expected_revision):
    try:
        for mark in marks.values():
            _validate_mark(mark)
        if any(key != (value['source_currency'], value['target_currency']) for key, value in rates.items()):
            raise DataError('FX map keys must match the observation currency pair.')
        raw = dict(version=2, marks={key: asdict(value) for key, value in marks.items()}, fx=list(rates.values()))
        content = json.dumps(raw, default=str, indent=2, allow_nan=False) + '\n'
        _decode(json.loads(content))
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise DataError(f'Invalid account observations: {exc}') from exc
    save_document(Path(directory) / FILE, content, expected_revision)


def current_marks(journal, marks):
    """Withhold stale basis bindings; old unbound same-day marks are ambiguous.

    Corrections are resolved before comparison. Voiding a later basis event can
    therefore restore a mark confirmed against the preceding effective history.
    """
    result = {}
    for position in journal.positions:
        mark = marks.get(position.id)
        if mark is None or mark.contract_id != position.contract_id:
            continue
        events = _basis_events(journal, position.id)
        latest = max((e.date for e in events), default='')
        if isinstance(mark, BoundPriceObservation):
            valid = mark.basis_fingerprint == _fingerprint(events) and mark.date >= latest
        else:
            valid = mark.date > latest
        if valid and mark.date <= date.today().isoformat():
            result[position.id] = mark
    return result


def manual_rate(rates, source, target):
    if source == target:
        return 1.
    if (source, target) in rates:
        return float(rates[source, target]['rate'])
    if (target, source) in rates:
        return 1. / float(rates[target, source]['rate'])
    for middle in ('EUR', 'USD', 'GBP'):
        if middle not in (source, target):
            def leg(start, end):
                direct, inverse = rates.get((start, end)), rates.get((end, start))
                return float(direct['rate']) if direct else 1. / float(inverse['rate']) if inverse else None
            first, second = leg(source, middle), leg(middle, target)
            if first is not None and second is not None:
                return first * second
    return None
