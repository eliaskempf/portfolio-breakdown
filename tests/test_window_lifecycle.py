"""Presentation integration uses invented workspaces and injected children only."""
from contextlib import contextmanager
from types import SimpleNamespace
import signal
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import json

import pytest

from portfolio_app.holdings import DataError
from portfolio_app import launcher


def test_focus_requires_authentication_and_post(tmp_path):
    focused = threading.Event()
    stopped = threading.Event()
    with launcher.instance(tmp_path, 'http://127.0.0.1:1', stopped, demo=False, focus=focused.set):
        info = launcher.request_instance(tmp_path)
        assert info['presentation'] == 'window'
        raw = json.loads(launcher.session_files(tmp_path)[1].read_text())
        url = f'http://127.0.0.1:{raw["control_port"]}/focus'
        for method, headers, code in [('POST', {}, 403), ('GET', {'Authorization': 'Bearer ' + raw['token']}, 404)]:
            with pytest.raises(HTTPError) as error:
                urlopen(Request(url, method=method, headers=headers), timeout=2)
            assert error.value.code == code
        assert not focused.is_set()
        assert launcher.open_existing(tmp_path, demo=False, browser=True, focus=True)
        assert focused.is_set() and not stopped.is_set()


def test_old_browser_instance_is_reopened_not_stopped(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(launcher, 'request_instance', lambda directory, action='status':
                        calls.append(action) or dict(demo=False, url='http://127.0.0.1:1234'))
    monkeypatch.setattr('portfolio_app.desktop.open_browser', lambda url: calls.append(url))
    assert launcher.open_existing(tmp_path, demo=False, browser=True, focus=True)
    assert calls == ['status', 'http://127.0.0.1:1234']


def test_mode_conflict_does_not_focus(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, 'request_instance', lambda *a: dict(demo=True, presentation='window'))
    with pytest.raises(DataError, match='another mode'):
        launcher.open_existing(tmp_path, demo=False, browser=True, focus=True)


def test_contended_window_launch_does_not_spawn(tmp_path):
    with launcher.workspace_lease(tmp_path):
        with pytest.raises(DataError, match='starting or closing'):
            with launcher.presentation_workspace(tmp_path, demo=False, browser=False, timeout=0):
                pytest.fail('Cannot acquire the occupied workspace')


def test_competing_launch_reuses_discovered_window(tmp_path, monkeypatch):
    focused = threading.Event()
    with launcher.workspace_lease(tmp_path), launcher.instance(
            tmp_path, 'http://127.0.0.1:1', threading.Event(), demo=False, focus=focused.set):
        with launcher.presentation_workspace(tmp_path, demo=False, browser=False) as acquired:
            assert not acquired
        assert focused.is_set()


def test_monitor_close_before_readiness_does_not_open_view(monkeypatch):
    stopped = threading.Event()
    stopped.set()
    assert launcher.monitor_server(SimpleNamespace(poll=lambda: None), '', stopped,
                                   lambda: pytest.fail('Already closed')) == 0


def test_monitor_detects_early_child_exit():
    with pytest.raises(DataError, match='code 7'):
        launcher.monitor_server(SimpleNamespace(poll=lambda: 7, returncode=7), '', threading.Event(), lambda: None)


def test_monitor_startup_timeout(monkeypatch):
    clock = iter([0, 61])
    monkeypatch.setattr(launcher.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(launcher, 'ready', lambda url: False)
    with pytest.raises(DataError, match='60 seconds'):
        launcher.monitor_server(SimpleNamespace(poll=lambda: None), '', threading.Event(), lambda: None)


def test_presentation_exception_reaps_child_before_lease_release(tmp_path, monkeypatch):
    order = []
    @contextmanager
    def child(command):
        try:
            yield SimpleNamespace()
        finally:
            with pytest.raises(DataError):
                with launcher.workspace_lease(tmp_path):
                    pass
            order.append('reaped')
    def run(url, stopped, monitor):
        assert launcher.request_instance(tmp_path)['presentation'] == 'window'
        raise DataError('Synthetic renderer failure')
    presentation = SimpleNamespace(child=child, run=run, focus=lambda: None)
    original = signal.getsignal(signal.SIGTERM)
    with pytest.raises(DataError, match='renderer failure'), launcher.workspace_lease(tmp_path):
        launcher.run_server([], tmp_path, port=1, browser=False, demo=False, presentation=presentation)
    assert order == ['reaped']
    assert signal.getsignal(signal.SIGTERM) == original
    assert launcher.request_instance(tmp_path) is None
    with launcher.workspace_lease(tmp_path):
        pass


def test_failed_spawn_restores_signal_handler(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('Synthetic spawn failure')
    monkeypatch.setattr(launcher.subprocess, 'Popen', fail)
    original = signal.getsignal(signal.SIGTERM)
    with pytest.raises(OSError):
        launcher.run_server([], tmp_path, port=1, browser=False, demo=False)
    assert signal.getsignal(signal.SIGTERM) == original
