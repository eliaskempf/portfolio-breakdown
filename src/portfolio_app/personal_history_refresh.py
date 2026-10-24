"""Background market evidence for personal history; never edits transactions."""
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from threading import RLock

from portfolio_app.gain_accounting import scope_dates
from portfolio_app.history_observations import HistoryScope, active_observations, load_observations, save_observations
from portfolio_app.locking import write_lock
from portfolio_app.market_data import coordinator
from portfolio_app.personal_market import merge_download
from portfolio_app.positions import read_snapshot
from portfolio_app.security_history import history_fingerprint
from portfolio_app.settlement_archive import SettlementArchive
from portfolio_app.workspace_lock import workspace_lock


@dataclass(frozen=True)
class RefreshStatus:
    pending: bool = False
    errors: tuple[str, ...] = ()


class HistoryRefresher:
    def __init__(self, jobs=coordinator, *, download=None, now=None):
        self.jobs, self.download = jobs, download
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._lock, self._results = RLock(), {}

    def pending(self, directory):
        return self.jobs.pending(Path(directory).resolve(), 'personal-history')

    def request(self, directory, journal, scope, target, *, force=False, today=None):
        directory = Path(directory).resolve()
        active = {b.position_id for b in journal.securities if b.active}
        scope = HistoryScope(tuple(active.intersection(scope.positions)), scope.accounts)
        dates = scope_dates(journal, scope)
        if not dates:
            return RefreshStatus()
        start, end = dates[0], (today or date.today()).isoformat()
        snapshot = read_snapshot(directory/'holdings.csv')
        from portfolio_app.account_journal import load_journal
        _, journal_revision = load_journal(directory)
        signature = sha256(repr((scope, history_fingerprint(journal), snapshot.revision, SettlementArchive(directory).revision(), start, end, target)).encode()).hexdigest()
        identity = str(directory), signature
        with self._lock:
            if self.pending(directory):
                return RefreshStatus(pending=True)
            previous = self._results.get(identity)
            if previous and not force:
                stamp, errors = previous
                cooldown = timedelta(minutes=5) if errors else timedelta(hours=1)
                if timedelta() <= self.now()-stamp < cooldown:
                    return RefreshStatus(errors=errors)
            started = self.jobs.request(directory, 'personal-history',
                lambda: self._run(directory, journal, scope, start, end, target, snapshot.revision, identity, journal_revision))
            return RefreshStatus(pending=started, errors=() if started else ('Market-data queue is busy. Try again shortly.',))

    def _run(self, directory, journal, scope, start, end, target, holdings_revision, identity, journal_revision):
        try:
            with write_lock(directory/'.personal-history-refresh.lock', blocking=False):
                from portfolio_app.personal_market import download_scope_observations
                before, _ = load_observations(directory)
                updated, errors = (self.download or download_scope_observations)(
                    directory, journal, scope, before, start, end, target)
                original_ids = {o.id for o in before.observations}
                incoming = tuple(replace(o, replaces='') for o in active_observations(updated) if o.id not in original_ids)
                with workspace_lock(directory):
                    from portfolio_app.account_journal import load_journal
                    _, current_revision = load_journal(directory)
                    if (read_snapshot(directory/'holdings.csv').revision != holdings_revision
                            or current_revision != journal_revision):
                        errors = ('Positions or journal changed during loading. Refresh history again.',)
                    elif incoming:
                        # Reload under the lock: manual edits and other scopes made
                        # during the network request must survive the merge.
                        latest, revision = load_observations(directory)
                        merged = merge_download(latest, incoming)
                        if merged != latest:
                            save_observations(directory, merged, revision)
        except Exception:
            errors = ('Historical market data could not be refreshed. Try again later.',)
        with self._lock:
            self._results[identity] = self.now(), tuple(errors)


refresher = HistoryRefresher()
