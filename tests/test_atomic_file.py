"""Synthetic single-file staging and caller-owned publication policies."""
from pathlib import Path

import pytest

from portfolio_app import atomic_file
from portfolio_app.analytics_cache import atomic_json
from portfolio_app.etf_refresh import write_json
from portfolio_app.holdings import DataError
from portfolio_app.positions import save_position
from portfolio_app.storage import revision, save_document


def test_stage_is_closed_beside_destination_and_does_not_publish_implicitly(tmp_path):
    path = tmp_path / 'invented.txt'
    path.write_text('original')
    handles = []
    def write(handle):
        handles.append(handle)
        handle.write('Invented £ €')
    with atomic_file.staged_text(path, write) as staged:
        assert staged.parent == path.parent and staged.suffix == '.tmp'
        assert handles[0].closed
        assert staged.read_text(encoding='utf-8') == 'Invented £ €'
        assert path.read_text() == 'original'
    assert not staged.exists() and path.read_text() == 'original'
    with atomic_file.staged_text(path, write) as staged:
        staged.replace(path)
    assert path.read_text(encoding='utf-8') == 'Invented £ €'


@pytest.mark.parametrize('failure', ['write', 'flush', 'fsync', 'review', 'replace'])
def test_failures_keep_destination_and_clean_partial_stage(tmp_path, monkeypatch, failure):
    path = tmp_path / 'invented.txt'
    path.write_text('original')
    def fail(*args):
        raise OSError('Invented failure')
    def write(handle):
        handle.write('partial')
        if failure == 'write':
            fail()
        if failure == 'flush':
            monkeypatch.setattr(handle, 'flush', fail)
    if failure == 'fsync':
        monkeypatch.setattr(atomic_file.os, 'fsync', fail)
    if failure == 'replace':
        monkeypatch.setattr(Path, 'replace', fail)
    with pytest.raises(OSError, match='Invented'):
        with atomic_file.staged_text(path, write, sync=True) as staged:
            if failure == 'review':
                fail()
            staged.replace(path)
    assert path.read_text() == 'original'
    assert not list(tmp_path.glob('*.tmp'))


@pytest.mark.parametrize('adapter', ['document', 'positions'])
@pytest.mark.parametrize('outcome', ['success', 'backup_failure', 'external_edit'])
def test_document_policy_orders_sync_backup_revision_and_publication(tmp_path, monkeypatch, adapter, outcome):
    path = tmp_path / ('holdings.csv' if adapter == 'positions' else 'settings.yaml')
    original = b'id,name,shares\na,Invented original,1\n' if adapter == 'positions' else b'invented: original\n'
    path.write_bytes(original)
    expected = revision(path)
    synced = []
    fsync, open_file = atomic_file.os.fsync, Path.open
    def sync(fd):
        fsync(fd)
        synced.append(True)
    def open_backup(candidate, *args, **kwargs):
        if candidate.parent.name == '.backups':
            assert synced, 'Backups must follow successful staging/fsync'
            if outcome == 'backup_failure':
                raise OSError('Invented backup failure')
            if outcome == 'external_edit':
                path.write_bytes(b'invented external edit')
        return open_file(candidate, *args, **kwargs)
    monkeypatch.setattr(atomic_file.os, 'fsync', sync)
    monkeypatch.setattr(Path, 'open', open_backup)
    def save():
        if adapter == 'positions':
            save_position(path, dict(name='Invented new', ticker='SYNTH-B', shares='2'), expected_revision=expected)
        else:
            save_document(path, 'invented: new\n', expected)
    if outcome == 'success':
        save()
        assert path.read_bytes() != original
    else:
        with pytest.raises(OSError if outcome == 'backup_failure' else DataError):
            save()
        assert path.read_bytes() == (original if outcome == 'backup_failure' else b'invented external edit')
    assert not list(tmp_path.glob('*.tmp'))


def test_json_strictness_stays_with_adapter(tmp_path):
    strict, status = tmp_path / 'strict.json', tmp_path / 'status.json'
    strict.write_text('{"invented": 1}')
    with pytest.raises(ValueError):
        atomic_json(strict, {'invented': float('nan')})
    assert strict.read_text() == '{"invented": 1}'
    write_json(status, {'invented': float('nan')})
    assert status.read_text() == '{"invented": NaN}'
    assert not list(tmp_path.glob('*.tmp'))
