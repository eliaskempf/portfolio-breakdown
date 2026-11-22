"""Small atomic, per-request cache for optional analytics providers."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from portfolio_app.workspace.workspace_lock import document_write
from portfolio_app.workspace.atomic_file import staged_text


def utc_now():
    return datetime.now(timezone.utc)


@document_write
def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with staged_text(path, lambda handle: json.dump(value, handle, allow_nan=False)) as temporary:
        temporary.replace(path)


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
        self._unsaved = {}

    @staticmethod
    def _version(path):
        try:
            stat = path.stat()
            return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
        except OSError as exc:
            return 'unavailable', exc.errno

    def _path(self, key):
        return self.directory / (sha256(key.encode()).hexdigest() + '.json') if self.directory else None

    def cached(self, key, *, ttl=timedelta(hours=24)):
        """Read dated evidence without contacting a provider. Return the result and whether it is due."""
        path = self._path(key)
        entry = self.memory.get(key, {})
        try:
            version = self._unsaved.get(key)
            unsaved = version is not None and version == self._version(path)
            if path and not unsaved:
                entry = json.loads(path.read_text())
            now = self.now()
            age = now - datetime.fromisoformat(entry['attempted_at'])
            data = entry.get('data')
            if data is not None and now < datetime.fromisoformat(entry['fetched_at']):
                raise ValueError('Future cache timestamp')
            cooldown = timedelta(minutes=15) if entry.get('error') else ttl
            due = not timedelta(0) <= age < cooldown
            status = 'unavailable' if data is None else 'stale' if due or entry.get('error') else 'cached'
            note = 'Analytics cache could not be saved.' if unsaved else entry.get('error', '')
            return CachedResult(data, status, entry.get('fetched_at', ''), note), due
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return CachedResult(None, 'unavailable'), True

    def get(self, key, fetch, *, refresh=False, ttl=timedelta(hours=24)):
        previous, due = self.cached(key, ttl=ttl)
        if not refresh and not due:
            return previous
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
            entry = dict(data=previous.data, fetched_at=previous.fetched_at,
                         attempted_at=stamp, error='Provider unavailable; cached data may be shown.')
            result = CachedResult(entry['data'], 'stale' if entry['data'] is not None else 'unavailable',
                                  entry['fetched_at'], entry['error'])
        self.memory[key] = entry
        if path := self._path(key):
            try:
                atomic_json(path, entry)
                self._unsaved.pop(key, None)
            except OSError:
                self._unsaved[key] = self._version(path)
                result = CachedResult(result.data, result.status, result.fetched_at, 'Analytics cache could not be saved.')
        return result
