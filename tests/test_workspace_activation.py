"""Launcher activation checks using only synthetic portfolio directories."""
import json
from threading import Event

import pytest

from portfolio_app.backup import create_backup, inspect_backup, commit_restore
from portfolio_app.demo import create_demo_data
from portfolio_app.holdings import DataError
from portfolio_app.launcher import instance, request_instance, workspace_lease
from portfolio_app.workspace import inventory
from portfolio_app.workspace_activation import WorkspaceActivation
from portfolio_app.workspace_selection import selection, selection_file


def test_activate_alias_restart_and_original_bypass(tmp_path):
    launch = create_demo_data(tmp_path / 'launch')
    target = create_demo_data(tmp_path / 'restored')
    before = inventory(launch)
    with workspace_lease(launch), WorkspaceActivation(launch) as owner:
        with instance(launch, 'http://127.0.0.1:1', Event(), demo=False, activation=owner):
            result = request_instance(launch, 'activate', payload=dict(directory=str(target), generation=0, digests=inventory(target)))
            assert result['error'] == ''
            assert result['workspace'] == dict(directory=str(target), generation=1)
            assert request_instance(target)['instance'] == result['instance']
            assert selection(launch).directory == target
            with pytest.raises(DataError):
                with workspace_lease(target): pass
            # HTTP response-loss retry is idempotent.
            assert owner.activate(target, 0, inventory(target))['generation'] == 1
    assert inventory(launch) == before
    assert request_instance(launch) is None and request_instance(target) is None
    with workspace_lease(launch), WorkspaceActivation(launch) as restarted:
        assert restarted.current.directory == target
        with pytest.raises(DataError):
            with workspace_lease(target): pass
    with workspace_lease(launch), WorkspaceActivation(launch, ignore_selection=True) as original:
        assert original.current.directory == launch
    assert selection(launch).directory == target


def test_failed_activation_retains_original_and_restored_files(tmp_path, monkeypatch):
    launch = create_demo_data(tmp_path / 'launch')
    draft = inspect_backup(create_backup(launch))
    target = commit_restore(draft, tmp_path / 'restored', launch)
    try:
        with workspace_lease(launch), WorkspaceActivation(launch) as owner:
            def fail(*args): raise OSError('Invented disk failure')
            monkeypatch.setattr('portfolio_app.workspace_activation.save_selection', fail)
            with pytest.raises(OSError): owner.activate(target, 0, draft.digests)
            assert owner.current.directory == launch == selection(launch).directory
            with workspace_lease(target): pass
            assert inventory(target) == draft.digests
    finally:
        draft.close()


def test_stale_changed_and_in_use_destinations_are_refused(tmp_path):
    launch = create_demo_data(tmp_path / 'launch')
    target = create_demo_data(tmp_path / 'restored')
    with workspace_lease(launch), WorkspaceActivation(launch) as owner:
        with pytest.raises(DataError, match='active portfolio changed'):
            owner.activate(target, 10, inventory(target))
        with workspace_lease(target):
            with pytest.raises(DataError, match='in use'):
                owner.activate(target, 0, inventory(target))
        with pytest.raises(DataError, match='files changed'):
            owner.activate(target, 0, {})
        assert selection(launch).directory == launch


def test_repeated_restore_selection_is_direct(tmp_path):
    launch = create_demo_data(tmp_path / 'launch')
    targets = [create_demo_data(tmp_path / f'restored-{i}') for i in range(2)]
    with workspace_lease(launch), WorkspaceActivation(launch) as owner:
        for generation, target in enumerate(targets):
            owner.activate(target, generation, inventory(target))
    chosen = selection(launch)
    assert chosen.directory == targets[-1] and chosen.generation == 2
    assert not selection_file(targets[0]).exists()


def test_unavailable_selection_fails_closed(tmp_path):
    launch = tmp_path / 'launch'
    record = selection_file(launch)
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps(dict(version=1, directory=str(tmp_path / 'missing'), generation=1)))
    with pytest.raises(DataError, match='ignore-workspace-selection'): selection(launch)
    with WorkspaceActivation(launch, ignore_selection=True) as original:
        assert original.current.directory == launch


def test_activation_failure_removes_provisional_alias(tmp_path, monkeypatch):
    launch = create_demo_data(tmp_path / 'launch')
    target = create_demo_data(tmp_path / 'restored')
    with workspace_lease(launch), WorkspaceActivation(launch) as owner:
        with instance(launch, 'http://127.0.0.1:1', Event(), demo=False, activation=owner):
            def fail(*args): raise OSError('Invented selection-write failure')
            monkeypatch.setattr('portfolio_app.workspace_activation.save_selection', fail)
            response = request_instance(launch, 'activate', payload=dict(directory=str(target), generation=0, digests=inventory(target)))
            assert response['error']
            assert request_instance(target) is None
            with workspace_lease(target): pass
            assert request_instance(launch)['workspace']['directory'] == str(launch)


def test_status_and_stop_stay_responsive_during_activation(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    launch = create_demo_data(tmp_path / 'launch')
    stopped = Event()
    with workspace_lease(launch), WorkspaceActivation(launch) as owner:
        with instance(launch, 'http://127.0.0.1:1', stopped, demo=False, activation=owner):
            # Reproduce an activation paused inside verification, while the
            # immutable committed selection must remain available to status/Stop.
            with ThreadPoolExecutor(1) as pool:
                with owner._mutex:
                    result = pool.submit(request_instance, launch, 'stop').result(3)
                assert result['workspace']['directory'] == str(launch)
                assert stopped.is_set()
