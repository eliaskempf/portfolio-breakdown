"""Cross-platform lock contention policy using temporary synthetic lock files."""
import errno
import sys
from types import SimpleNamespace
from contextlib import contextmanager
from pathlib import Path

import pytest

from portfolio_app import locking


@pytest.mark.parametrize('code,expected', [(errno.EACCES, BlockingIOError), (errno.EIO, OSError)])
def test_windows_nonblocking_lock_normalizes_only_contention(tmp_path, monkeypatch, code, expected):
    def fail(*args):
        raise OSError(code, 'Synthetic lock failure')
    monkeypatch.setattr(locking, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setitem(sys.modules, 'msvcrt', SimpleNamespace(locking=fail, LK_LOCK=1, LK_NBLCK=2, LK_UNLCK=3))
    with pytest.raises(expected) as error:
        with locking.write_lock(tmp_path / 'synthetic.lock', blocking=False):
            pytest.fail('Contended lock was acquired')
    assert type(error.value) is expected


def test_windows_contention_reaches_lock_api_without_reading_reserved_byte(tmp_path, monkeypatch):
    path = tmp_path / 'synthetic.lock'
    path.write_bytes(b'0')  # Existing lease files remain supported.
    open_file = Path.open
    def denied(*args, **kwargs):
        raise PermissionError('Synthetic reserved byte is held by another handle')
    @contextmanager
    def open_reserved(path, *args, **kwargs):
        with open_file(path, *args, **kwargs) as handle:
            yield SimpleNamespace(seek=handle.seek, fileno=handle.fileno, read=denied, write=denied)
    calls = []
    def contend(fd, mode, length):
        calls.append((mode, length))
        raise OSError(errno.EACCES, 'Synthetic contention')
    monkeypatch.setattr(Path, 'open', open_reserved)
    monkeypatch.setattr(locking, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setitem(sys.modules, 'msvcrt', SimpleNamespace(locking=contend, LK_LOCK=1, LK_NBLCK=2, LK_UNLCK=3))
    with pytest.raises(BlockingIOError):
        with locking.write_lock(path, blocking=False):
            pytest.fail('Contended lease acquired')
    assert calls == [(2, 1)]


@pytest.mark.parametrize('initial', [b'', b'0'], ids=['empty-lease', 'existing-lease'])
def test_native_lock_contention_and_release_after_exception(tmp_path, initial):
    path = tmp_path / 'synthetic.lock'
    path.write_bytes(initial)
    with pytest.raises(ValueError, match='Synthetic body failure'):
        with locking.write_lock(path):
            with pytest.raises(BlockingIOError):
                with locking.write_lock(path, blocking=False):
                    pytest.fail('Second handle acquired an active lease')
            raise ValueError('Synthetic body failure')
    with locking.write_lock(path, blocking=False):
        pass
