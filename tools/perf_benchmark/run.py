"""Paired cold and reopened desktop/browser measurements using invented portfolios."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
from statistics import median
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

from fixture import create_workspace
from renderer import exercise, wait


def benchmark(executable, workspace, trial, variant, surface, scenario):
    trial.mkdir()
    env = dict(os.environ, PORTFOLIO_STATE_DIR=str(workspace.parent / 'state'),
        PORTFOLIO_BENCHMARK_TRIAL=str(trial), PORTFOLIO_BENCHMARK_VARIANT=variant,
        PORTFOLIO_BENCHMARK_REFRESH=scenario)
    args = [str(executable), '--data-dir', str(workspace), '--offline-demo', '--ignore-workspace-selection']
    if surface == 'browser':
        with socket.socket() as reserve:
            reserve.bind(('127.0.0.1', 0))
            port = reserve.getsockname()[1]
        args += ['--browser', '--foreground', '--no-browser', '--server.port', str(port)]
    started = time.time()
    with (trial / 'app.log').open('w') as log:
        proc = subprocess.Popen(args, env=env, stdout=log, stderr=log)
        try:
            if surface == 'native':
                code = proc.wait(timeout=240)
                path = trial / 'window.json'
                if not path.exists():
                    raise RuntimeError(f'No native results. Exit {code}. ' + (trial/'app.log').read_text(errors='replace')[-2000:])
                report = json.loads(path.read_text())
                if code or 'error' in report:
                    raise RuntimeError(report.get('error', str(code)))
            else:
                from playwright.sync_api import sync_playwright
                from portfolio_app.runtime.launcher import ready
                url = f'http://127.0.0.1:{port}'
                wait(lambda: ready(url))
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'),
                        args=['--no-sandbox'])
                    page = browser.new_page(viewport={'width':1280,'height':900})
                    page.goto(url)
                    report = exercise(page.evaluate, trial, scenario)
                    browser.close()
        finally:
            if proc.poll() is None:
                if surface == 'browser':
                    subprocess.run([str(executable), '--browser', '--stop', '--data-dir', str(workspace)],
                        env=env, stdout=log, stderr=log, timeout=20)
                else:
                    proc.terminate()
                proc.wait(timeout=20)
    report['launch_ms'] = (report.pop('ready_at') - started) * 1000
    events = [json.loads(line) for line in (trial / 'server.jsonl').read_text().splitlines()]
    report['history_calls'] = sum(e['event'] == 'history_calculated' for e in events)
    report['disk_hits'] = sum(e['event'] == 'disk_hit' for e in events)
    for interaction in [report['first'], report['range_3m'], *report['warm']]:
        begin = interaction.pop('click_epoch_ms')
        specific = [e for e in events if begin <= e['epoch_ms'] <= begin + interaction['settled_ms']]
        interaction['history_compute_ms'] = sum(e['duration_ms'] for e in specific if e['event']=='history_calculated')
        sends = [e for e in specific if e['event']=='chart_enqueued']
        if sends:
            interaction['first_chart_enqueue_ms'] = sends[0]['epoch_ms'] - begin
    return report


def run(executable, destination, surfaces, pairs):
    destination.mkdir(parents=True, exist_ok=False)
    result = dict(platform=sys.platform, cpu=platform.machine(), revision=os.environ.get('GITHUB_SHA'),
        runner_image=os.environ.get('ImageVersion'), python=platform.python_version(), runs=[])
    binary = hashlib.sha256(executable.read_bytes()).hexdigest()
    result['binary_sha256'] = binary
    expected = {}
    for surface in surfaces:
        for pair in range(pairs):
            order = ('baseline','persistent') if pair % 2 == 0 else ('persistent','baseline')
            for variant in order:
                with TemporaryDirectory(prefix='portfolio-benchmark-') as tmp:
                    workspace = Path(tmp) / 'synthetic'
                    create_workspace(workspace)
                    for scenario, launch in [('unchanged','cold'), ('changed','reopen')]:
                        trial = Path(tmp) / launch
                        metrics = benchmark(executable, workspace, trial, variant, surface, scenario)
                        if variant == 'persistent' and launch == 'reopen' and metrics['disk_hits'] == 0:
                            raise AssertionError('Relaunch did not reuse persisted history')
                        if metrics['digest'] != expected.setdefault(scenario, metrics['digest']):
                            raise AssertionError('Rendered series or gain data differs across runs')
                        result['runs'].append(dict(surface=surface, pair=pair, variant=variant, launch=launch, **metrics))
                        (destination / 'results.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
                        print(surface, pair, variant, launch, 'first chart ms', round(metrics['first']['chart_ms']),
                              'warm median ms', round(median(r['chart_ms'] for r in metrics['warm'])), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--surface', choices=['native','browser','both'], default='both')
    parser.add_argument('--pairs', type=int, choices=range(1,11), default=2)
    options = parser.parse_args()
    run(options.executable.resolve(), options.output.resolve(),
        ['native','browser'] if options.surface=='both' else [options.surface], options.pairs)
