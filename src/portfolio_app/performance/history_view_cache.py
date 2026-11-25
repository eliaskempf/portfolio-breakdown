"""Two replaceable, exact-input snapshots of the last portfolio chart and summary."""
from dataclasses import fields
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from hashlib import sha256
from importlib.metadata import version
import json

from portfolio_app.performance.gain_accounting import GainSummary
from portfolio_app.performance.history_diagnostics import HistoryIssue
from portfolio_app.performance.history_valuation import ValuationEvidence
from portfolio_app.performance.history_view import HistoryView
from portfolio_app.performance.personal_performance import PersonalPerformance
from portfolio_app.portfolio.holdings import DataError
from portfolio_app.workspace.atomic_file import staged_text
from portfolio_app.workspace.workspace_lock import workspace_lock

_RECORDS = {cls.__name__: cls for cls in (HistoryView, PersonalPerformance, ValuationEvidence, GainSummary)}
_LIMIT = 2 * 1024 * 1024
# Increment for changes to valuation, coverage, calendar or return semantics.
_SCHEMA = 4


@lru_cache(maxsize=1)
def _versions():
    return (_SCHEMA, version('portfolio-breakdown'), version('exchange-calendars'), version('tzdata'))


def _encode(value):
    if type(value) in _RECORDS.values():
        return {'$':type(value).__name__, 'v':{f.name:_encode(getattr(value, f.name)) for f in fields(value)}}
    if isinstance(value, HistoryIssue):
        return {'$':'issue', 'v':[str(value), value.kind, _encode(value.causes)]}
    if isinstance(value, Decimal):
        return {'$':'decimal', 'v':str(value)}
    if isinstance(value, tuple):
        return [_encode(item) for item in value]
    # Points are plain JSON scalars keyed by fixed chart field names.
    return value


def _decode(value):
    if isinstance(value, list):
        return tuple(_decode(item) for item in value)
    if not isinstance(value, dict) or '$' not in value:
        return value
    kind, data = value['$'], value['v']
    if kind == 'decimal':
        return Decimal(data)
    if kind == 'issue':
        message, category, causes = data
        return HistoryIssue(message, category, _decode(causes))
    return _RECORDS[kind](**{name:_decode(item) for name,item in data.items()})


def load_or_compute(directory, signature, compute):
    """Only publish complete snapshots. A miss or unusable cache runs the canonical calculation."""
    journal, revision, scope, period, target, day, points, annualized = signature
    if not revision:
        return compute()
    key = sha256(json.dumps((_versions(), journal, revision, scope.key, period, target,
        day.isoformat(), points, annualized)).encode()).hexdigest()
    path = directory / '.cache' / 'history-views' / ('chart.json' if points else 'summary.json')
    try:
        with path.open('rb') as handle:
            data = handle.read(_LIMIT + 1)
        stored_key, checksum, payload = data.split(b'\n', 2)
        if len(data) <= _LIMIT and stored_key.decode() == key and sha256(payload).hexdigest().encode() == checksum:
            view = _decode(json.loads(payload))
            if isinstance(view, HistoryView):
                return view
    except (OSError, ValueError, KeyError, TypeError, InvalidOperation, RecursionError):
        pass
    view = compute()
    try:
        payload = json.dumps(_encode(view), allow_nan=False, separators=(',', ':'))
        checksum = sha256(payload.encode()).hexdigest()
        content = f'{key}\n{checksum}\n{payload}'
        if len(content) <= _LIMIT:
            with workspace_lock(directory):
                path.parent.mkdir(parents=True, exist_ok=True)
                with staged_text(path, lambda handle: handle.write(content)) as staged:
                    staged.replace(path)
    except (DataError, OSError, ValueError, TypeError, RecursionError):
        pass
    return view
