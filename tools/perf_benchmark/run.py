"""Paired cold and reopened desktop/browser measurements using invented portfolios."""
import argparse
from collections import Counter
import csv
import hashlib
import json
import os
from pathlib import Path
from datetime import date
import platform
import socket
from math import ceil
from statistics import median
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

from fixture import create_workspace, prepare_fund_check, prepare_fundamental_check, prepare_quote_check, prepare_risk_check
from renderer import measure_view, wait


def benchmark(executable, workspace, trial, variant, surface, scenario, *, themes=False, company_accounts=False):
    trial.mkdir()
    if scenario == 'exposure' and themes:
        prepare_fundamental_check(workspace)
    if scenario == 'risk':
        risk_listings = prepare_risk_check(workspace)
    env = dict(os.environ, PORTFOLIO_STATE_DIR=str(workspace.parent / 'state'),
        PORTFOLIO_BENCHMARK_TRIAL=str(trial), PORTFOLIO_BENCHMARK_VARIANT=variant,
        PORTFOLIO_BENCHMARK_REFRESH=scenario, PORTFOLIO_BENCHMARK_THEMES=str(int(themes)),
        PORTFOLIO_BENCHMARK_COMPANY_ACCOUNTS=str(int(company_accounts)))
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
                    report = measure_view(page.evaluate, trial, variant, scenario, themes=themes, company_accounts=company_accounts)
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
    def interval(measurement):
        start, end = measurement.pop('event_range')
        return events[start:end]

    if scenario == 'risk':
        for index, action in enumerate(report['actions']):
            during = interval(action)
            requests = Counter(e['ticker'] for e in during if e['event'] == 'adjusted_fetched')
            expected = []
            if index == 0:
                expected = risk_listings[-1:] if variant == 'candidate' else risk_listings
            if requests != Counter(expected):
                raise AssertionError(f'Risk action {index} expected {expected}, received {requests}')
            action['requests'] = requests.total()
            action['valuations'] = sum(e['event'] == 'portfolio_valued' for e in during)
            action['calculations'] = sum(e['event'] == 'risk_calculated' for e in during)
            if action['calculations'] != (1 if index == 0 or variant == 'reference' else 0):
                raise AssertionError(f'Unexpected risk calculation count on action {index}: {action}')
            if action['holdings'] != len(risk_listings) - 1 or any(value in ('', '—') for value in action['metrics']):
                raise AssertionError('Incomplete risk estimates or correlation matrix')
        return report
    if scenario == 'exposure':
        if visit := report['fundamentals']:
            start, end = visit.pop('event_range')
            during = events[start:end]
            with (workspace / 'holdings.csv').open(newline='', encoding='utf-8') as source:
                expected = {row['ticker'] for row in csv.DictReader(source)}
            fetched = Counter(e['ticker'] for e in during if e['event'] == 'fundamental_fetched')
            outside = [e['ticker'] for e in [*events[:start], *events[end:]] if e['event'] == 'fundamental_fetched']
            if fetched != Counter(expected) or outside:
                raise AssertionError(f'Fundamental requests differ: missing={sorted(expected - fetched.keys())}, '
                                     f'unexpected={sorted(fetched.keys() - expected)}, '
                                     f'repeated={[key for key, count in fetched.items() if count > 1]}, outside={outside}')
            visit['requests'] = fetched.total()
            visit['valuations'] = sum(e['event'] == 'portfolio_valued' for e in during)
            if variant == 'candidate' and visit['valuations'] > 2:
                raise AssertionError('Partial fundamentals repeatedly revalued the mounted workspace')
        for visit in report['details']:
            visit['valuations'] = sum(e['event'] == 'portfolio_valued' for e in interval(visit))
            if variant == 'candidate' and visit['valuations']:
                raise AssertionError(f"Read-only Exposure detail revalued the portfolio {visit['valuations']} times")
        return report
    if 'start_event' in report:
        start, end = report.pop('start_event'), report.pop('end_event')
        field = {'quote-pending': 'pending_quote_valuations', 'etf-open-failed': 'open_panel_valuations'}.get(
            scenario, 'idle_valuations')
        report[field] = sum(e['event'] == 'portfolio_valued' for e in events[start:end])
        if report[field]:
            raise AssertionError(f'{scenario} revalued the portfolio {report[field]} times before expected')
    if 'publication_start_event' in report:
        start = report.pop('publication_start_event')
        report['publication_valuations'] = sum(e['event'] == 'portfolio_valued' for e in events[start:])
        if report['publication_valuations'] != 1:
            raise AssertionError(f"Completed quote batch triggered {report['publication_valuations']} valuations")
    report['history_calls'] = sum(e['event'] == 'history_calculated' for e in events)
    interactions = [report['first'], report['range_3m'], *report['warm']]
    if 'post_notice_navigation' in report:
        interactions.append(report['post_notice_navigation'])
    for interaction in interactions:
        dispatched = interaction.pop('dispatch_ms')
        specific = interval(interaction)
        if interaction is report['first']:
            report['first_chart_disk_hits'] = sum(e['event'] == 'disk_hit' and e['chart'] for e in specific)
        interaction['history_compute_ms'] = sum(e['duration_ms'] for e in specific if e['event']=='history_calculated')
        interaction['valuations'] = sum(e['event'] == 'portfolio_valued' for e in specific)
        interaction['journal_fingerprints'] = sum(e['event'] == 'journal_fingerprinted' for e in specific)
        sends = [e for e in specific if e['event']=='chart_enqueued']
        if sends:
            interaction['first_chart_enqueue_ms'] = sends[0]['monotonic_ms'] - dispatched
    post_notice = report.get('post_notice_navigation')
    if post_notice and post_notice['valuations']:
        raise AssertionError(f'Navigating after the ETF notice repeated valuation: {post_notice}')
    return report


def run(executable, destination, surfaces, pairs, experiment='history', profile='small'):
    destination.mkdir(parents=True, exist_ok=False)
    variants = ('navigation-reference', 'navigation') if experiment == 'navigation' else ('baseline', 'persistent')
    as_of = date.today()
    result = dict(profile=profile, as_of=as_of.isoformat(), experiment=experiment, platform=sys.platform, cpu=platform.machine(), revision=os.environ.get('GITHUB_SHA'),
        runner_image=os.environ.get('ImageVersion'), python=platform.python_version(), runs=[])
    binary = hashlib.sha256(executable.read_bytes()).hexdigest()
    result['binary_sha256'] = binary
    expected = {}
    for surface in surfaces:
        for pair in range(pairs):
            order = variants if pair % 2 == 0 else reversed(variants)
            for variant in order:
                with TemporaryDirectory(prefix='portfolio-benchmark-') as tmp:
                    workspace = Path(tmp) / 'synthetic'
                    create_workspace(workspace, profile, today=as_of)
                    for scenario, launch in [('unchanged','cold'), ('changed','reopen')]:
                        trial = Path(tmp) / launch
                        metrics = benchmark(executable, workspace, trial, variant, surface, scenario)
                        if variant != 'baseline' and launch == 'reopen' and metrics['first_chart_disk_hits'] == 0:
                            raise AssertionError('Relaunch did not reuse persisted history')
                        if variant == 'navigation' and metrics['range_3m']['journal_fingerprints']:
                            raise AssertionError('Changing the chart range re-fingerprinted the same journal')
                        for switch in metrics['warm']:
                            if variant == 'navigation' and (switch['valuations'] or switch['journal_fingerprints']):
                                raise AssertionError(f'Unchanged navigation repeated financial work: {switch}')
                            if variant == 'navigation-reference' and not switch['valuations']:
                                raise AssertionError('Reference switch missed the normal valuation path')
                        if metrics['digest'] != expected.setdefault(scenario, metrics['digest']):
                            raise AssertionError('Rendered series or gain data differs across runs')
                        result['runs'].append(dict(surface=surface, pair=pair, variant=variant, launch=launch, **metrics))
                        (destination / 'results.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
                        print(surface, pair, variant, launch, 'first chart ms', round(metrics['first']['chart_ms']),
                              'warm median ms', round(median(r['chart_ms'] for r in metrics['warm'])), flush=True)

        if experiment == 'navigation' and profile == 'mixed-history':
            checks = result.setdefault('background_checks', [])
            for scenario in ('etf-checked', 'etf-failed', 'quote-pending', 'etf-open-failed'):
                with TemporaryDirectory(prefix='portfolio-refresh-benchmark-') as tmp:
                    workspace = Path(tmp) / 'synthetic'
                    create_workspace(workspace, profile, today=as_of)
                    if scenario == 'quote-pending':
                        prepare_quote_check(workspace)
                    else:
                        prepare_fund_check(workspace)
                    metrics = benchmark(executable, workspace, Path(tmp) / 'trial', 'navigation', surface, scenario)
                    if metrics['digest'] != expected['unchanged']:
                        raise AssertionError('Provider check altered the rendered historical series')
                    checks.append(dict(surface=surface, scenario=scenario, **metrics))
                    (destination / 'results.json').write_text(json.dumps(result, indent=2), encoding='utf-8')

    rows = ['| Surface | Launch | Reuse | Launch to ready (ms) | First chart (ms) | Warm chart median / p95 (ms) |',
            '|---|---|---|---:|---:|---:|']
    for surface in surfaces:
        for launch in ('cold','reopen'):
            for variant in variants:
                runs = [r for r in result['runs'] if (r['surface'],r['launch'],r['variant']) == (surface, launch, variant)]
                warm = sorted(w['chart_ms'] for r in runs for w in r['warm'])
                rows.append(f"| {surface} | {launch} | {variant} | {median(r['launch_ms'] for r in runs):.0f} | "
                            f"{median(r['first']['chart_ms'] for r in runs):.0f} | {median(warm):.0f} / {warm[ceil(.95*len(warm))-1]:.0f} |")
    summary = f'Portfolio profile: {profile}\n\n' + '\n'.join(rows) + '\n'
    print(summary)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as handle:
            handle.write(summary)


def check_exposure(report, expected, surface, profile):
    for visit in report['visits']:
        sample = visit.pop('sample')
        if sample != expected.setdefault(surface, sample) or visit['rows'] != (12 if profile == 'small' else 100):
            raise AssertionError('Rendered Exposure rows or expanded preview changed')
    for visit in report['details']:
        digest = hashlib.sha256(visit.pop('sample').encode()).hexdigest()
        if digest != expected.setdefault((surface, 'detail'), digest):
            raise AssertionError('Financial source details changed across calls or binaries')
        visit['digest'] = digest
    if report['companies']:
        expected_sources = 65624 if profile == 'multi-account' else 16424
        if report['companies'][-1]['source_rows'] != expected_sources:
            raise AssertionError('Company disclosure lost direct or account-specific fund contributions')
    for visit in report['companies']:
        sample = visit.pop('sample')
        if visit['companies'] != 8950 or len(sample) != 3 or any(len(row) != 6 for row in sample):
            raise AssertionError('Company grid omitted synthetic issuers or financial fields')
        # Different summation algorithms can differ below display precision.
        cells = [[name, *(round(float(value), 8) for value in values)] for name, *values in sample]
        digest = hashlib.sha256(json.dumps(cells).encode()).hexdigest()
        if digest != expected.setdefault((surface, 'companies'), digest):
            raise AssertionError('Company names, values or denominators changed across visits or binaries')
        visit['digest'] = digest
    for visit in report['themes']:
        labels, values = visit.pop('sample')
        digest = hashlib.sha256(json.dumps([labels, values]).encode()).hexdigest()
        if len(labels) != len(values) or not {f'Invented sector {i}' for i in range(24)} <= set(labels):
            raise AssertionError('Sector chart omitted labels or values')
        if digest != expected.setdefault((surface, 'themes'), digest):
            raise AssertionError('Rendered sector chart changed across visits or binaries')
        visit['digest'] = digest


def run_comparison(candidate, reference, destination, surfaces, pairs, profile, experiment):
    destination.mkdir(parents=True, exist_ok=False)
    result = dict(experiment=experiment, profile=profile, platform=sys.platform, revision=os.environ.get('GITHUB_SHA'),
                  binaries={name: hashlib.sha256(path.read_bytes()).hexdigest()
                            for name, path in [('reference', reference), ('candidate', candidate)]}, runs=[])
    as_of = date.today()
    expected = {}
    for surface in surfaces:
        for pair in range(pairs):
            order = [('reference', reference), ('candidate', candidate)]
            for name, exe in (reversed(order) if pair % 2 else order):
                with TemporaryDirectory(prefix=experiment + '-benchmark-') as tmp:
                    workspace = Path(tmp) / 'synthetic'
                    create_workspace(workspace, profile, today=as_of)
                    report = benchmark(exe, workspace, Path(tmp) / 'trial', name, surface, experiment,
                                       themes=profile == 'mixed-history', company_accounts=profile == 'multi-account')
                    if experiment == 'risk':
                        for action in report['actions']:
                            sample = (action['metrics'], action['digest'])
                            if sample != expected.setdefault(surface, sample):
                                raise AssertionError('Risk metrics or correlations changed across actions or binaries')
                        print(surface, name, 'risk chart ms', [round(a['chart_ms']) for a in report['actions']], flush=True)
                    else:
                        check_exposure(report, expected, surface, profile)
                        print(surface, name, 'Exposure table ms', [round(v['table_ms']) for v in report['visits']],
                              'sector chart ms', [round(v['chart_ms']) for v in report['themes']],
                              'company grid ms', [round(v['grid_ms']) for v in report['companies']], flush=True)
                    result['runs'].append(dict(variant=name, surface=surface, pair=pair, **report))
                    (destination / 'results.json').write_text(json.dumps(result, indent=2), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--surface', choices=['native','browser','both'], default='both')
    parser.add_argument('--pairs', type=int, choices=range(1,11), default=2)
    parser.add_argument('--experiment', choices=['history', 'navigation', 'exposure', 'risk'], default='history')
    parser.add_argument('--profile', choices=['small', 'etf-heavy', 'mixed-history', 'multi-account'], default='small')
    parser.add_argument('--reference', type=Path, help='Reference bundle for a paired comparison')
    options = parser.parse_args()
    surfaces = ['native','browser'] if options.surface == 'both' else [options.surface]
    if options.experiment in {'exposure', 'risk'}:
        if not options.reference:
            parser.error('A paired binary comparison requires --reference')
        if options.experiment == 'risk' and options.profile == 'small':
            parser.error('The risk benchmark requires a profile with listed equities and ETFs')
        run_comparison(options.executable.resolve(), options.reference.resolve(), options.output.resolve(), surfaces,
                       options.pairs, options.profile, options.experiment)
    else:
        run(options.executable.resolve(), options.output.resolve(), surfaces, options.pairs, options.experiment, options.profile)
