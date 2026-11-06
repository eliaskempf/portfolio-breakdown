"""Smoke-driver regressions use synthetic launchers and process failures."""
import importlib
import json
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
