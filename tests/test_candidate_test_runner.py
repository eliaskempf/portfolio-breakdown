"""Candidate parallel runner evidence must fail closed; no app or network needed."""
import importlib.util
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('candidate_runner', ROOT/'tools/run_candidate_tests.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def evidence(tmp_path):
    groups = {'unit': ['tests/test_unit.py'], 'browser-0': ['tests/test_first_browser.py'],
              'browser-1': ['tests/test_second_browser.py']}
    nodeids = []
    for name, files in groups.items():
        node = files[0]+'::test_invented'
        nodeids.append(node)
        (tmp_path/(name+'.collection.json')).write_text(json.dumps({'nodeids': [node], 'skipped_browsers': [],
            'executed_nodeids': [node], 'collection_skips': []}))
        (tmp_path/(name+'.xml')).write_text('<testsuites><testsuite tests="1" errors="0" failures="0" skipped="0" time="1"><testcase name="invented"/></testsuite></testsuites>')
    return groups, dict.fromkeys(groups, 0), nodeids


def finish(tmp_path, groups, results, nodeids, *, unchanged=True):
    return runner.final_evidence(tmp_path, groups, results, nodeids,
        source_unchanged=unchanged, aggregate=tmp_path/'aggregate.xml')


def test_all_groups_require_exact_disjoint_collection_and_valid_junit(tmp_path):
    groups, results, nodeids = evidence(tmp_path)
    result = finish(tmp_path, groups, results, nodeids)
    assert result['status'] == 'passed' and result['complete_results']
    assert result['exhaustive_disjoint'] and result['junit']['tests'] == '3'
    assert len(ET.parse(tmp_path/'aggregate.xml').getroot()) == 3


@pytest.mark.parametrize('fault', ['missing_result', 'missing_xml', 'invalid_xml', 'wrong_xml_root',
    'xml_failure', 'xml_error', 'hidden_failure_element', 'failed_exit', 'unexpected_result',
    'missing_collection', 'wrong_collection', 'duplicate_collection', 'browser_skip', 'changed_source',
    'zero_junit_cases', 'missing_execution'])
def test_incomplete_or_failed_worker_cannot_produce_green_evidence(tmp_path, fault):
    groups, results, nodeids = evidence(tmp_path)
    xml = tmp_path/'browser-0.xml'
    collection = tmp_path/'browser-0.collection.json'
    unchanged = True
    if fault == 'missing_result':
        results.pop('browser-0')
    elif fault == 'missing_xml':
        xml.unlink()
    elif fault == 'invalid_xml':
        xml.write_text('<broken')
    elif fault == 'wrong_xml_root':
        xml.write_text('<invented/>')
    elif fault in ('xml_failure', 'xml_error'):
        kind = 'failure' if fault == 'xml_failure' else 'error'
        xml.write_text(f'<testsuite tests="1" errors="{int(kind == "error")}" failures="{int(kind == "failure")}" skipped="0"><testcase name="invented"><{kind} message="Invented failure"/></testcase></testsuite>')
    elif fault == 'hidden_failure_element':
        xml.write_text('<testsuite tests="1" errors="0" failures="0" skipped="0"><testcase name="invented"><failure message="Inconsistent summary"/></testcase></testsuite>')
    elif fault == 'failed_exit':
        results['browser-0'] = 2
    elif fault == 'unexpected_result':
        results['unreviewed-worker'] = 0
    elif fault == 'missing_collection':
        collection.unlink()
    elif fault == 'wrong_collection':
        collection.write_text(json.dumps({'nodeids': ['tests/test_unexpected.py::test_wrong'], 'skipped_browsers': []}))
    elif fault == 'duplicate_collection':
        collection.write_text(json.dumps({'nodeids': [nodeids[0]], 'skipped_browsers': []}))
    elif fault == 'browser_skip':
        collection.write_text(json.dumps({'nodeids': [nodeids[1]], 'skipped_browsers': [nodeids[1]]}))
    elif fault == 'zero_junit_cases':
        xml.write_text('<testsuite tests="0" errors="0" failures="0" skipped="0"/>')
    elif fault == 'missing_execution':
        data = json.loads(collection.read_text())
        data['executed_nodeids'] = []
        collection.write_text(json.dumps(data))
    else:
        unchanged = False
    result = finish(tmp_path, groups, results, nodeids, unchanged=unchanged)
    assert result['status'] == 'failed'
    if fault in {'missing_result', 'missing_xml', 'invalid_xml', 'wrong_xml_root', 'failed_exit', 'unexpected_result'}:
        assert int(result['junit']['errors']) > 0
    if fault in {'xml_failure', 'hidden_failure_element'}:
        assert int(result['junit']['failures']) > 0


def test_all_worker_failures_survive_aggregation(tmp_path):
    groups, results, nodeids = evidence(tmp_path)
    for name in groups:
        results[name] = 1
        (tmp_path/(name+'.xml')).write_text(f'<testsuite tests="1" failures="1" errors="0"><testcase name="{name}"><failure message="Invented {name} failure"/></testcase></testsuite>')
    result = finish(tmp_path, groups, results, nodeids)
    assert result['status'] == 'failed' and result['junit']['failures'] == '3'
    assert {e.get('message') for e in ET.parse(tmp_path/'aggregate.xml').getroot().iter('failure')} == {
        f'Invented {name} failure' for name in groups}


def test_workflow_keeps_ceiling_and_aggregate_artifact_path():
    import yaml
    workflow = yaml.load((ROOT/'.github/workflows/candidate.yml').read_text(), Loader=yaml.BaseLoader)
    job = workflow['jobs']['build']
    assert job['timeout-minutes'] == '45'
    step = next(s for s in job['steps'] if s.get('name') == 'Tests (including required browser suites)')
    assert step['run'] == 'uv run python tools/run_candidate_tests.py'
    assert step['env']['PORTFOLIO_REQUIRE_BROWSER'] == '1'
    upload = next(s for s in job['steps'] if s.get('with', {}).get('name') == 'candidate-test-results-${{ matrix.platform }}')
    paths = upload['with']['path'].splitlines()
    assert 'dist/test-results.xml' in paths and 'dist/candidate-tests/*.log' in paths
    from portfolio_app.privacy import path_problem
    assert path_problem('tools/run_candidate_tests.py') is None
    assert path_problem('tools/unreviewed_runner.py') is not None


def test_legitimate_nonbrowser_collection_skip_has_separate_junit_case(tmp_path):
    groups, results, nodeids = evidence(tmp_path)
    path = tmp_path/'unit.collection.json'
    data = json.loads(path.read_text())
    data['collection_skips'] = ['tests/test_platform_only.py']
    path.write_text(json.dumps(data))
    (tmp_path/'unit.xml').write_text('<testsuite tests="2" errors="0" failures="0" skipped="1"><testcase name="invented"/><testcase name="platform_only"><skipped/></testcase></testsuite>')
    result = finish(tmp_path, groups, results, nodeids)
    assert result['status'] == 'passed' and result['junit']['tests'] == '4'


def test_cleanup_continues_after_tree_kill_timeout_and_preserves_primary(monkeypatch):
    import io
    import subprocess
    from types import SimpleNamespace
    calls = []
    class Process:
        def __init__(self, pid):
            self.pid = pid
        def poll(self):
            return None
        def wait(self, timeout):
            return 0
    logs = [io.StringIO() for _ in range(3)]
    workers = {str(pid): (Process(pid), log) for pid, log in zip((123, 124, 125), logs)}
    def stop(command, **kwargs):
        calls.append(command)
        if command[2] == '123':
            raise subprocess.TimeoutExpired(command, 30)
    monkeypatch.setattr(runner, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(runner.subprocess, 'run', stop)
    with pytest.raises(RuntimeError, match='primary'):
        try:
            raise RuntimeError('Invented primary worker failure')
        finally:
            errors = runner.cleanup_processes(workers)
    assert [call[2] for call in calls] == ['123', '124', '125']
    assert errors and all(log.closed for log in logs)


def test_worker_spawn_failure_closes_new_log(tmp_path, monkeypatch):
    import io
    from types import SimpleNamespace
    log = io.StringIO()
    def failed(*args, **kwargs):
        raise OSError('Invented spawn failure')
    monkeypatch.setattr(runner.subprocess, 'Popen', failed)
    with pytest.raises(OSError, match='spawn failure'):
        runner.start_worker(['invented'], cwd=tmp_path, env={}, log_path=SimpleNamespace(open=lambda mode: log))
    assert log.closed


def test_untracked_source_is_not_a_clean_frozen_checkout(tmp_path):
    import subprocess
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    assert runner.clean(tmp_path)
    (tmp_path/'unreviewed.py').write_text('pass\n')
    assert not runner.clean(tmp_path)
