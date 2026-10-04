"""Exercise an installed experimental binary with fixed synthetic native checks."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

from portfolio_app.launcher import request_instance, ready


def wait(check, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(.2)
    raise TimeoutError('Installed desktop check timed out')


def run(executable, output):
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for mode in ['render', 'welcome', 'early-close']:
        with (output / f'{mode}.log').open('w') as log:
            result = subprocess.run([str(executable), '--native-self-test', str(output / mode), mode],
                                    cwd=output, stdout=log, stderr=subprocess.STDOUT, timeout=150)
        if result.returncode:
            raise RuntimeError(f'{mode} failed; inspect synthetic log in {output}')
        report = json.loads((output / mode / 'native-result.json').read_text())
        assert report['status'] == 'passed'
        results.append(report)
    # Kill the actual window process and confirm its independently supervised
    # server exits. Browser fallback uses the same isolated workspace afterward.
    with TemporaryDirectory(prefix='portfolio-packaged-synthetic-') as temporary:
        root = Path(temporary)
        workspace = root / 'workspace'
        workspace.mkdir()
        sentinel = workspace / 'synthetic-preserved.txt'
        sentinel.write_text('Invented persistent content')
        env = dict(os.environ, PORTFOLIO_STATE_DIR=str(root / 'state'))
        previous = os.environ.get('PORTFOLIO_STATE_DIR')
        os.environ['PORTFOLIO_STATE_DIR'] = env['PORTFOLIO_STATE_DIR']
        try:
            args = [str(executable), '--data-dir', str(workspace), '--offline-demo', '--demo', '--skip-intro']
            with (output / 'lifecycle.log').open('w') as log:
                process = subprocess.Popen(args, env=env, cwd=root, stdout=log, stderr=subprocess.STDOUT)
                try:
                    def status():
                        if process.poll() is not None:
                            raise RuntimeError('Native window exited during startup')
                        value = request_instance(workspace)
                        return value if value and value['ready'] else None
                    instance = wait(status)
                    # A repeated launch must leave the first instance in charge.
                    subprocess.run(args, env=env, cwd=root, stdout=log, stderr=log, timeout=15, check=True)
                    assert request_instance(workspace)['instance'] == instance['instance']
                    process.kill()
                    process.wait(timeout=15)
                    wait(lambda: not ready(instance['url']), timeout=30)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                fallback = subprocess.Popen([*args, '--browser', '--foreground', '--no-browser'],
                    env=env, cwd=root, stdout=log, stderr=log)
                try:
                    wait(lambda: (value := request_instance(workspace)) and value['ready'])
                    subprocess.run([str(executable), '--browser', '--data-dir', str(workspace), '--stop'],
                                   env=env, cwd=root, stdout=log, stderr=log, check=True, timeout=30)
                    assert fallback.wait(timeout=20) == 0
                finally:
                    if fallback.poll() is None:
                        fallback.terminate()
                        fallback.wait(timeout=20)
            assert sentinel.read_text() == 'Invented persistent content'
        finally:
            if previous is None:
                os.environ.pop('PORTFOLIO_STATE_DIR', None)
            else:
                os.environ['PORTFOLIO_STATE_DIR'] = previous
    from package_smoke import smoke as browser_smoke
    console = executable.with_name('portfolio-cli') if sys.platform == 'darwin' else executable
    browser_smoke([str(console), '--browser'])
    summary = {'status': 'passed', 'native': results,
               'browser': ['packaged portfolio edit, save, restart, CSV/Excel import, backup/restore'],
               'lifecycle': ['repeat launch', 'window crash cleanup', 'browser fallback', 'restart preserves data'],
               'shipping': 'experimental only; review native test gaps and signing'}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.executable.resolve(), args.output.resolve())
