"""Run installed native checks under an owned X11 WM or Wayland compositor."""
import argparse
import os
from pathlib import Path
import subprocess
import time

from desktop_smoke import run


def session(backend, executable, output):
    output.mkdir(parents=True, exist_ok=False)
    if backend == 'wayland':
        runtime = output / 'runtime'
        runtime.mkdir(mode=0o700)
        os.environ.update(XDG_RUNTIME_DIR=str(runtime), WAYLAND_DISPLAY='portfolio-test', QT_QPA_PLATFORM='wayland')
        os.environ.pop('DISPLAY', None)
        command = ['weston', '--backend=headless-backend.so', '--use-gl', '--socket=portfolio-test',
                   '--idle-time=0', '--width=1280', '--height=900']
    else:
        os.environ['QT_QPA_PLATFORM'] = 'xcb'
        command = ['openbox']
    with (output / 'desktop.log').open('w') as log:
        desktop = subprocess.Popen(command, stdout=log, stderr=log)
        try:
            for _ in range(100):
                if desktop.poll() is not None:
                    raise RuntimeError('Test desktop exited during startup; inspect desktop.log')
                if backend == 'x11' or (runtime / 'portfolio-test').exists():
                    break
                time.sleep(.1)
            else:
                raise TimeoutError('Wayland socket did not appear')
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
    args = parser.parse_args()
    session(args.backend, args.executable.resolve(), args.output.resolve())
