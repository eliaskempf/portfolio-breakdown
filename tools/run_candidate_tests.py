"""Run exhaustive tests with isolated units/latency and two balanced browser workers."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


def aggregate_junit(directory, results, groups, destination):
    root = ET.Element('testsuites')
    case_counts = {}
    def error(name, message):
        suite = ET.SubElement(root, 'testsuite', name=name+'-process', tests='1', errors='1', failures='0', skipped='0', time='0')
        case = ET.SubElement(suite, 'testcase', name='worker evidence', classname='candidate_runner')
        ET.SubElement(case, 'error', message=message)

    for name in groups:
        code = results.get(name)
        path = directory / (name + '.xml')
        suites = []
        try:
            document = ET.parse(path).getroot()
            suites = [document] if document.tag == 'testsuite' else list(document)
            if document.tag not in ('testsuite', 'testsuites') or not suites or any(s.tag != 'testsuite' for s in suites):
                raise ValueError('Expected JUnit testsuites')
            for suite in suites:
                if int(suite.get('tests', '0')) != len(list(suite.iter('testcase'))):
                    raise ValueError('JUnit tests summary differs from actual testcase evidence')
                # A failure element must never disappear because a producer has
                # emitted an inconsistent summary attribute.
                for key, element in (('failures', 'failure'), ('errors', 'error')):
                    suite.set(key, str(max(int(suite.get(key, '0')), len(list(suite.iter(element))))))
                for key in ('tests', 'skipped'):
                    if int(suite.get(key, '0')) < 0:
                        raise ValueError('Invalid negative JUnit count')
                float(suite.get('time', '0'))
            root.extend(suites)
            case_counts[name] = sum(len(list(s.iter('testcase'))) for s in suites)
        except (OSError, ET.ParseError, ValueError) as exc:
            suites = []
            error(name, f'Missing or invalid JUnit evidence: {exc}')
        if code is None:
            error(name, 'Missing worker result')
        elif code != 0 and not any(int(s.get('failures', 0)) + int(s.get('errors', 0)) for s in suites):
            error(name, f'{name} exited {code}; inspect its log')
    if set(results) - set(groups):
        error('unexpected', 'Results contain an unexpected worker')
    for key in ('tests', 'errors', 'failures', 'skipped'):
        root.set(key, str(sum(int(s.get(key, '0')) for s in root)))
    root.set('time', str(sum(float(s.get('time', '0')) for s in root)))
    destination.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(destination, encoding='utf-8', xml_declaration=True)
    return dict(root.attrib, case_counts=case_counts)


def final_evidence(output, groups, results, canonical, *, source_unchanged, aggregate):
    counts = aggregate_junit(output, results, groups, aggregate)
    actual, skips, collection_errors = [], [], []
    for name in groups:
        try:
            evidence = json.loads((output/(name+'.collection.json')).read_text())
            actual.extend(evidence['nodeids'])
            skips.extend(evidence['skipped_browsers'])
            if Counter(evidence['executed_nodeids']) != Counter(evidence['nodeids']):
                collection_errors.append(name+': execution evidence differs from collection')
            expected_cases = len(evidence['nodeids']) + len(evidence['collection_skips'])
            if counts['case_counts'].get(name) != expected_cases:
                collection_errors.append(name+': JUnit testcase evidence is incomplete')
        except (OSError, ValueError, KeyError) as exc:
            collection_errors.append(str(exc))
    exhaustive = Counter(actual) == Counter(canonical) and len(actual) == len(set(actual))
    complete = set(results) == set(groups)
    passed = (complete and all(code == 0 for code in results.values()) and exhaustive
              and not skips and not collection_errors and source_unchanged
              and int(counts['errors']) == 0 and int(counts['failures']) == 0)
    return {'exit_codes': results, 'complete_results': complete,
            'expected_tests': len(canonical), 'collected_tests': len(actual),
            'exhaustive_disjoint': exhaustive, 'skipped_browsers': skips, 'collection_errors': collection_errors,
            'source_unchanged': source_unchanged, 'junit': counts, 'status': 'passed' if passed else 'failed'}


class CollectionEvidence:
    def __init__(self, path):
        self.path = path
        self.nodeids = []
        self.skipped_browsers = []
        self.executed_nodeids = []
        self.collection_skips = []

    def pytest_collection_finish(self, session):
        self.nodeids = [item.nodeid for item in session.items]

    def pytest_collectreport(self, report):
        if report.skipped:
            self.collection_skips.append(report.nodeid)
        if report.skipped and report.nodeid.split('::')[0].endswith('browser.py'):
            self.skipped_browsers.append(report.nodeid)

    def pytest_runtest_logreport(self, report):
        if report.when == 'teardown':
            self.executed_nodeids.append(report.nodeid)
        if report.skipped and report.nodeid.split('::')[0].endswith('browser.py'):
            self.skipped_browsers.append(report.nodeid)

    def pytest_sessionfinish(self, session, exitstatus):
        if self.skipped_browsers or not self.nodeids:
            session.exitstatus = 1
        self.path.write_text(json.dumps({'nodeids': self.nodeids, 'skipped_browsers': self.skipped_browsers,
            'executed_nodeids': self.executed_nodeids, 'collection_skips': self.collection_skips}, indent=2))


def child(args):
    import pytest
    plugin = CollectionEvidence(args.output/(args.worker+'.collection.json'))
    if args.worker == 'canonical':
        command = ['--collect-only', '-q']
    else:
        manifest = json.loads((args.output/'manifest.json').read_text())
        command = ['-q', '--durations=20', '--junitxml='+str(args.output/(args.worker+'.xml')),
                   '--basetemp='+str(args.output/(args.worker+'-tmp')), *manifest['groups'][args.worker]]
    return int(pytest.main(command, plugins=[plugin]))


def head(checkout):
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=checkout, text=True).strip()


def clean(checkout):
    return not subprocess.check_output(['git', 'status', '--porcelain'], cwd=checkout, text=True).strip()


def cleanup_processes(processes):
    """Attempt every owned cleanup without masking an earlier worker exception."""
    errors = []
    for name, (process, log) in processes.items():
        try:
            if process.poll() is None:
                try:
                    if os.name == 'nt':
                        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=30, check=True)
                    else:
                        os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                except Exception as exc:
                    errors.append(name+': '+str(exc))
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    if os.name != 'nt':
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        process.kill()
                    process.wait(timeout=10)
        except Exception as exc:
            errors.append(name+': '+str(exc))
        finally:
            try:
                log.close()
            except Exception as exc:
                errors.append(name+': '+str(exc))
    return errors


def start_worker(command, *, cwd, env, log_path):
    log = log_path.open('w')
    try:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=log,
                                   start_new_session=os.name != 'nt')
    except BaseException:
        log.close()
        raise
    return process, log


def build_schedule(files, nodeids):
    """Keep timing-sensitive work alone, then balance browser case counts."""
    files = sorted(files)
    counts = Counter(node.split('::')[0] for node in nodeids)
    if set(counts) - set(files):
        raise ValueError('Canonical collection contains an unassigned test file')
    responsiveness = ['tests/test_market_data_browser.py', 'tests/test_unified_exposure_browser.py']
    browser = [p for p in files if p.endswith('browser.py') and p not in responsiveness]
    groups = {'unit': [p for p in files if not p.endswith('browser.py')],
              'responsiveness': responsiveness, 'browser-0': [], 'browser-1': []}
    if not set(responsiveness) <= set(files):
        raise ValueError('Missing isolated responsiveness test file')
    weights = {'browser-0': 0, 'browser-1': 0}
    for path in sorted(browser, key=lambda path: (-counts[path], path)):
        group = min(weights, key=lambda name: (weights[name], name))
        groups[group].append(path)
        weights[group] += counts[path]
    phases = [['unit'], ['responsiveness'], ['browser-0', 'browser-1']]
    if not all(groups.values()):
        raise ValueError('Expected nonempty unit, responsiveness and browser groups')
    assigned = [path for paths in groups.values() for path in paths]
    if len(assigned) != len(set(assigned)) or sorted(assigned) != files:
        raise ValueError('Test file assignment must be disjoint and exhaustive')
    return groups, phases, dict(counts)


def run_phases(groups, phases, start, output):
    """Finish every worker in a phase before starting the next; never retry."""
    results, timings, processes = {}, {}, {}
    started = time.monotonic()
    try:
        for phase in phases:
            worker_started = {}
            for name in phase:
                process, log = start(name)
                processes[name] = (process, log)
                worker_started[name] = time.monotonic()
                print(json.dumps({'started': name, 'files': len(groups[name]), 'pid': process.pid,
                                  'seconds': round(worker_started[name]-started, 2)}), flush=True)
            while processes:
                for name, (process, log) in list(processes.items()):
                    code = process.poll()
                    if code is not None:
                        results[name] = code
                        timings[name] = time.monotonic()-worker_started[name]
                        log.close()
                        del processes[name]
                        print(json.dumps({'finished': name, 'code': code,
                            'worker_seconds': round(timings[name], 2),
                            'seconds': round(time.monotonic()-started, 2)}), flush=True)
                if processes:
                    time.sleep(1)
    finally:
        errors = cleanup_processes(processes)
        if errors:
            (output/'cleanup-errors.json').write_text(json.dumps(errors))
            if sys.exc_info()[0] is None:
                raise RuntimeError('Worker cleanup failed; inspect evidence')
    return results, timings


def main(args):
    checkout, output = args.checkout.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    expected = head(checkout)
    if (args.expected_sha and expected != args.expected_sha) or not clean(checkout):
        raise RuntimeError('Expected a clean frozen checkout at the supplied SHA')
    # Match pytest's configured test root and default Python file patterns. The
    # canonical collection below independently verifies every collected node ID.
    files = sorted({p.relative_to(checkout).as_posix() for pattern in ('test_*.py', '*_test.py')
                    for p in (checkout/'tests').rglob(pattern)})
    env = dict(os.environ, PORTFOLIO_REQUIRE_BROWSER='1')
    env.pop('VIRTUAL_ENV', None)
    script = str(Path(__file__).resolve())
    def command(name):
        return [sys.executable, script, '--worker', name, '--output', str(output)]
    canonical_process, canonical_log = start_worker(command('canonical'), cwd=checkout, env=env,
                                                  log_path=output/'canonical.log')
    try:
        code = canonical_process.wait(timeout=120)
        if code:
            raise subprocess.CalledProcessError(code, command('canonical'))
    finally:
        errors = cleanup_processes({'canonical': (canonical_process, canonical_log)})
        if errors:
            (output/'canonical-cleanup-errors.json').write_text(json.dumps(errors))
            if sys.exc_info()[0] is None:
                raise RuntimeError('Canonical worker cleanup failed; inspect evidence')
    canonical = json.loads((output/'canonical.collection.json').read_text())
    assert not canonical['skipped_browsers']
    groups, phases, counts = build_schedule(files, canonical['nodeids'])
    manifest = {'source': expected, 'groups': groups, 'phases': phases, 'max_processes': 2,
                'canonical_cases_per_file': counts,
                'mandatory_browser': True, 'all_test_files': files}
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2))
    started = time.monotonic()
    def start(name):
        child_env = dict(env, PORTFOLIO_STATE_DIR=str(output/(name+'-state')),
                         PORTFOLIO_BROWSER_EVIDENCE=str(output/(name+'-browser-evidence')))
        return start_worker(command(name), cwd=checkout, env=child_env, log_path=output/(name+'.log'))
    results, timings = run_phases(groups, phases, start, output)
    unchanged = head(checkout) == expected and clean(checkout)
    result = final_evidence(output, groups, results, canonical['nodeids'],
                            source_unchanged=unchanged, aggregate=args.junit.resolve())
    result.update(source=expected, seconds=time.monotonic()-started, worker_seconds=timings)
    (output/'results.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, default=ROOT/'dist/candidate-tests')
    parser.add_argument('--junit', type=Path, default=ROOT/'dist/test-results.xml')
    parser.add_argument('--expected-sha')
    parser.add_argument('--worker')
    args = parser.parse_args()
    raise SystemExit(child(args) if args.worker else main(args))
