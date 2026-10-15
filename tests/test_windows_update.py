"""Synthetic release/download fixtures; no network or native installers run."""
from contextlib import contextmanager
from hashlib import sha256
import io
import json
import os
import sys
import threading
from types import SimpleNamespace

import pytest

from portfolio_app.holdings import DataError
from portfolio_app import windows_update as updates

PAYLOAD = b'Invented Windows installer bytes, not an executable.'


def release(**changes):
    asset = dict(id=123, name='portfolio-breakdown-0.2.0-windows-x64-setup.exe',
                 state='uploaded', size=len(PAYLOAD), digest='sha256:' + sha256(PAYLOAD).hexdigest())
    asset.update(changes)
    return dict(tag_name='v0.2.0', draft=False, prerelease=False, assets=[asset])


def metadata(monkeypatch, raw=None):
    monkeypatch.setattr(updates, 'app_version', lambda: '0.1.0')
    monkeypatch.setattr(updates, '_open', lambda *a, **k: io.BytesIO(json.dumps(raw or release()).encode()))
    return updates.installer_metadata('v0.2.0', installed='0.1.0')


@pytest.mark.parametrize('changes', [dict(digest=None), dict(digest='sha256:bad'), dict(size=0),
    dict(size=True), dict(size=updates.MAX_INSTALLER + 1), dict(id=-1), dict(state='new'),
    dict(name='arbitrary.exe')])
def test_metadata_requires_uploaded_installer_size_and_digest(monkeypatch, changes):
    with pytest.raises(DataError):
        metadata(monkeypatch, release(**changes))


@pytest.mark.parametrize('tag', ['../../malicious', 'v0.2.0rc1', '0.1.0', 'v0.0.9', None])
def test_reject_nonupgrade_before_network(monkeypatch, tag):
    monkeypatch.setattr(updates, '_open', lambda *a, **k: pytest.fail('unexpected request'))
    with pytest.raises(DataError):
        updates.installer_metadata(tag, installed='0.1.0')


@pytest.mark.parametrize('field,value', [('draft', True), ('prerelease', True), ('tag_name', 'v0.3.0')])
def test_exact_stable_release_is_rechecked(monkeypatch, field, value):
    raw = release()
    raw[field] = value
    with pytest.raises(DataError):
        metadata(monkeypatch, raw)


def test_download_uses_asset_id_and_verifies_before_publish(tmp_path, monkeypatch):
    asset = metadata(monkeypatch)
    calls, progress = [], []
    def fetch(url, **kwargs):
        calls.append((url, kwargs))
        return io.BytesIO(PAYLOAD)
    monkeypatch.setattr(updates, '_open', fetch)
    path = updates.download_installer(asset, tmp_path, progress.append)
    assert path.read_bytes() == PAYLOAD
    assert calls == [(f'https://api.github.com/repos/{updates.REPOSITORY}/releases/assets/123', {'binary': True})]
    assert progress == [1]
    assert not list(tmp_path.rglob('*.part'))
    assert updates.download_installer(asset, tmp_path) == path
    assert len(calls) == 1
    path.write_bytes(b'corrupt cache')
    updates.download_installer(asset, tmp_path)
    assert path.read_bytes() == PAYLOAD
    assert len(calls) == 2


@pytest.mark.parametrize('payload', [b'truncated', PAYLOAD + b'extra', b'x' * len(PAYLOAD)])
def test_invalid_download_never_becomes_executable(tmp_path, monkeypatch, payload):
    asset = metadata(monkeypatch)
    monkeypatch.setattr(updates, '_open', lambda *a, **k: io.BytesIO(payload))
    with pytest.raises(DataError):
        updates.download_installer(asset, tmp_path)
    assert not asset.path(tmp_path).exists()
    assert not list(tmp_path.rglob('*.part'))


def test_interrupted_download_is_cleaned_up(tmp_path, monkeypatch):
    asset = metadata(monkeypatch)
    class Broken(io.BytesIO):
        def read(self, *a):
            raise OSError('synthetic interruption')
    monkeypatch.setattr(updates, '_open', lambda *a, **k: Broken())
    with pytest.raises(OSError):
        updates.download_installer(asset, tmp_path)
    assert not asset.path(tmp_path).exists()
    assert not list(tmp_path.rglob('*.part'))


@pytest.mark.parametrize('url', ['http://github.com/a', 'https://github.com.evil.test/a',
    'https://example.test/a', 'https://github.com:123/a', 'https://user@github.com/a'])
def test_redirects_cannot_downgrade_or_leave_github(url):
    with pytest.raises(DataError):
        updates._ReleaseRedirects().redirect_request(None, None, 302, '', {}, url)


def test_registered_installation_must_match_running_executable(tmp_path, monkeypatch):
    @contextmanager
    def key(*args):
        yield object()
    registry = SimpleNamespace(OpenKey=key, QueryValueEx=lambda *a: (str(tmp_path), 1),
                               HKEY_CURRENT_USER=1, KEY_READ=2, KEY_WOW64_64KEY=4)
    monkeypatch.setitem(sys.modules, 'winreg', registry)
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(tmp_path / 'portfolio-app.exe'))
    assert updates.installed_directory() is None
    (tmp_path / 'unins000.exe').touch()
    assert updates.installed_directory() == tmp_path
    monkeypatch.setattr(sys, 'executable', str(tmp_path / 'portable' / 'portfolio-app.exe'))
    assert updates.installed_directory() is None
    monkeypatch.setattr(sys, 'frozen', False)
    assert updates.installed_directory() is None


def test_download_worker_deduplicates_and_reports_failure(tmp_path, monkeypatch):
    started, finish = threading.Event(), threading.Event()
    def fetch(tag):
        started.set()
        assert finish.wait(5)
        raise DataError('Invented unavailable release')
    monkeypatch.setattr(updates, 'installer_metadata', fetch)
    service = updates.InstallerDownload(tmp_path)
    assert service.start('v0.2.0')
    assert started.wait(5)
    assert not service.start('v0.3.0')
    finish.set()
    # Run the synchronous worker as well to inspect the published error state.
    service._download('v0.2.0')
    assert service.status().phase == 'failed'
    assert service.status().error == 'Invented unavailable release'


def test_handoff_rechecks_release_and_bytes_and_uses_no_shell(tmp_path, monkeypatch):
    asset = metadata(monkeypatch)
    installed = tmp_path / 'Installed app ü & spaces'
    monkeypatch.setattr(updates, 'installed_directory', lambda: installed)
    monkeypatch.setattr(updates, 'state_path', lambda: tmp_path)
    path = asset.path(tmp_path / 'updates')
    path.parent.mkdir(parents=True)
    path.write_bytes(PAYLOAD)
    called = []
    monkeypatch.setattr(updates.subprocess, 'Popen', lambda *a, **k: called.append((a, k)))
    monkeypatch.setattr(updates.subprocess, 'DETACHED_PROCESS', 8, raising=False)
    monkeypatch.setattr(updates.subprocess, 'CREATE_NEW_PROCESS_GROUP', 512, raising=False)
    handoff = updates.InstallerHandoff()
    handoff.request(asset.tag)
    assert not called  # Validation cannot launch before the server has stopped.
    with pytest.raises(DataError, match='already starting'):
        handoff.request(asset.tag)
    handoff.launch()
    command = called[0][0][0]
    assert command[0] == str(path)
    assert command[-1] == f'/DIR={installed}'
    assert '/NORESTART' in command and '/NOCLOSEAPPLICATIONS' in command
    assert '/SILENT' not in command
    assert not called[0][1].get('shell')
    assert called[0][1]['creationflags'] == 520
    path.write_bytes(b'changed after validation')
    with pytest.raises(DataError):
        handoff.launch()
    assert len(called) == 1


def test_handoff_refuses_source_or_portable_app(monkeypatch):
    monkeypatch.setattr(updates, 'installed_directory', lambda: None)
    with pytest.raises(DataError, match='installed Windows'):
        updates.InstallerHandoff().request('v0.2.0')


def test_installer_environment_preserves_system_paths_and_resets_app_context(tmp_path, monkeypatch):
    installed = tmp_path / 'Installed app'
    system = tmp_path / 'System'
    monkeypatch.setenv('PATH', os.pathsep.join(map(str, [installed / '_internal', system])))
    monkeypatch.setenv('PORTFOLIO_DOCS_URL', 'http://127.0.0.1:1234/')
    env = updates.installer_environment(installed)
    assert env['PATH'] == str(system)
    assert env['PYINSTALLER_RESET_ENVIRONMENT'] == '1'
    assert 'PORTFOLIO_DOCS_URL' not in env
    assert 'PORTFOLIO_DOCS_URL' in os.environ


def test_frozen_dll_search_is_reset_and_restored_even_if_launch_fails(monkeypatch):
    import ctypes
    calls = []
    def get(length, buffer):
        if buffer is not None:
            buffer.value = 'bundled'
        return 7
    def set_directory(directory):
        calls.append(directory)
        return 1
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *a, **k: SimpleNamespace(
        GetDllDirectoryW=get, SetDllDirectoryW=set_directory), raising=False)
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    with pytest.raises(OSError):
        with updates.system_dll_search():
            assert calls == [None]
            raise OSError('synthetic launch failure')
    assert calls == [None, 'bundled']


def test_update_control_validates_before_stopping(tmp_path, monkeypatch):
    from portfolio_app.launcher import instance, request_instance, session_files
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    monkeypatch.setattr('portfolio_app.launcher.state_path', lambda: tmp_path / 'state')
    stopped = threading.Event()
    received = []
    def request(tag):
        received.append(tag)
        if tag != 'v0.2.0':
            raise DataError('Invalid release')
    with instance(tmp_path, 'http://127.0.0.1:1', stopped, demo=False, update=request):
        info = json.loads(session_files(tmp_path)[1].read_text())
        with pytest.raises(HTTPError) as error:
            urlopen(Request(f'http://127.0.0.1:{info["control_port"]}/update',
                            data=b'{"tag":"v0.2.0"}'), timeout=2)
        assert error.value.code == 403
        assert not received
        result = request_instance(tmp_path, 'update', payload={'path': 'arbitrary.exe', 'tag': 'v0.2.0'})
        assert result['error'] and not stopped.is_set() and not received
        result = request_instance(tmp_path, 'update', payload={'tag': 'invalid'})
        assert result['error'] == 'Invalid release' and not stopped.is_set()
        result = request_instance(tmp_path, 'update', payload={'tag': 'v0.2.0'})
        assert not result['error'] and stopped.is_set()


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_installer_launch_occurs_only_after_successful_child_cleanup(tmp_path, monkeypatch, native, cleanup_fails):
    from contextlib import nullcontext
    from portfolio_app import launcher
    events = []
    class Handoff:
        def request(self, tag):
            events.append('request')
        def launch(self):
            events.append('installer')
    @contextmanager
    def child(command):
        events.append('child started')
        yield object()
        events.append('child stopped')
        if cleanup_fails:
            raise DataError('Synthetic job cleanup failure')
    @contextmanager
    def control(*args, **kwargs):
        kwargs['update']('v0.2.0')
        yield
        events.append('control stopped')
    monkeypatch.setattr(updates, 'installed_directory', lambda: tmp_path)
    monkeypatch.setattr(updates, 'InstallerHandoff', Handoff)
    monkeypatch.setattr('portfolio_app.workspace_activation.WorkspaceActivation', lambda *a, **k: nullcontext())
    monkeypatch.setattr(launcher, 'instance', control)
    monkeypatch.setattr(launcher, 'owned_child', child)
    monkeypatch.setattr(launcher, 'monitor_server', lambda *a: 0)
    presentation = SimpleNamespace(child=child, run=lambda *a: 0, focus=lambda: None) if native else None
    if cleanup_fails:
        with pytest.raises(DataError, match='cleanup'):
            launcher.run_server([], tmp_path, port=1, browser=False, demo=False, presentation=presentation)
    else:
        assert launcher.run_server([], tmp_path, port=1, browser=False, demo=False, presentation=presentation) == 0
    assert events == ['child started', 'request', 'control stopped', 'child stopped'] + ([] if cleanup_fails else ['installer'])
