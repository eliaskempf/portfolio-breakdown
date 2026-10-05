"""Run installed native checks under an owned X11 WM or Wayland compositor."""
import argparse
import os
from pathlib import Path
import subprocess
import time

from desktop_smoke import run


def session(backend, executable, output, native_repeats=0):
    output.mkdir(parents=True, exist_ok=False)
    desktop_env = dict(os.environ)
    if backend == 'wayland':
        if not os.environ.get('DISPLAY'):
            raise RuntimeError('Wayland activation checks require an input seat; run this command under xvfb-run -a')
        runtime = output / 'runtime'
        runtime.mkdir(mode=0o700)
        os.environ.update(XDG_RUNTIME_DIR=str(runtime), WAYLAND_DISPLAY='portfolio-test',
                          QT_QPA_PLATFORM='wayland', PORTFOLIO_TEST_WESTON_SCENE='1')
        desktop_env.update(XDG_RUNTIME_DIR=str(runtime))
        desktop_env.pop('WAYLAND_DISPLAY', None)
        # Weston gets a virtual keyboard/pointer from Xvfb. The app itself has
        # no DISPLAY and must connect to our private Wayland socket.
        os.environ.pop('DISPLAY', None)
        command = ['weston', '--backend=x11-backend.so', '--debug', '--socket=portfolio-test',
                   '--idle-time=0', '--width=1280', '--height=900']
    else:
        os.environ['QT_QPA_PLATFORM'] = 'xcb'
        command = ['openbox']
    with (output / 'desktop.log').open('w') as log:
        desktop = subprocess.Popen(command, stdout=log, stderr=log, env=desktop_env)
        try:
            for _ in range(100):
                if desktop.poll() is not None:
                    raise RuntimeError('Test desktop exited during startup; inspect desktop.log')
                if backend == 'x11' or (runtime / 'portfolio-test').exists():
                    break
                time.sleep(.1)
            else:
                raise TimeoutError('Wayland socket did not appear')
            if native_repeats:
                import json
                reports = []
                for attempt in range(native_repeats):
                    for mode in ['desktop', 'welcome', 'early-close']:
                        target = output / f'{attempt + 1}-{mode}'
                        command = [str(executable), '--native-self-test', str(target), mode]
                        if not executable.read_bytes()[:2] == b'#!':
                            command = ['gdb', '--batch', '--return-child-result',
                                       '-ex', 'set pagination off', '-ex', 'run',
                                       '-ex', 'thread apply all bt', '--args', *command]
                        with target.with_suffix('.log').open('w') as log:
                            try:
                                result = subprocess.run(command, stdout=log, stderr=log, timeout=180)
                                code = result.returncode
                            except subprocess.TimeoutExpired:
                                code = 'timeout'
                        report_path = target / 'native-result.json'
                        report = json.loads(report_path.read_text()) if report_path.exists() else {}
                        reports.append(dict(attempt=attempt + 1, mode=mode, exit=code,
                                            status=report.get('status', 'missing')))
                (output / 'repeated-results.json').write_text(json.dumps(reports, indent=2))
                if any(r['exit'] or r['status'] != 'passed' for r in reports):
                    raise RuntimeError('Repeated native checks failed; inspect diagnostic evidence')
            else:
                run(executable, output / 'app', desktop=True)
        finally:
            desktop.terminate()
            try:
                desktop.wait(timeout=10)
            except subprocess.TimeoutExpired:
                desktop.kill()
                desktop.wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('backend', choices=['x11', 'wayland'])
    parser.add_argument('executable', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--native-repeats', type=int, default=0)
    args = parser.parse_args()
    session(args.backend, args.executable.resolve(), args.output.resolve(), args.native_repeats)
