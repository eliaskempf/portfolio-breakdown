"""Workspace transactions shared by snapshots, document writers and activation.

Order: workspace barrier, then document locks. Network work never holds this
barrier. Reentrancy is thread-local; the OS lock coordinates other processes.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from threading import RLock, local

from portfolio_app.holdings import DataError
from portfolio_app.locking import write_lock

_mutex = RLock()
_locks = {}
_contexts = {}
_held = local()
write_context = ContextVar('workspace_write_context', default=None)


def bind_workspace(launch, chosen):
    context = (launch, chosen)
    write_context.set(context)
    with _mutex:
        _contexts[str(chosen.directory)] = context


def document_workspace(path: Path) -> Path:
    """Resolve the root for the application's root, ETF and cache documents."""
    path = Path(path).absolute()
    context = write_context.get()
    if context is not None and path.is_relative_to(context[1].directory):
        return context[1].directory
    with _mutex:
        roots = [Path(root) for root in _contexts if path.is_relative_to(root)]
    if roots:
        return max(roots, key=lambda root: len(root.parts))
    for parent in path.parents:
        if parent.name in {'.cache', 'etfs'}:
            return parent.parent.resolve()
    for parent in path.parents:
        if parent.name in {'.cache', 'etfs', '.backups'}:
            return parent.parent.resolve()
    return path.parent.resolve()


@contextmanager
def workspace_lock(directory: Path, *, check_context=True):
    directory = Path(directory).resolve()
    key = str(directory)
    with _mutex:
        lock = _locks.setdefault(key, RLock())
    with lock:
        held = getattr(_held, 'keys', set())
        if key in held:
            yield
            return
        directory.mkdir(parents=True, exist_ok=True)
        with write_lock(directory / '.workspace.lock'):
            # Streamlit can run dialog fragments on a new script thread without
            # rerunning render_app. Their old document paths must remain fenced.
            with _mutex:
                context = write_context.get() or _contexts.get(key)
            if check_context and context is not None:
                from portfolio_app.workspace_selection import selection
                launch, expected = context
                current = selection(launch)
                if current != expected or current.directory != directory:
                    raise DataError('The active portfolio changed. Reopen this view before saving.')
            _held.keys = held | {key}
            try:
                yield
            finally:
                _held.keys = held


def document_write(function):
    """Guard a persistence function whose first argument is a document path."""
    @wraps(function)
    def guarded(path, *args, **kwargs):
        with workspace_lock(document_workspace(path)):
            return function(path, *args, **kwargs)
    return guarded
