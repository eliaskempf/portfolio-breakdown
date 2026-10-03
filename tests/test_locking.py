"""Cross-platform lock contention policy using temporary synthetic lock files."""
import errno
import sys
from types import SimpleNamespace

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
