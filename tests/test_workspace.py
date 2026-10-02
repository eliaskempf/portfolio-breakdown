from pathlib import Path

import pytest

from portfolio_app.demo import create_demo_data
from portfolio_app.holdings import DataError
from portfolio_app.launcher import workspace_lease
from portfolio_app.settings import workspace_path
from portfolio_app.workspace import copy_workspace, inventory


def test_default_is_independent_of_launch_directory(tmp_path, monkeypatch):
    expected = workspace_path()
    elsewhere = tmp_path / 'different launch directory'
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert workspace_path() == expected
    assert workspace_path(Path('custom')) == elsewhere / 'custom'


def test_migration_backup_restore_preserve_all_files(tmp_path):
    source = create_demo_data(tmp_path / 'source ü')
    (source / '.backups').mkdir()
    (source / '.backups' / 'synthetic.csv').write_text('deliberately invented backup', encoding='utf-8')
    (source / '.cache').mkdir()
    (source / '.cache' / 'synthetic.json').write_text('{}', encoding='utf-8')
    original = inventory(source)
    copied = copy_workspace(source, tmp_path / 'migrated é')
    backup = copy_workspace(copied, tmp_path / 'backup')
    restored = copy_workspace(backup, tmp_path / 'restored')
    assert inventory(source) == inventory(copied) == inventory(restored) == original


def test_copy_refuses_overwrite_and_nested_destination(tmp_path):
    source = create_demo_data(tmp_path / 'source')
    existing = tmp_path / 'existing'
    existing.mkdir()
    sentinel = existing / 'keep'
    sentinel.write_text('unchanged')
    for target in [existing, source, source / 'nested']:
        with pytest.raises(DataError, match='new directory'):
            copy_workspace(source, target)
    assert sentinel.read_text() == 'unchanged'


def test_copy_refuses_active_workspace(tmp_path):
    source = create_demo_data(tmp_path / 'source')
    with workspace_lease(source):
        with pytest.raises((DataError, PermissionError), match='.*'):
            copy_workspace(source, tmp_path / 'destination')
    assert not (tmp_path / 'destination').exists()


def test_copy_detects_source_changes(tmp_path, monkeypatch):
    import shutil
    source = create_demo_data(tmp_path / 'source')
    original = shutil.copytree
    def changing(*args, **kwargs):
        result = original(*args, **kwargs)
        (source / 'changed.txt').write_text('synthetic concurrent change')
        return result
    monkeypatch.setattr('portfolio_app.workspace.shutil.copytree', changing)
    with pytest.raises(DataError, match='changed during copy'):
        copy_workspace(source, tmp_path / 'destination')
    assert not (tmp_path / 'destination').exists()


def test_invalid_workspace_is_not_copied(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'holdings.csv').write_text('invalid')
    with pytest.raises(DataError):
        copy_workspace(source, tmp_path / 'destination')
    assert not (tmp_path / 'destination').exists()
