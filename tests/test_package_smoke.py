"""Smoke-driver regressions use synthetic launchers and process failures."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytest.importorskip('playwright.sync_api')


@pytest.fixture
def smoke_driver(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'tools'))
    return importlib.import_module('package_smoke')


@pytest.mark.parametrize('installation,name', [
    ('wheel', 'portfolio-desktop.exe'), ('frozen', 'Portfolio Breakdown.exe'),
])
def test_windows_gui_entry_matches_explicit_installation(smoke_driver, tmp_path, installation, name):
    expected = tmp_path / name
    expected.write_bytes(b'Synthetic launcher; not executable')
    assert smoke_driver.windows_gui_entry(tmp_path / 'portfolio-app.exe', installation) == expected


@pytest.mark.parametrize('installation,other_name', [
    ('wheel', 'Portfolio Breakdown.exe'), ('frozen', 'portfolio-desktop.exe'),
])
def test_missing_gui_entry_cannot_use_other_installation(smoke_driver, tmp_path, installation, other_name):
    (tmp_path / other_name).write_bytes(b'Synthetic wrong launcher')
    with pytest.raises(AssertionError, match=f'Missing console-free {installation} desktop entry'):
        smoke_driver.windows_gui_entry(tmp_path / 'portfolio-app.exe', installation)


@pytest.mark.parametrize('installation', ['wheel', 'frozen'])
def test_windows_browser_smoke_selects_background_launch(smoke_driver, tmp_path, monkeypatch, installation):
    from portfolio_app.window import main
    name = 'portfolio-desktop.exe' if installation == 'wheel' else 'Portfolio Breakdown.exe'
    (tmp_path / name).write_bytes(b'Synthetic launcher')
    command = smoke_driver.windows_browser_command(tmp_path / 'portfolio-app.exe', installation, tmp_path / 'workspace')
    monkeypatch.setattr(sys, 'argv', command)
    monkeypatch.setattr(sys, 'frozen', installation == 'frozen', raising=False)
    monkeypatch.setenv('PORTFOLIO_STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.setattr('portfolio_app.window.configure_logging', lambda: None)
    launches = []
    monkeypatch.setattr('portfolio_app.app.start_desktop', lambda *a, **k: launches.append((a, k)))
    monkeypatch.setattr('portfolio_app.app.run_server', lambda *a, **k: pytest.fail('Smoke entered foreground server'))
    main()
    assert len(launches) == 1
    assert launches[0][1] == {'demo': True, 'browser': False}


def test_wheel_browser_entry_detaches_and_stops(smoke_driver, tmp_path, monkeypatch):
    from portfolio_app.launcher import request_instance, stop_instance
    state, workspace = tmp_path / 'state', tmp_path / 'synthetic workspace'
    monkeypatch.setenv('PORTFOLIO_STATE_DIR', str(state))
    if os.name == 'nt':
        executable = Path(sys.executable).parent / 'portfolio-app.exe'
    else:
        executable = tmp_path / 'portfolio-app.exe'
        (tmp_path / 'portfolio-desktop.exe').write_bytes(b'Synthetic launcher placeholder')
    command = smoke_driver.windows_browser_command(executable, 'wheel', workspace)
    if os.name != 'nt':
        # Exercise the same window/browser dispatch on hosts without a Windows stub.
        command = [sys.executable, '-m', 'portfolio_app.window', *command[1:]]
    try:
        result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stderr
        info = request_instance(workspace)
        assert info and info['ready'] and info['demo']
    finally:
        stop_instance(workspace)
    assert request_instance(workspace) is None


@pytest.mark.parametrize('arguments', [
    ['synthetic.exe'], ['synthetic.exe', '--installation', 'source'], ['--installation', 'wheel'],
])
def test_executable_cli_requires_explicit_installation(arguments):
    script = Path(__file__).resolve().parents[1] / 'tools/package_smoke.py'
    result = subprocess.run([sys.executable, str(script), *arguments], capture_output=True, text=True)
    assert result.returncode == 2
    assert 'Select --installation wheel or frozen' in result.stderr


def test_failure_outside_browser_keeps_logs_before_workspace_cleanup(smoke_driver, tmp_path, monkeypatch):
    evidence = tmp_path / 'evidence'
    roots = []
    failure = AssertionError('Synthetic launcher missing after browser checks')

    def fail(command, output, installation):
        with smoke_driver.smoke_workspace(output) as root:
            roots.append(root)
            (root / 'synthetic-server.log').write_text('Invented server diagnostic\n')
            (root / 'state/window').mkdir(parents=True)
            (root / 'state/window/window-123.log').write_text('Invented window diagnostic\n')
            raise failure

    monkeypatch.setattr(smoke_driver, '_smoke', fail)
    with pytest.raises(AssertionError) as raised:
        smoke_driver.smoke(['synthetic.exe'], evidence, installation='frozen')
    assert raised.value is failure
    assert not roots[0].exists()
    report = json.loads((evidence / 'smoke-result.json').read_text())
    assert report['status'] == 'failed' and report['installation'] == 'frozen'
    assert report['error_type'] == 'AssertionError' and str(failure) in report['traceback']
    assert (evidence / 'process-logs/synthetic-server.log').read_text() == 'Invented server diagnostic\n'
    assert (evidence / 'process-logs/window-123.log').read_text() == 'Invented window diagnostic\n'


def test_early_child_process_failure_keeps_exit_code_and_output(smoke_driver, tmp_path, monkeypatch):
    evidence = tmp_path / 'evidence'
    failure = subprocess.CalledProcessError(17, ['synthetic.exe'], output=b'invented output', stderr=b'invented error')

    def fail(*args):
        raise failure

    monkeypatch.setattr(smoke_driver, '_smoke', fail)
    with pytest.raises(subprocess.CalledProcessError) as raised:
        smoke_driver.smoke(['synthetic.exe'], evidence, installation='wheel')
    assert raised.value is failure
    report = json.loads((evidence / 'smoke-result.json').read_text())
    assert report['status'] == 'failed' and report['returncode'] == 17
    assert report['stdout'] == 'invented output' and report['stderr'] == 'invented error'


@pytest.mark.parametrize('primary_failure', [False, True])
@pytest.mark.parametrize('stop_failure', ['timeout', 'exit'])
def test_stop_failure_still_reaps_child_and_preserves_primary_error(smoke_driver, monkeypatch, capsys, primary_failure, stop_failure):
    events = []
    stop_error = (subprocess.TimeoutExpired(['synthetic-stop'], 30) if stop_failure == 'timeout'
                  else subprocess.CalledProcessError(1, ['synthetic-stop']))
    original = AssertionError('Invented original smoke failure')
    def stop(*args, **kwargs):
        events.append('stop')
        if stop_failure == 'exit' and not kwargs.get('check'):
            return subprocess.CompletedProcess(args[0], 1)
        raise stop_error
    class Child:
        def poll(self):
            return None
        def wait(self, timeout=None):
            events.append(('wait', timeout))
            if timeout is not None:
                raise subprocess.TimeoutExpired(['synthetic-child'], timeout)
        def kill(self):
            events.append('kill')
    monkeypatch.setattr(smoke_driver.subprocess, 'run', stop)
    with pytest.raises(AssertionError if primary_failure else type(stop_error)) as raised:
        try:
            if primary_failure:
                raise original
        finally:
            smoke_driver.stop_smoke_processes(['synthetic-stop'], Child(), env={}, cwd='.',
                                             preserve_failure=sys.exc_info()[1] is not None)
    assert raised.value is (original if primary_failure else stop_error)
    assert events == ['stop', ('wait', 15), 'kill', ('wait', None)]
    assert 'Smoke process cleanup failed' in capsys.readouterr().err


@pytest.mark.parametrize('primary_failure', [False, True])
def test_locked_workspace_preserves_primary_failure_after_log_capture(smoke_driver, tmp_path, monkeypatch, primary_failure):
    from types import SimpleNamespace
    root, evidence = tmp_path / 'synthetic', tmp_path / 'evidence'
    root.mkdir()
    evidence.mkdir()
    (root / 'synthetic-server.log').write_text('Invented process log')
    cleanup_error = PermissionError('Invented locked temporary directory')
    original = AssertionError('Invented original smoke failure')
    def cleanup():
        raise cleanup_error
    monkeypatch.setattr(smoke_driver, 'TemporaryDirectory',
                        lambda **kwargs: SimpleNamespace(name=str(root), cleanup=cleanup))
    with pytest.raises(AssertionError if primary_failure else PermissionError) as raised:
        with smoke_driver.smoke_workspace(evidence):
            if primary_failure:
                raise original
    assert raised.value is (original if primary_failure else cleanup_error)
    assert (evidence / 'process-logs/synthetic-server.log').read_text() == 'Invented process log'
