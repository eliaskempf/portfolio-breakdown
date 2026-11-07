"""Stage one text file beside its destination; callers own publication policy."""
from collections.abc import Callable, Iterator
from contextlib import contextmanager
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TextIO


@contextmanager
def staged_text(path: Path, write: Callable[[TextIO], object], *, sync: bool = False) -> Iterator[Path]:
    """Yield a closed staged file and remove it on every exit.

Text is written as UTF-8 without newline translation. The parent must exist.
Serialization, locks, backups and revision checks belong
to the caller, which explicitly calls temporary.replace(path) to publish. Sync
flushes file content before yielding; it does not fsync the containing directory.
"""
    temporary = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', newline='', dir=path.parent, suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            write(handle)
            if sync:
                handle.flush()
                os.fsync(handle.fileno())
        yield temporary
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
