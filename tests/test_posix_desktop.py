"""Native-platform policies and real POSIX ownership tests, using invented data."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from portfolio_app.holdings import DataError
from portfolio_app.posix_window import PosixWindowPresentation, navigation_allowed
from portfolio_app.posix_window_process import internal_command


@pytest.mark.parametrize('url,main,expected', [
    ('http://127.0.0.1:8519/', True, 'allow'),
    ('https://example.org/', True, 'external'),
    ('file:///invented.txt', False, 'block'),
    ('about:srcdoc', False, 'allow'),
    ('about:blank', True, 'block'),
    ('blob:http://127.0.0.1:8519/synthetic', True, 'allow'),
    ('blob:https://example.org/synthetic', True, 'block'),
])
def test_native_navigation_policy(url, main, expected):
    assert navigation_allowed(url, 'http://127.0.0.1:8519', main_frame=main) == expected


def test_no_display_has_browser_fallback(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.delenv('DISPLAY', raising=False)
    monkeypatch.delenv('WAYLAND_DISPLAY', raising=False)
    with pytest.raises(DataError, match='--browser'):
        PosixWindowPresentation().prepare()


def test_supervisor_dispatch_preserves_arguments(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', False, raising=False)
    assert internal_command(['python', '-m', 'streamlit', 'run', 'invented ü.py']) == [
        'python', '-m', 'portfolio_app.window', '--internal-posix-supervisor', 'run', 'invented ü.py']
    monkeypatch.setattr(sys, 'frozen', True)
    assert internal_command(['app', '--internal-streamlit', 'run', 'invented.py']) == [
        'app', '--internal-posix-supervisor', 'run', 'invented.py']


def wait(check, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(.05)
    raise AssertionError('Synthetic child did not reach expected state')


@pytest.mark.skipif(os.name != 'posix', reason='POSIX process supervision')
@pytest.mark.parametrize('gate', [False, True])
def test_parent_eof_stops_owned_server_and_descendants_only(tmp_path, gate):
    marker = tmp_path / 'started.txt'
    script = tmp_path / 'synthetic_child.py'
    # Both server and descendant ignore SIGTERM to exercise bounded escalation.
    script.write_text('import os, signal, subprocess, sys, time\n'
        'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
        'p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(90)"])\n'
        f'open({str(marker)!r}, "w").write(str(os.getpid()) + " " + str(p.pid))\n'
        'time.sleep(90)\n')
    code = ('import sys\nfrom portfolio_app.posix_window_process import supervise\n'
            f'sys.exit(supervise([sys.executable, {str(script)!r}], sys.stdin.buffer))')
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(90)'])
    supervisor = subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.PIPE)
    try:
        if gate:
            supervisor.stdin.write(b'start\n')
            supervisor.stdin.flush()
            wait(marker.exists)
        supervisor.stdin.close()  # Same EOF delivered when the window is killed.
        assert supervisor.wait(timeout=20) == 0
        assert unrelated.poll() is None
        assert marker.exists() == gate
        if gate:
            for pid in map(int, marker.read_text().split()):
                # Zombies are already terminated and await the host's reaper.
                def gone():
                    status = Path(f'/proc/{pid}/stat')
                    if sys.platform == 'linux' and status.exists():
                        return status.read_text().split()[2] == 'Z'
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        return True
                    return False
                wait(gone)
    finally:
        if supervisor.poll() is None:
            supervisor.kill()
            supervisor.wait()
        unrelated.terminate()
        unrelated.wait()


def test_linux_installer_only_owns_application_paths(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / 'tools'))
    spec = importlib.util.spec_from_file_location('desktop_build', root / 'tools/desktop_build.py')
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    (bundle / 'portfolio-window').write_text('Invented executable')
    calls = []
    monkeypatch.setattr(build.subprocess, 'run', lambda cmd, **kwargs: calls.append(cmd))
    artifact = build.linux_package(bundle, tmp_path / 'stage', tmp_path, '0.1.0')
    assert artifact.suffix == '.deb'
    stage = tmp_path / 'stage'
    assert {p.parts[0] for p in [f.relative_to(stage) for f in stage.iterdir()]} == {'opt', 'usr', 'DEBIAN'}
    assert list((stage / 'DEBIAN').iterdir()) == [stage / 'DEBIAN/control']
    assert 'libxcb-cursor0' in (stage / 'DEBIAN/control').read_text()
    assert '--root-owner-group' in calls[0]


def test_self_test_rejects_real_workspace_arguments(monkeypatch):
    from portfolio_app.window import main
    monkeypatch.setattr(sys, 'argv', ['app', '--native-self-test', 'invented', 'render', '--data-dir', 'private'])
    with pytest.raises(SystemExit, match='Usage'):
        main()


def test_experiment_paths_are_narrowly_allowlisted():
    from portfolio_app.privacy import path_problem
    for path in ['tools/desktop_build.py', 'tools/desktop_smoke.py', 'packaging/posix-window.spec',
                 '.github/workflows/desktop-experiment.yml', 'docs/desktop-experiment.md']:
        assert path_problem(path) is None
    assert path_problem('docs/desktop-private-report.md')


def test_windowed_posix_bootloader_recovers_inherited_pipe(monkeypatch):
    from io import BytesIO
    from portfolio_app.window import control_input
    monkeypatch.setattr(sys, 'stdin', None)
    monkeypatch.setattr(os, 'name', 'posix')
    pipe = BytesIO(b'start\n')
    descriptors = []
    monkeypatch.setattr(os, 'dup', lambda fd: descriptors.append(fd) or 73)
    monkeypatch.setattr(os, 'fdopen', lambda fd, mode: pipe if fd == 73 and mode == 'rb' else None)
    assert control_input() is pipe
    assert descriptors == [0]


@pytest.mark.parametrize('accept', [True, False])
def test_qt6_download_accept_and_cancel(tmp_path, accept):
    selected = str(tmp_path / 'invented-download.txt') if accept else ''
    from types import SimpleNamespace
    from portfolio_app.posix_window import save_qt_download
    calls = []
    download = SimpleNamespace(suggestedFileName=lambda: 'invented.txt',
        setDownloadDirectory=lambda value: calls.append(('directory', value)),
        setDownloadFileName=lambda value: calls.append(('filename', value)),
        accept=lambda: calls.append('accept'), cancel=lambda: calls.append('cancel'))
    save_qt_download(download, lambda name: selected)
    assert calls == ([('directory', str(tmp_path)), ('filename', 'invented-download.txt'), 'accept']
                     if selected else ['cancel'])


@pytest.mark.skipif(os.name != 'posix', reason='POSIX process supervision')
def test_natural_server_exit_preserves_exit_code_without_buffered_pipe_deadlock():
    code = ('import sys; from portfolio_app.posix_window_process import supervise; '
            'sys.exit(supervise([sys.executable, "-c", "raise SystemExit(7)"], sys.stdin.buffer))')
    child = subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        child.stdin.write(b'start\n')
        child.stdin.flush()
        code = child.wait(timeout=10)
        diagnostic = child.stderr.read().decode('utf-8', errors='replace')
        assert code == 7, diagnostic
        assert 'Fatal Python error' not in diagnostic
    finally:
        child.stdin.close()
        if child.poll() is None:
            child.kill()
            child.wait()
