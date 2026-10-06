"""Launcher-owned leases and the durable workspace activation commit point."""
from contextlib import ExitStack
from pathlib import Path
from threading import RLock

from portfolio_app.backup import validate_portable
from portfolio_app.holdings import DataError
from portfolio_app.workspace import inventory
from portfolio_app.workspace_lock import workspace_lock
from portfolio_app.workspace_selection import Selection, save_selection, selection


class WorkspaceActivation:
    def __init__(self, launch: Path, *, ignore_selection=False):
        self.launch = launch.resolve()
        self.ignore_selection = ignore_selection
        self.current = Selection(self.launch) if ignore_selection else selection(self.launch)
        self._leases = ExitStack()
        self._mutex = RLock()
        self._owned = {self.launch}
        self._closed = False
        self.register = lambda directory: None
        self.unregister = lambda directory: None

    def __enter__(self):
        try:
            if self.current.directory != self.launch:
                self._acquire(self.current.directory)
            if self.current.generation:
                validate_portable(self.current.directory)
            return self
        except BaseException:
            self._leases.close()
            raise

    def __exit__(self, *args):
        with self._mutex:
            self._closed = True
            self._leases.close()

    def stop(self):
        """Wait for any confirmed activation before discovery aliases disappear."""
        with self._mutex:
            self._closed = True

    def _acquire(self, directory):
        from portfolio_app.launcher import workspace_lease
        if directory not in self._owned:
            self._leases.enter_context(workspace_lease(directory))
            self._owned.add(directory)

    def status(self):
        # Selection is immutable and replaced as one reference at commit. Status
        # and Stop must stay responsive while activation verifies a large archive.
        current = self.current
        return dict(directory=str(current.directory), generation=current.generation)

    def activate(self, directory: Path, generation: int, digests: dict):
        if self.ignore_selection:
            raise DataError('Relaunch without --ignore-workspace-selection before switching portfolios.')
        directory = directory.resolve()
        with self._mutex:
            if self._closed:
                raise DataError('The application is stopping. Relaunch before switching.')
            if generation != self.current.generation:
                # A lost HTTP response can be retried without a second activation.
                if directory == self.current.directory and generation + 1 == self.current.generation:
                    return self.status()
                raise DataError('The active portfolio changed. Review the restore again.')
            if not directory.is_dir() or directory == self.current.directory:
                raise DataError('Choose the newly restored workspace.')
            from portfolio_app.launcher import workspace_lease
            with ExitStack() as pending:
                if directory not in self._owned:
                    pending.enter_context(workspace_lease(directory))
                # Serialize the durable pointer with in-flight and waiting writers.
                with workspace_lock(self.current.directory, check_context=False), workspace_lock(directory, check_context=False):
                    if selection(self.launch) != self.current:
                        raise DataError('Workspace selection changed outside this application.')
                    if inventory(directory) != digests:
                        raise DataError('Restored files changed since review. Restore again.')
                    validate_portable(directory)
                    chosen = Selection(directory, generation + 1)
                    # Publish discovery before the commit. Failure leaves the old
                    # selection intact; aliases are removed when this instance ends.
                    registered = self.register(directory)
                    try:
                        save_selection(self.launch, chosen)
                    except BaseException:
                        if registered:
                            self.unregister(directory)
                        raise
                    self.current = chosen
                    self._owned.add(directory)
                    self._leases.enter_context(pending.pop_all())
            # Retain previous leases until process shutdown: old tabs/jobs cannot
            # race a second launcher, and all aliases still focus/stop this app.
            return self.status()
