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


def launch_services(app, output):
    """Check the child report: `open -W` does not return the app's exit status."""
    if output.exists():
        raise ValueError('Native evidence directory must be new')
    args = ['open', '-W', '-n']
    # Launch Services does not inherit the calling shell's environment. Forward
    # only the opt-in flags for synthetic input, never arbitrary CI credentials.
    if (os.environ.get('PORTFOLIO_TEST_HOSTED_INPUT') == '1'
            and os.environ.get('GITHUB_ACTIONS') == 'true'
            and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'):
        for key in ('PORTFOLIO_TEST_HOSTED_INPUT', 'GITHUB_ACTIONS', 'RUNNER_ENVIRONMENT'):
            args.extend(['--env', f'{key}={os.environ[key]}'])
    args.extend([str(app), '--args', '--native-self-test', str(output), 'render'])
    subprocess.run(args, check=True, timeout=150)
    report = json.loads((output / 'native-result.json').read_text(encoding='utf-8'))
    required = {'native window shown', 'native WebKit snapshot captured',
                'native Cocoa open panel supplies exact uploaded bytes to WebKit',
                'native Cocoa save panel writes exact downloaded bytes',
                'window close stops managed server and preserves workspace'}
    if (report.get('status') != 'passed' or report.get('errors') != []
            or report.get('mode') != 'render' or report.get('platform') != 'darwin'
            or not required.issubset(report.get('checks', []))):
        raise RuntimeError(f'Launch Services native checks failed; inspect {output}')


def source_launch_services(output):
    """Exercise Launch Services without spending time freezing an installer."""
    import plistlib
    import shlex
    with TemporaryDirectory(prefix='portfolio-synthetic-app-') as temporary:
        app = Path(temporary) / 'Synthetic Native Probe.app'
        contents = app / 'Contents'
        binaries = contents / 'MacOS'
        binaries.mkdir(parents=True)
        with (contents / 'Info.plist').open('wb') as handle:
            plistlib.dump(dict(CFBundleExecutable='probe', CFBundleIdentifier='org.portfolio.synthetic-probe',
                              CFBundleName='Synthetic Native Probe', CFBundlePackageType='APPL'), handle)
        launcher = binaries / 'probe'
        launcher.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable)
                            + ' -m portfolio_app.window "$@"\n', encoding='utf-8')
        launcher.chmod(0o755)
        launch_services(app, output)


def run(executable, output, desktop=False):
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for mode in (['desktop'] if desktop else ['render']) + ['welcome', 'early-close']:
        with (output / f'{mode}.log').open('w') as log:
            try:
                result = subprocess.run([str(executable), '--native-self-test', str(output / mode), mode],
                                        cwd=output, stdout=log, stderr=subprocess.STDOUT, timeout=150)
                code = result.returncode
            except subprocess.TimeoutExpired:
                code = 'timeout'
        path = output / mode / 'native-result.json'
        report = json.loads(path.read_text()) if path.exists() else {'mode': mode, 'status': 'failed'}
        if code:
            report['status'] = 'failed'
            report['process_exit'] = code
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
    browser_smoke([str(console), '--browser'], evidence=output / 'browser-evidence')
    passed = all(result['status'] == 'passed' for result in results)
    summary = {'status': 'passed' if passed else 'failed', 'native': results,
               'browser': ['packaged portfolio edit, save, restart, CSV/Excel import, backup/restore'],
               'lifecycle': ['repeat launch', 'window crash cleanup', 'browser fallback', 'restart preserves data'],
               'shipping': 'experimental only; review native test gaps and signing'}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2))
    if not passed:
        raise RuntimeError(f'Native checks failed; inspect synthetic summary in {output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path, nargs='?')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--desktop', action='store_true', help='Require real window-state/focus checks')
    parser.add_argument('--browser-only', action='store_true')
    parser.add_argument('--launch-services', action='store_true', help='Launch a Mac .app and require its native report')
    parser.add_argument('--source-launch-services', action='store_true', help='Test source through a temporary synthetic Mac .app')
    args = parser.parse_args()
    if args.source_launch_services:
        if args.executable or args.launch_services or args.browser_only or args.desktop:
            parser.error('--source-launch-services requires no executable or other mode')
        source_launch_services(args.output.resolve())
    elif args.executable is None:
        parser.error('an executable is required')
    elif args.launch_services:
        if args.browser_only or args.desktop:
            parser.error('--launch-services cannot be combined with other modes')
        launch_services(args.executable.resolve(), args.output.resolve())
    elif args.browser_only:
        from package_smoke import smoke
        smoke([str(args.executable.resolve()), '--browser'], evidence=args.output.resolve())
    else:
        run(args.executable.resolve(), args.output.resolve(), desktop=args.desktop)
