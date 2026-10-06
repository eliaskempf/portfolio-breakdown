"""Background ETF updates with persistent throttling and one writer per workspace.

Workers never call Streamlit. Provider adapters validate and atomically publish
snapshots; readers can continue using the previous snapshot during a download.
"""
from contextvars import copy_context
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from portfolio_app.workspace_lock import document_write
from tempfile import NamedTemporaryFile
from threading import Lock, Thread

from portfolio_app.locking import write_lock
from portfolio_app.etf import load_funds, matching_fund
from portfolio_app.etf_sources import SOURCES, download, install_snapshot, record_aliases, refresh_snapshot as refresh_provider
from portfolio_app.etf_discovery import Discovery
from portfolio_app.vaneck import refresh_snapshot as refresh_vaneck


def supported(fund):
    return fund.provider != 'manual' and fund.manifest_path is not None and (fund.isin in SOURCES or fund.isin == 'IE00BMC38736'
                                              or bool(fund.provider and fund.product_url))


def candidate_key(row):
    for column in ('isin', 'wkn', 'ticker'):
        value = row.get(column)
        if isinstance(value, str) and value.strip():
            return ('' if column == 'isin' else column + ':') + value.strip().upper()
    return ''


def discovery_candidates(holdings, funds):
    return {candidate_key(row): row for row in holdings.to_dict('records')
            if row.get('shares', 1) > 0 and matching_fund(row, funds) is None and candidate_key(row)
            and row.get('instrument_type', '') not in {'equity', 'crypto', 'physical', 'cash', 'bond', 'etc'}}


def attempt_due(record, now):
    try:
        return now - datetime.fromisoformat(record['attempted_at']) >= timedelta(days=1)
    except (KeyError, ValueError, TypeError):
        return True


def read_json(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


@document_write
def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode='w', dir=path.parent, suffix='.tmp', delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def preferences(data_dir):
    raw = read_json(Path(data_dir) / '.cache' / 'etf-refresh' / 'preferences.json')
    days = raw.get('minimum_age_days', 1)
    return {'enabled': raw.get('enabled', True) is not False,
            'minimum_age_days': days if isinstance(days, int) and 1 <= days <= 30 else 1}


def save_preferences(data_dir, *, enabled, minimum_age_days):
    write_json(Path(data_dir) / '.cache' / 'etf-refresh' / 'preferences.json',
               {'enabled': bool(enabled), 'minimum_age_days': max(1, min(30, int(minimum_age_days)))})


def due(fund, record, now, minimum_age_days=1):
    if (now.date() - fund.as_of).days < minimum_age_days:
        return False
    return attempt_due(record, now)


class RefreshCoordinator:
    def __init__(self, *, refresh=None, now=None, discover=None, install=None):
        self.refresh = refresh or (lambda fund: (refresh_vaneck if fund.isin == 'IE00BMC38736' else refresh_provider)(fund))
        self.discover = discover
        self.install = install or install_snapshot
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._lock = Lock()
        self._workers = {}
        self._errors = {}

    def running(self, data_dir):
        with self._lock:
            worker = self._workers.get(str(Path(data_dir).resolve()))
            return bool(worker and worker.is_alive())

    def error(self, data_dir):
        with self._lock:
            return self._errors.get(str(Path(data_dir).resolve()), '')

    def schedule(self, data_dir, holdings, funds, *, demo=False, force=False):
        if demo:
            return False
        directory = Path(data_dir).resolve()
        prefs = preferences(directory)
        if not force and not prefs['enabled']:
            return False
        held = {fund.isin for row in holdings.to_dict('records')
                if row.get('shares', 1) > 0 and (fund := matching_fund(row, funds)) is not None}
        records = read_json(directory / '.cache' / 'etf-refresh' / 'status.json')
        eligible = {fund.isin for fund in funds if fund.isin in held and supported(fund)
                    and (force or due(fund, records.get(fund.isin, {}), self.now(), prefs['minimum_age_days']))}
        candidates = {key: row for key, row in discovery_candidates(holdings, funds).items()
                      if force or attempt_due(records.get(key, {}), self.now())}
        if not eligible and not candidates:
            return False
        key = str(directory)
        with self._lock:
            if key in self._workers and self._workers[key].is_alive():
                return False
            self._errors.pop(key, None)
            worker = Thread(target=copy_context().run, args=(self._run, directory, eligible, force, prefs['minimum_age_days'], candidates), daemon=True)
            self._workers[key] = worker
            worker.start()
        return True

    def _run(self, directory, eligible, force, minimum_age_days, candidates):
        cache = directory / '.cache' / 'etf-refresh'
        try:
            cache.mkdir(parents=True, exist_ok=True)
            with ExitStack() as locks:
                try:
                    locks.enter_context(write_lock(cache / 'writer.lock', blocking=False))
                except BlockingIOError:
                    return
                records = read_json(cache / 'status.json')
                for fund in load_funds(directory / 'etfs'):
                    if fund.isin not in eligible or not supported(fund):
                        continue
                    prior = records.get(fund.isin, {})
                    if not force and not due(fund, prior, self.now(), minimum_age_days):
                        continue
                    record = {**prior, 'attempted_at': self.now().isoformat(), 'status': 'refreshing', 'error': ''}
                    records[fund.isin] = record
                    write_json(cache / 'status.json', records)
                    try:
                        updated = self.refresh(fund)
                        record.update(status='checked', checked_at=self.now().isoformat(), as_of=updated.as_of.isoformat())
                    except Exception as exc:
                        record.update(status='failed', error=str(exc), as_of=fund.as_of.isoformat())
                    write_json(cache / 'status.json', records)
                discover = self.discover or Discovery(download).resolve
                for key, row in candidates.items():
                    current = load_funds(directory / 'etfs')
                    if matching_fund(row, current) or (not force and not attempt_due(records.get(key, {}), self.now())):
                        continue
                    record = {**records.get(key, {}), 'attempted_at': self.now().isoformat(),
                              'status': 'discovering', 'error': ''}
                    records[key] = record
                    write_json(cache / 'status.json', records)
                    try:
                        isin, source = discover(row)
                        existing = next((fund for fund in current if fund.isin == isin), None)
                        updated = record_aliases(existing, source) if existing else self.install(
                            directory / 'etfs', isin, source=source, today=self.now().date())
                        record.update(status='checked', checked_at=self.now().isoformat(), as_of=updated.as_of.isoformat(), isin=isin)
                    except Exception as exc:
                        record.update(status='unavailable', error=str(exc))
                    write_json(cache / 'status.json', records)
        except Exception as exc:
            with self._lock:
                self._errors[str(directory)] = str(exc)


coordinator = RefreshCoordinator()
