"""Small atomic, per-request cache for optional analytics providers."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from portfolio_app.workspace_lock import document_write
from tempfile import NamedTemporaryFile


def utc_now():
    return datetime.now(timezone.utc)


@document_write
def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, allow_nan=False)
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class CachedResult:
    data: dict | None
    status: str
    fetched_at: str = ''
    note: str = ''


class AnalyticsCache:
    def __init__(self, directory: Path | None = None, *, now=utc_now):
        self.directory, self.now = directory, now
        self.memory = {}

    def get(self, key, fetch, *, refresh=False, ttl=timedelta(hours=24)):
        path = self.directory / (sha256(key.encode()).hexdigest() + '.json') if self.directory else None
        entry = self.memory.get(key, {})
        try:
            if path and path.exists():
                entry = json.loads(path.read_text())
            age = self.now() - datetime.fromisoformat(entry['attempted_at'])
            fetched_age = self.now() - datetime.fromisoformat(entry['fetched_at']) if entry.get('data') is not None else None
            if fetched_age is not None and fetched_age < timedelta(0):
                raise ValueError('Future cache timestamp')
            cooldown = timedelta(minutes=15) if entry.get('error') else ttl
            if not refresh and timedelta(0) <= age < cooldown:
                return CachedResult(entry.get('data'), 'stale' if entry.get('error') and entry.get('data') is not None
                                    else 'cached' if entry.get('data') is not None else 'unavailable',
                                    entry.get('fetched_at', ''), entry.get('error', ''))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            entry = {}
        stamp = self.now().isoformat()
        try:
            data = fetch()
            if not isinstance(data, dict):
                raise ValueError('Provider returned invalid analytics data')
            json.dumps(data, allow_nan=False)
            entry = dict(data=data, fetched_at=stamp, attempted_at=stamp, error='')
            result = CachedResult(data, 'fresh', stamp)
        except Exception:
            # Provider failures must not break valuations or expose raw responses.
            entry = dict(data=entry.get('data'), fetched_at=entry.get('fetched_at', ''),
                         attempted_at=stamp, error='Provider unavailable; cached data may be shown.')
            result = CachedResult(entry['data'], 'stale' if entry['data'] is not None else 'unavailable',
                                  entry['fetched_at'], entry['error'])
        self.memory[key] = entry
        if path:
            try:
                atomic_json(path, entry)
            except OSError:
                result = CachedResult(result.data, result.status, result.fetched_at, 'Analytics cache could not be saved.')
        return result
