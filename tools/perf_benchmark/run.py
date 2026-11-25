"""Paired cold and reopened desktop/browser measurements using invented portfolios."""
import argparse
from collections import Counter
import csv
import hashlib
import json
import os
from pathlib import Path
from datetime import date, timedelta
import platform
import socket
from math import ceil, isclose
from statistics import median
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

from fixture import create_workspace, prepare_fund_check, prepare_fundamental_check, prepare_market_chart, prepare_quote_check, prepare_risk_check
from renderer import measure_view, wait


def benchmark(executable, workspace, trial, variant, surface, scenario, *, themes=False, company_accounts=False):
    trial.mkdir()
    if scenario == 'market-chart':
        markets = prepare_market_chart(workspace)
        (trial / 'market-chart.json').write_text(json.dumps(markets), encoding='utf-8')
    if scenario == 'checklist':
        with (workspace / 'holdings.csv').open(newline='', encoding='utf-8') as handle:
            positions = [row['position_key'] for row in csv.DictReader(handle)]
        (trial / 'checklist-ids.json').write_text(json.dumps(positions[::4]), encoding='utf-8')
    if scenario == 'exposure' and (themes or company_accounts):
        prepare_fundamental_check(workspace, expired=themes)
    if scenario == 'collection':
        prepare_fundamental_check(workspace, expired=False)
    if scenario.startswith('risk'):
        risk_listings = prepare_risk_check(workspace, scenario=scenario)
    env = dict(os.environ, PORTFOLIO_STATE_DIR=str(workspace.parent / 'state'),
        PORTFOLIO_BENCHMARK_TRIAL=str(trial), PORTFOLIO_BENCHMARK_VARIANT=variant,
        PORTFOLIO_BENCHMARK_REFRESH=scenario, PORTFOLIO_BENCHMARK_THEMES=str(int(themes)),
        PORTFOLIO_BENCHMARK_COMPANY_ACCOUNTS=str(int(company_accounts)))
    if scenario == 'collection':
        if variant == 'reference':
            env['STREAMLIT_RUNNER_POST_SCRIPT_GC'] = 'true'
        else:
            env.pop('STREAMLIT_RUNNER_POST_SCRIPT_GC', None)  # Exercise the shipped default.
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
                # The four-account trial has two extra chart and table waits of up to 60 seconds each.
                code = proc.wait(timeout=360 if scenario == 'exposure' and company_accounts else 240)
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

    if scenario in {'cash-history', 'long-history'}:
        from dateutil.relativedelta import relativedelta
        settings = json.loads((workspace.parent / 'cash-history.json').read_bytes())
        start, end = (date.fromisoformat(settings[key]) for key in ('start', 'end'))
        long_history = scenario == 'long-history'
        if long_history:
            report['estimated_purchases'] = settings['estimated_purchases']
        for i, action in enumerate(report['actions']):
            action.pop('dispatch_ms')
            during = interval(action)
            charts = [e for e in during if e['event'] == 'cash_history_calculated' and e['points']]
            counts = [e['positions'] for e in charts]
            if ((i == 0 and not charts) or (i == len(report['actions'])-1 and charts) or
                    sum(counts) > settings['positions'] * (settings['purchases'] + 3)):
                raise AssertionError(f'Unexpected position capital work on {action["period"]}: {counts}')
            action['position_capital_calculations'] = counts
            first = (start - timedelta(days=1) if action['period'] == 'All' else
                     end - relativedelta(months=12 if action['period'] == '1Y' else 3))
            points = action.pop('points')
            if len(points) != (end-first).days+1:
                raise AssertionError(f'Incomplete cash-history range: {action["period"]}, {len(points)} closes')
            if long_history:
                # The first visit may build the index and qualify its summary.
                # Later ranges reuse that index. A cached chart needs no checks.
                estimates = settings['estimated_purchases']
                checks = sum(chart['estimate_checks'] for chart in charts)
                if i == 0 and not checks:
                    raise AssertionError('The cold chart did not exercise the estimated purchase probe')
                if checks > estimates * (2 if i == 0 else 1):
                    raise AssertionError(f'{action["period"]}: {checks} scope checks for {estimates} estimated purchases')
                if any(not chart['estimated_gain'] for chart in charts):
                    raise AssertionError(f'{action["period"]}: gain lost its estimate qualification')
                action['estimated_purchase_checks'] = checks
                closes = sum(e['closes'] for e in charts)
                if i > 0 and closes > len(points) + 2:
                    raise AssertionError(f'{action["period"]} recalculated {closes} portfolio closes for {len(points)} dates')
                action['portfolio_closes_calculated'] = closes
                member_closes = sum(e['member_closes'] for e in charts)
                # Allow individual opening and endpoint checks. A daily replay of
                # each member grows with both chart length and portfolio size.
                if member_closes > 4 * (settings['positions'] + settings['accounts']):
                    raise AssertionError(f'{action["period"]} calculated {member_closes} individual member closes')
                action['member_closes_calculated'] = member_closes
                valuations = sum(e['valuations'] for e in charts)
                fx_lookups = sum(e['fx_lookups'] for e in charts)
                if fx_lookups > valuations * len(set(settings['quote_currencies']) | {'EUR'}):
                    raise AssertionError(f'{action["period"]} performed {fx_lookups} FX lookups across {valuations} valuations')
                action['fx_lookups'] = fx_lookups
            for offset, (stamp, actual) in enumerate(points):
                day = first + timedelta(days=offset)
                if stamp[:10] != day.isoformat():
                    raise AssertionError(f'Expected cash-history date {day}, received {stamp}')
                t = (day-start).days
                if t < 0:
                    if actual is not None:
                        raise AssertionError('The combined balance before the first cash opening must be unknown')
                    continue
                balance = settings['accounts'] * (100 + (t//30 if long_history else t))
                # Calculate the expected EUR balance independently from the app.
                alternate = (t//30) % 2
                rates = {'EUR': 1., 'USD': .90 if alternate else .80, 'GBP': 1/(.75 if alternate else .80)}
                for index, close in enumerate(settings['closing_prices']):
                    stagger = index % 12 if long_history else 0
                    shares = 1 + max(0, (t-stagger)//30)
                    rate = rates[settings['quote_currencies'][index]] if long_history else 1.
                    balance += shares * (100 + (close-100) * t / (end-start).days) * rate
                if actual is None or not isclose(actual, balance, rel_tol=1e-12):
                    raise AssertionError(f'Incorrect cash-history close for {day}: {actual}, expected {balance}')
            action['closes'] = len(points)
        return report
    if scenario == 'purchase-fx':
        settings = json.loads((workspace.parent / 'purchase-fx.json').read_bytes())
        requests = Counter((e['currency'], e['day']) for e in events if e['event'] == 'purchase_fx_requested')
        expected_requests = Counter(('USD', day) for day in settings['days'])
        if requests != expected_requests or any(e['event'] == 'unexpected_listing' for e in events):
            raise AssertionError(f'Unexpected FX or listing requests: {requests}')
        split = report.pop('publication_start')
        initial = [e for e in events[:split] if e['event'] == 'purchase_costs']
        published = [e for e in events[split:] if e['event'] == 'purchase_costs']
        if not initial or any(e['covered'] for e in initial) or not published:
            raise AssertionError(f'Expected unknown acquisition costs until release: {initial}, {published}')
        final = published[-1]
        if (final['covered'] != settings['positions'] or final['total'] != settings['basis'] or
                final['distinct'] != [settings['basis'] / settings['positions']]):
            raise AssertionError(f'Completed purchase costs differ from fixture: {published[-1]}')
        first = report['first']
        first.pop('dispatch_ms')
        first['valuations'] = sum(e['event'] == 'portfolio_valued' for e in interval(first))
        report.update(published_valuations=len(published), requests=requests.total())
        if (first['valuations'] or len(published) != 1 or report['refresh']['fades'] > 1 or
                report['refresh']['changes'] or report['refresh']['replaced'] or not report['refresh']['preserved']):
            raise AssertionError(f'Purchase FX refresh repeated or interrupted chart work: {report}')
        if report['closing'] != settings['closing'] or report['end'][:10] != settings['end'] or report['points'] not in (366, 367):
            raise AssertionError(f'Incomplete or incorrect historical chart: {report}')
        return report
    if scenario == 'market-chart':
        if any(e['event'] in {'unexpected_market_fetch', 'unexpected_market_network'} for e in events):
            raise AssertionError('Fresh saved market data triggered a provider or network request')
        seen = set()
        for action in report['actions']:
            during = interval(action)
            action['file_opens'] = sum(e['event'] == 'history_file_opened' for e in during)
            action['conversion_ms'] = [e['elapsed_ms'] for e in during if e['event'] == 'converted']
            period = action['period']
            if variant == 'candidate' and action['file_opens'] != (0 if period in seen else 2):
                raise AssertionError(f'Unexpected saved price or FX file reads: {action}')
            seen.add(period)
        return report
    if scenario == 'checklist':
        for index, action in enumerate(report['actions']):
            action['calculations'] = sum(e['event'] == 'checklist_calculated' for e in interval(action))
            if variant == 'candidate' and action['calculations'] != int(index == 0):
                raise AssertionError('A fresh checklist should calculate once and reuse the result on return visits')
        return report
    if scenario == 'collection':
        if attempted := [e['ticker'] for e in events if e['event'] == 'unexpected_listing']:
            raise AssertionError(f'Unexpected live listing requests: {attempted}')
        reference = variant == 'reference'
        for action in report['actions']:
            action.pop('dispatch_ms')
            during = interval(action)
            cleanup = during[-1]
            if (cleanup['event'] != 'script_finished' or not cleanup['automatic'] or
                    cleanup['policy'] != reference or cleanup['forced'] != int(reference)):
                raise AssertionError(f'Unexpected server cleanup after switching views: {cleanup}')
            if cleanup['observation_parses'] != 1:
                raise AssertionError('Collector comparison requires a single shared parse of the history fixture')
            action['forced_collections'] = cleanup['forced']
            action['cleanup_collections'] = cleanup['cleanup_collections']
            if any(e['event'] == 'portfolio_valued' for e in during):
                raise AssertionError('Overview switch unexpectedly revalued the portfolio')
        return report
    if scenario.startswith('risk'):
        staged = scenario == 'risk-stale' and variant == 'candidate'
        data_actions = 2 if staged else 1
        fetched = Counter()
        for index, action in enumerate(report['actions']):
            during = interval(action)
            requests = Counter(e['ticker'] for e in during if e['event'] == 'adjusted_fetched')
            if index >= data_actions and requests:
                raise AssertionError('Resizing the risk matrix fetched adjusted histories')
            fetched.update(requests)
            action['requests'] = requests.total()
            action['valuations'] = sum(e['event'] == 'portfolio_valued' for e in during)
            if index >= data_actions and variant == 'candidate' and action['valuations']:
                raise AssertionError('Resizing a current risk matrix revalued the portfolio')
            action['calculations'] = sum(e['event'] == 'risk_calculated' for e in during)
            if action['calculations'] != (1 if index < data_actions or variant == 'reference' else 0):
                raise AssertionError(f'Unexpected risk calculation count on action {index}: {action}')
            if action['holdings'] != len(risk_listings) - 1 or any(value in ('', '—') for value in action['metrics']):
                raise AssertionError('Incomplete risk estimates or correlation matrix')
            if variant == 'candidate' and action['label_bytes'] > 1024 * action['holdings']:
                raise AssertionError('Risk matrix label payload exceeded 1 KiB per instrument')
        if scenario == 'risk-cold':
            edges = []
            for event in events:
                if event['event'] == 'adjusted_fetched':
                    edges.extend(((event['started_ms'], 1), (event['monotonic_ms'], -1)))
            active = peak = 0
            for _, change in sorted(edges):
                active += change
                peak = max(peak, active)
            report['peak_provider_requests'] = peak
            if variant == 'candidate' and not 2 <= peak <= 3:
                raise AssertionError(f'Expected bounded overlap of cold provider requests, observed {peak}')
        expected = risk_listings[-1:] if scenario == 'risk' and variant == 'candidate' else risk_listings
        if fetched != Counter(expected):
            raise AssertionError(f'Unexpected adjusted history requests: {fetched}')
        return report
    if scenario == 'exposure':
        for visit in report['main_tabs']:
            during = interval(visit)
            visit['journal_reads'] = sum(e['event'] == 'journal_read' for e in during)
            if variant == 'candidate' and visit['journal_reads'] > 1:
                raise AssertionError(f"Repeated journal reads while navigating to {visit['name']}")
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
            for scenario in ('etf-checked', 'etf-failed', 'quote-pending', 'etf-open-failed', 'purchase-fx'):
                with TemporaryDirectory(prefix='portfolio-refresh-benchmark-') as tmp:
                    workspace = Path(tmp) / 'synthetic'
                    create_workspace(workspace, profile, today=as_of, purchase_fx=scenario == 'purchase-fx')
                    if scenario == 'quote-pending':
                        prepare_quote_check(workspace)
                    elif scenario.startswith('etf-'):
                        prepare_fund_check(workspace)
                    metrics = benchmark(executable, workspace, Path(tmp) / 'trial', 'navigation', surface, scenario)
                    key = 'purchase-fx' if scenario == 'purchase-fx' else 'unchanged'
                    digest = expected.setdefault(key, metrics['digest'])
                    if metrics['digest'] != digest:
                        raise AssertionError('Provider check altered the rendered historical series')
                    checks.append(dict(surface=surface, scenario=scenario, **metrics))
                    (destination / 'results.json').write_text(json.dumps(result, indent=2), encoding='utf-8')

            for scenario in ('cash-history', 'long-history'):
                with TemporaryDirectory(prefix='portfolio-cash-history-') as tmp:
                    workspace = Path(tmp) / 'synthetic'
                    create_workspace(workspace, profile, today=as_of, cash_history=True, long_history=scenario == 'long-history')
                    metrics = benchmark(executable, workspace, Path(tmp) / 'trial', 'navigation', surface, scenario)
                    for action in metrics['actions']:
                        key = (scenario, action['period'])
                        if action['digest'] != expected.setdefault(key, action['digest']):
                            raise AssertionError('Account-history series differed across ranges or renderers')
                    result.setdefault(scenario.replace('-', '_'), []).append(dict(surface=surface, **metrics))
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
    for check in result.get('background_checks', []):
        if check['scenario'] == 'purchase-fx':
            summary += (f"\n{check['surface']} purchase-date FX: first Performance paint "
                        f"{check['first']['chart_ms']:.0f} ms, completed costs {check['publication_ms']:.0f} ms "
                        f"after release, {check['requests']} FX dates, {check['published_valuations']} valuation(s), "
                        f"{check['refresh']['fades']} chart fade(s).\n")
    for check in result.get('cash_history', []):
        first, quarter, repeat = check['actions']
        summary += (f"\n{check['surface']} four-account history: cold Performance paint {first['chart_ms']:.0f} ms, "
                    f"3M {quarter['chart_ms']:.0f} ms, repeat 1Y {repeat['chart_ms']:.0f} ms.\n")
    for check in result.get('long_history', []):
        actions = ', '.join(f"{a['period']} {a['chart_ms']:.0f}/{a['settled_ms']:.0f} ms" for a in check['actions'])
        all_days = next(action for action in check['actions'] if action['period'] == 'All')
        summary += (f"\n{check['surface']} ten-year history paint/settle: {actions}. "
                    f"The All chart used {all_days['portfolio_closes_calculated']} whole-portfolio and "
                    f"{all_days['member_closes_calculated']} single-member valuations with {all_days['fx_lookups']} "
                    f"FX lookups and {all_days['estimated_purchase_checks']} scope checks on {check['estimated_purchases']} estimated purchases "
                    f"for {all_days['closes']} dates.\n")
    print(summary)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as handle:
            handle.write(summary)


def check_exposure(report, expected, surface, profile):
    for visit in report['main_tabs']:
        digest = hashlib.sha256(json.dumps(visit.pop('sample'), ensure_ascii=False).encode()).hexdigest()
        if digest != expected.setdefault((surface, visit['name']), digest):
            raise AssertionError(f"Displayed {visit['name']} data changed between visits or binaries")
        visit['digest'] = digest
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
    dimensions = [item for visit in report['themes'] for item in visit.get('dimensions', [])]
    if profile == 'multi-account' and sum(visit.get('show_tickers', False) for visit in report['themes']) != 1:
        raise AssertionError('Four-account comparison missed the ticker-enabled sector chart')
    if profile == 'multi-account' and [item['name'] for item in dimensions] != ['Account', 'Investment']:
        raise AssertionError('Four-account comparison missed a grouping')
    for visit in report['themes']:
        labels, values = visit.pop('sample')
        digest = hashlib.sha256(json.dumps([labels, values]).encode()).hexdigest()
        if len(labels) != len(values) or not {f'Invented sector {i}' for i in range(24)} <= set(labels):
            raise AssertionError('Sector chart omitted labels or values')
        if digest != expected.setdefault((surface, 'themes'), digest):
            raise AssertionError('Rendered sector chart changed across visits or binaries')
        visit['digest'] = digest

    for dimension in dimensions:
        name = dimension['name']
        labels, values = dimension.pop('sample')
        if name == 'Account':
            valid = dimension['rows'] == 4 and set(labels) == {
                'Selected holdings', *(f'Invented account {i}' for i in range(1, 5))}
        else:
            valid = name == 'Investment' and dimension['rows'] == 8958 and len(labels) == 30
        if not valid or len(labels) != len(values):
            raise AssertionError(f'{name} chart or financial table is incomplete')
        digest = hashlib.sha256(json.dumps([labels, values]).encode()).hexdigest()
        if digest != expected.setdefault((surface, name), digest):
            raise AssertionError(f'{name} chart labels or amounts changed across binaries')
        dimension['digest'] = digest


def check_comparison(report, expected, *, surface, name, profile, experiment):
    if experiment == 'market-chart':
        print(surface, name, 'market chart first and repeat paint ms',
              [round(a['paint_ms']) for a in report['actions']], flush=True)
    elif experiment == 'checklist':
        for action in report['actions']:
            if action['digest'] != expected.setdefault(surface, action['digest']):
                raise AssertionError('Checklist cells or diagnostic tooltips differ across visits or binaries')
        print(surface, name, 'first and repeat checklist paint ms',
              [round(a['paint_ms']) for a in report['actions']], flush=True)
    elif experiment == 'collection':
        if report['digest'] != expected.setdefault(surface, report['digest']):
            raise AssertionError('Collection policy changed displayed financial data')
        print(surface, name, 'median settled ms', round(median(a['settled_ms'] for a in report['actions'])),
              'server MiB', [round(b / 2**20) for b in report['server_resident_bytes']], flush=True)
    elif experiment.startswith('risk'):
        actions = report['actions']
        if experiment == 'risk-stale' and name == 'candidate':
            if actions[0]['digest'] == actions[1]['digest']:
                raise AssertionError('Corrected history did not change the displayed risk matrix')
            actions = actions[1:]
        for action in actions:
            sample = (action['metrics'], action['digest'])
            if sample != expected.setdefault(surface, sample):
                raise AssertionError('Risk metrics or correlations changed across actions or binaries')
        print(surface, name, 'risk chart ms', [round(a['chart_ms']) for a in report['actions']], flush=True)
    else:
        check_exposure(report, expected, surface, profile)
        print(surface, name, 'Exposure table ms', [round(v['table_ms']) for v in report['visits']],
              'sector chart ms', [round(v['chart_ms']) for v in report['themes']],
              'company grid ms', [round(v['grid_ms']) for v in report['companies']], flush=True)
        for visit in report['themes']:
            if visit.get('show_tickers'):
                print(surface, name, 'ticker-enabled sector paint / settled ms',
                      round(visit['chart_ms']), round(visit['settled_ms']), flush=True)
            for dimension in visit.get('dimensions', []):
                print(surface, name, dimension['name'], 'paint / settled ms',
                      round(dimension['chart_ms']), round(dimension['settled_ms']), flush=True)


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
                    create_workspace(workspace, profile, today=as_of, checklist=experiment == 'checklist',
                                     large_risk_matrix=experiment == 'risk' and profile == 'mixed-history')
                    trial = Path(tmp) / 'trial'
                    identity = dict(variant=name, surface=surface, pair=pair)
                    print('Starting', experiment, surface, name, 'pair', pair + 1, flush=True)
                    try:
                        report = benchmark(exe, workspace, trial, name, surface, experiment,
                                           themes=profile == 'mixed-history', company_accounts=profile == 'multi-account')
                        trial_expected = expected.copy()
                        check_comparison(report, trial_expected, surface=surface, name=name,
                                         profile=profile, experiment=experiment)
                    except Exception as error:
                        failure = dict(**identity, error=str(error))
                        detail = trial / 'market-chart-failure.json'
                        if detail.is_file():
                            failure['market_chart'] = json.loads(detail.read_text(encoding='utf-8'))
                        result.setdefault('failures', []).append(failure)
                        print('Failed', experiment, surface, name, 'pair', pair + 1, str(error), flush=True)
                    else:
                        expected = trial_expected
                        result['runs'].append(dict(**identity, **report))
                    (destination / 'results.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    if result.get('failures'):
        raise RuntimeError(f"{len(result['failures'])} comparison trial(s) failed; see {destination / 'results.json'}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--surface', choices=['native','browser','both'], default='both')
    parser.add_argument('--pairs', type=int, choices=range(1,11), default=2)
    parser.add_argument('--experiment', choices=['history', 'navigation', 'exposure', 'risk', 'risk-cold', 'risk-stale', 'collection', 'checklist', 'market-chart'], default='history')
    parser.add_argument('--profile', choices=['small', 'etf-heavy', 'mixed-history', 'multi-account'], default='small')
    parser.add_argument('--reference', type=Path, help='Reference bundle for a paired comparison')
    options = parser.parse_args()
    surfaces = ['native','browser'] if options.surface == 'both' else [options.surface]
    if options.experiment == 'collection':
        if options.profile == 'small':
            parser.error('The collection benchmark exercises the ETF look-through company grid')
        run_comparison(options.executable.resolve(), options.executable.resolve(), options.output.resolve(), surfaces,
                       options.pairs, options.profile, options.experiment)
    elif options.experiment in {'exposure', 'risk', 'risk-cold', 'risk-stale', 'checklist', 'market-chart'}:
        if not options.reference:
            parser.error('A paired binary comparison requires --reference')
        if (options.experiment.startswith('risk') or options.experiment == 'market-chart') and options.profile == 'small':
            parser.error('This benchmark requires a profile with listed equities and ETFs')
        run_comparison(options.executable.resolve(), options.reference.resolve(), options.output.resolve(), surfaces,
                       options.pairs, options.profile, options.experiment)
    else:
        run(options.executable.resolve(), options.output.resolve(), surfaces, options.pairs, options.experiment, options.profile)
