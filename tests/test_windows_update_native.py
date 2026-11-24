"""Exercise the real Windows child job and detached Setup handoff with an invented executable."""
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest


@pytest.mark.skipif(sys.platform != 'win32', reason='Requires a native Windows job and executable')
def test_windows_installer_survives_server_job_cleanup(tmp_path, monkeypatch):
    from portfolio_app.runtime import launcher, windows_update
    from portfolio_app.runtime.window_process import contained_child

    # A purpose-built Setup stand-in records what an actual child receives.
    # It has no installer privileges, registry writes or external dependencies.
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    recorder = tmp_path / 'Recorder.cs'
    recorder.write_text('''
using System;
using System.IO;
public static class Recorder {
    public static void Main(string[] args) {
        string path = Environment.GetEnvironmentVariable("SYNTHETIC_SETUP_RECEIPT");
        File.WriteAllLines(path + ".part", args);
        File.Move(path + ".part", path);
    }
}
''', encoding='utf-8')
    executable = tmp_path / 'recorder.exe'
    subprocess.run([str(compiler), '/nologo', '/target:winexe', f'/out:{executable}', str(recorder)],
                   cwd=tmp_path, check=True)
    contents = executable.read_bytes()
    asset = windows_update.Installer('v9.0.0', 77, len(contents), sha256(contents).hexdigest())
    installed = tmp_path / 'Invented installation with spaces'
    installed.mkdir()
    receipt = tmp_path / 'setup-arguments.txt'
    monkeypatch.setenv('SYNTHETIC_SETUP_RECEIPT', str(receipt))
    monkeypatch.setattr(windows_update, 'installed_directory', lambda: installed)
    monkeypatch.setattr(windows_update, 'installer_metadata', lambda tag: asset)
    monkeypatch.setattr(windows_update, '_open', lambda *args, **kwargs: BytesIO(contents))
    windows_update.download_installer(asset, windows_update.state_path() / 'updates')

    workspace = tmp_path / 'Synthetic workspace'
    workspace.mkdir()
    port = launcher.choose_port(0)
    app = tmp_path / 'empty_app.py'
    app.write_text('import streamlit as st\nst.write("Synthetic app")\n', encoding='utf-8')
    command = [sys.executable, '-m', 'streamlit', 'run', str(app), '--server.address=127.0.0.1',
               f'--server.port={port}', '--server.headless=true', '--server.fileWatcherType=none',
               '--browser.gatherUsageStats=false']

    def trigger():
        assert not receipt.exists()
        result = launcher.request_instance(workspace, 'update', payload={'tag': asset.tag})
        assert result and not result['error']

    # The desktop window normally runs this monitor alongside its message loop.
    # Exercise the same real Job, child process, control endpoint and shutdown.
    presentation = SimpleNamespace(child=contained_child, focus=lambda: None,
                                   run=lambda url, stopped, monitor: monitor(trigger))
    assert launcher.run_server(command, workspace, port=port, browser=False, demo=False,
                               ignore_selection=True, presentation=presentation) == 0
    assert launcher.request_instance(workspace) is None
    assert not launcher.ready(f'http://127.0.0.1:{port}')
    deadline = time.monotonic() + 15
    while not receipt.exists() and time.monotonic() < deadline:
        time.sleep(.05)
    assert receipt.read_text(encoding='utf-8-sig').splitlines() == [
        '/SP-', '/NORESTART', '/NOCLOSEAPPLICATIONS', '/NOFORCECLOSEAPPLICATIONS',
        '/NORESTARTAPPLICATIONS', f'/DIR={installed}',
    ]
