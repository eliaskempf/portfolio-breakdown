"""Small atomic, per-request cache for optional analytics providers."""
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from threading import Lock, RLock
from weakref import WeakValueDictionary
from portfolio_app.workspace.workspace_lock import document_write
from portfolio_app.workspace.atomic_file import staged_text


def utc_now():
    return datetime.now(timezone.utc)


def atomic_json(path: Path, value):
    _publish_json(path, lambda handle: json.dump(value, handle, allow_nan=False))


@document_write
def _publish_json(path: Path, write, version=None):
    # Check and replace under the same workspace barrier as external corrections.
    if version is not None and AnalyticsCache._version(path) != version:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with staged_text(path, write) as temporary:
        temporary.replace(path)
    return True


@dataclass(frozen=True)
class CachedResult:
    data: dict | None
    status: str
    fetched_at: str = ''
    note: str = ''


class ObservedCacheInput:
    """Keep a read-only snapshot's disk evidence for later freshness checks."""
    def __init__(self, result):
        self.result = result
        self._document = None

    def read_document(self, path):
        content = path.read_bytes()
        digest = sha256(content).digest()
        saved = self._document
        if saved is not None and saved[0] == digest:
            return saved[1]
        entry = json.loads(content)
        if isinstance(entry, dict) and entry.get('data') == self.result.data:
            # Share the already held observations. Only the envelope and digest
            # need to outlive this check. Expiry is evaluated on every read.
            entry['data'] = self.result.data
            self._document = digest, entry
        return entry


class AnalyticsCache:
    def __init__(self, directory: Path | None = None, *, now=utc_now):
        self.directory, self.now = directory, now
        self.memory = {}
        self._unsaved = {}
        self._save_lock = RLock()
        self._acquisitions = WeakValueDictionary()

    @staticmethod
    def _version(path):
        try:
            stat = path.stat()
            return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
        except OSError as exc:
            return 'unavailable', exc.errno

    def _path(self, key):
        return self.directory / (sha256(key.encode()).hexdigest() + '.json') if self.directory else None

    def cached(self, key, *, ttl=timedelta(hours=24), observed=None):
        """Read dated evidence without contacting a provider. Return the result and whether it is due."""
        path = self._path(key)
        entry = self.memory.get(key, {})
        try:
            pending = self._unsaved.get(key)
            unsaved = pending is not None and pending[0] == self._version(path)
            if unsaved:
                entry = pending[1]
            elif path:
                entry = observed.read_document(path) if observed is not None else json.loads(path.read_text())
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

    def get(self, key, fetch, *, refresh=False, ttl=timedelta(hours=24), preserve_updates=False, before_fetch=None):
        path = self._path(key)
        version = self._version(path) if path and preserve_updates else None
        pending = self._unsaved.get(key)
        previous, due = self.cached(key, ttl=ttl)
        if not refresh and not due:
            return previous
        if before_fetch is not None:
            before_fetch(key, previous, due)
        guard = nullcontext()
        if version is not None:
            with self._save_lock:
                guard = self._acquisitions.get(key)
                if guard is None:
                    guard = self._acquisitions[key] = Lock()
        with guard:
            # Another reader may have acquired this same missing revision while
            # we waited. Explicit refreshes made after publication still fetch.
            if version is not None and (self._unsaved.get(key) is not pending or self._version(path) != version):
                return self.cached(key, ttl=ttl)[0]
            return self._fetch_and_store(key, path, fetch, previous, version, pending, ttl)

    def _fetch_and_store(self, key, path, fetch, previous, version, pending, ttl):
        stamp = self.now().isoformat()
        try:
            data = fetch()
            if not isinstance(data, dict):
                raise ValueError('Provider returned invalid analytics data')
            entry = dict(data=data, fetched_at=stamp, attempted_at=stamp, error='')
            content = json.dumps(entry, allow_nan=False)
            result = CachedResult(data, 'fresh', stamp)
        except Exception:
            # Provider failures must not break valuations or expose raw responses.
            entry = dict(data=previous.data, fetched_at=previous.fetched_at,
                         attempted_at=stamp, error='Provider unavailable; cached data may be shown.')
            result = CachedResult(entry['data'], 'stale' if entry['data'] is not None else 'unavailable',
                                  entry['fetched_at'], entry['error'])
            content = None
        if not path:
            self.memory[key] = entry
            return result
        def write(handle):
            if content is None:
                # A failed provider may have raced a repair to malformed stale data.
                # Serialize that fallback only if it still owns the disk revision.
                json.dump(entry, handle, allow_nan=False)
            else:
                handle.write(content)

        with self._save_lock:
            try:
                if version is not None and self._unsaved.get(key) is not pending:
                    return self.cached(key, ttl=ttl)[0]
                if not _publish_json(path, write, version):
                    return self.cached(key, ttl=ttl)[0]
                self._unsaved.pop(key, None)
            except OSError:
                self._unsaved[key] = (version if version is not None else self._version(path), entry)
                result = CachedResult(result.data, result.status, result.fetched_at, 'Analytics cache could not be saved.')
        return result
