import json
from pathlib import Path
import socket
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from portfolio_app.desktop import desktop_quote
from portfolio_app.holdings import DataError
from portfolio_app.launcher import choose_port, instance, open_existing, request_instance, session_files


def test_occupied_explicit_port_does_not_attach_to_another_app():
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0))
        port = occupied.getsockname()[1]
        with pytest.raises(DataError, match='occupied'):
            choose_port(port)


def test_control_is_authenticated_and_does_not_trust_stale_pids(tmp_path):
    stopped = threading.Event()
    with instance(tmp_path, 'http://127.0.0.1:1', stopped, demo=True):
        info = request_instance(tmp_path)
        assert info['demo'] is True
        assert info['ready'] is False
        _, path = session_files(tmp_path)
        raw = json.loads(path.read_text())
        with pytest.raises(HTTPError) as error:
            urlopen(Request(f'http://127.0.0.1:{raw["control_port"]}/stop', method='POST'), timeout=2)
        assert error.value.code == 403
        assert not stopped.is_set()
        assert request_instance(tmp_path, 'stop')
        assert stopped.is_set()
    assert request_instance(tmp_path) is None
    path.write_text(json.dumps(dict(control_port=1, token='invented', pid=123)))
    assert request_instance(tmp_path, 'stop') is None


def test_repeated_launch_refuses_silent_mode_switch(tmp_path, monkeypatch):
    monkeypatch.setattr('portfolio_app.launcher.request_instance', lambda *a: dict(demo=True, url='http://127.0.0.1:1'))
    assert open_existing(tmp_path, demo=True, browser=False)
    with pytest.raises(DataError, match='another mode'):
        open_existing(tmp_path, demo=False, browser=False)


def test_desktop_exec_escaping():
    assert desktop_quote('/tmp/space ü/100%') == '"/tmp/space ü/100%%"'
    assert '\\$' in desktop_quote('$HOME')


def test_windows_streamlit_launch_uses_frozen_dispatch(tmp_path, monkeypatch):
    import sys
    from portfolio_app.app import main
    seen = []
    def run(command, *args, **kwargs):
        seen.append(command)
        return 0
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'argv', ['portfolio-app', '--foreground', '--demo', '--no-browser'])
    monkeypatch.setattr('portfolio_app.app.run_server', run)
    with pytest.raises(SystemExit):
        main()
    assert seen[0][1:3] == ['--internal-streamlit', 'run']
    assert '--server.fileWatcherType=none' in seen[0]
