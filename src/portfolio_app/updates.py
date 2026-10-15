"""Public release checks and per-user preferences; never reads portfolio data."""
from dataclasses import dataclass
from functools import lru_cache
import json
import math
from pathlib import Path
import platform
import re
import sys
import threading
import time
from urllib.request import Request, urlopen

from portfolio_app.atomic_file import staged_text
from portfolio_app.locking import write_lock
from portfolio_app.settings import state_path

REPOSITORY = 'eliaskempf/portfolio-breakdown'
RELEASES_URL = f'https://github.com/{REPOSITORY}/releases'
API_URL = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
DAY = 24 * 60 * 60
WEEK = 7 * DAY
RETRY = 60 * 60
MAX_RESPONSE = 1024 * 1024


def version_number(value):
    """Only published three-part stable versions participate in comparisons."""
    if not isinstance(value, str) or not re.fullmatch(r'v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', value) or len(value) > 40:
        return None
    return tuple(int(part) for part in value.removeprefix('v').split('.'))


def source_checkout():
    return not getattr(sys, 'frozen', False) and (Path(__file__).resolve().parents[2] / '.git').exists()


def release_target():
    if not getattr(sys, 'frozen', False):
        return 'source'
    machine = platform.machine().lower()
    if sys.platform == 'win32' and machine in {'amd64', 'x86_64'}:
        return 'windows-x64'
    if sys.platform.startswith('linux') and machine in {'amd64', 'x86_64'}:
        return 'linux-x64'
    if sys.platform == 'darwin' and machine in {'arm64', 'aarch64'}:
        return 'macos-arm64'
    return 'unsupported'


@dataclass(frozen=True)
class Release:
    tag: str

    @property
    def version(self):
        return self.tag.removeprefix('v')

    @property
    def url(self):
        # Never render arbitrary links or release-note HTML from a response.
        return f'{RELEASES_URL}/tag/{self.tag}'


def parse_release(raw, target):
    if not isinstance(raw, dict) or not isinstance(raw.get('assets'), list):
        raise ValueError('Invalid release response')
    tag = raw.get('tag_name')
    if version_number(tag) is None or raw.get('draft') is not False or raw.get('prerelease') is not False:
        return None
    version = tag.removeprefix('v')
    names = {
        'source': {f'portfolio_breakdown-{version}.tar.gz', f'portfolio_breakdown-{version}-py3-none-any.whl'},
        'windows-x64': {f'portfolio-breakdown-{version}-windows-x64-setup.exe', f'portfolio-breakdown-{version}-windows-x64.zip'},
        'linux-x64': {f'portfolio-breakdown-{version}-linux-x64.tar.gz', f'portfolio-breakdown-experimental_{version}_amd64.deb'},
        'macos-arm64': {f'portfolio-breakdown-experimental-{version}-macos-arm64.dmg'},
    }.get(target, set())
    if any(isinstance(asset, dict) and asset.get('name') in names and asset.get('state') == 'uploaded'
           for asset in raw['assets']):
        return Release(tag)
    return None


def fetch_release(target):
    request = Request(API_URL, headers={'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'portfolio-breakdown-update-check'})
    with urlopen(request, timeout=6) as response:
        content = response.read(MAX_RESPONSE + 1)
    if len(content) > MAX_RESPONSE:
        raise ValueError('Release response too large')
    return parse_release(json.loads(content), target)


class UpdateStateError(ValueError):
    pass


def _timestamp(value):
    return type(value) in (float, int) and math.isfinite(value) and 0 <= value < 253402300800


class UpdateStore:
    """Small atomic document shared across workspaces and app processes.

    Invalid preferences are preserved and fail closed, so corruption never
    silently re-enables checks after an opt-out. Locks never cover network I/O.
    """
    def __init__(self, directory):
        self.path = Path(directory) / 'updates.json'

    def read(self):
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {'version': 1, 'enabled': None, 'remind_after': 0, 'seen': '', 'checks': {}}
        except (ValueError, UnicodeError) as exc:
            raise UpdateStateError('Update preferences could not be read; automatic checks are paused.') from exc
        try:
            valid = (isinstance(raw, dict) and type(raw['version']) is int and raw['version'] == 1
                     and (raw['enabled'] is None or type(raw['enabled']) is bool)
                     and _timestamp(raw['remind_after']) and isinstance(raw['seen'], str)
                     and isinstance(raw['checks'], dict))
            if not valid:
                raise ValueError('Invalid preferences')
            for target, check in raw['checks'].items():
                if not (isinstance(target, str) and isinstance(check, dict)
                        and _timestamp(check['attempted']) and _timestamp(check['checked'])
                        and type(check['failed']) is bool
                        and (check['tag'] is None or version_number(check['tag']) is not None)):
                    raise ValueError('Invalid cached check')
        except (ValueError, KeyError, TypeError) as exc:
            raise UpdateStateError('Update preferences could not be read; automatic checks are paused.') from exc
        return raw

    def change(self, edit):
        with write_lock(self.path.with_suffix('.lock'), blocking=False):
            raw = self.read()
            if edit(raw) is False:
                return False
            with staged_text(self.path, lambda handle: json.dump(raw, handle), sync=True) as temporary:
                temporary.replace(self.path)
        return True

    def enable(self, enabled):
        def edit(raw):
            raw['enabled'] = enabled
        self.change(edit)

    def mark_seen(self, tag):
        def edit(raw):
            if raw['seen'] == tag:
                return False
            raw['seen'] = tag
        self.change(edit)


class UpdateService:
    def __init__(self, directory, target, *, automatic=True, fetch=fetch_release, now=time.time):
        self.store, self.target = UpdateStore(directory), target
        self.automatic, self.fetch, self.now = automatic, fetch, now
        self._lock = threading.Lock()
        self._thread = None

    def enabled(self, raw):
        return self.automatic if raw['enabled'] is None else raw['enabled']

    def pending(self):
        return self._thread is not None and self._thread.is_alive()

    def schedule(self, *, force=False):
        with self._lock:
            if self.pending() or self.target == 'unsupported':
                return False
            stamp = self.now()
            def reserve(raw):
                if not force and not self.enabled(raw):
                    return False
                previous = raw['checks'].get(self.target)
                if previous:
                    interval = 60 if force else RETRY if previous['failed'] else DAY
                    if 0 <= stamp - previous['attempted'] < interval:
                        return False
                raw['checks'][self.target] = {**(previous or {'checked': 0, 'tag': None}),
                                             'attempted': stamp, 'failed': True}
            try:
                if not self.store.change(reserve):
                    return False
            except (OSError, UpdateStateError):
                return False
            self._thread = threading.Thread(target=self._check, args=(stamp,), daemon=True, name='release-check')
            self._thread.start()
            return True

    def _check(self, stamp):
        try:
            release = self.fetch(self.target)
            def complete(raw):
                # A later check in another process owns its result. Preference
                # changes made during this download remain intact.
                if raw['checks'].get(self.target, {}).get('attempted') != stamp:
                    return False
                raw['checks'][self.target] = {'attempted': stamp, 'checked': self.now(),
                    'tag': release.tag if release else None, 'failed': False}
            self.store.change(complete)
        except Exception:
            # Failure was reserved durably before network access. Keep the last
            # successful result and retry later, without interrupting the app.
            pass

    def available(self, raw, installed):
        check = raw['checks'].get(self.target, {})
        tag, current = check.get('tag'), version_number(installed)
        return Release(tag) if tag and current is not None and version_number(tag) > current else None

    def claim_reminder(self, installed):
        """Reserve one weekly reminder across sessions before displaying it."""
        def claim(raw):
            check = raw['checks'].get(self.target, {})
            if (not self.enabled(raw) or not self.available(raw, installed) or check.get('failed', True)
                    or not 0 <= self.now() - check['checked'] < DAY
                    or self.now() < raw['remind_after']):
                return False
            raw['remind_after'] = self.now() + WEEK
            raw['seen'] = check['tag']
        return self.store.change(claim)


@lru_cache(maxsize=8)
def _service(directory, target, automatic):
    return UpdateService(directory, target, automatic=automatic)


def updates_for():
    return _service(str(state_path()), release_target(), not source_checkout())
