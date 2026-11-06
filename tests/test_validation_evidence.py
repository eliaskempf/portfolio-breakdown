"""Synthetic failures must remain diagnosable and must never accept a release."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT/'tools'))
    import release_evidence
    import run_diagnostics
    return release_evidence, run_diagnostics


@pytest.mark.parametrize('selection', [[], ['--help'], ['tests/../private.py'], ['tests/absent.py'],
                                      ['tests/test_invented.py', 'tests/test_invented.py::test_one']])
def test_diagnostics_reject_unknown_empty_and_overlapping_selections(modules, selection):
    _, diagnostic = modules
    with pytest.raises(ValueError):
        diagnostic.selections(selection, ['tests/test_invented.py::test_one'])


def test_diagnostics_select_parameterized_nodes_without_shell_parsing(modules):
    _, diagnostic = modules
    nodes = ['tests/test_invented.py::test_one[value with spaces]', 'tests/test_invented.py::test_two']
    assert diagnostic.selections(['tests/test_invented.py'], nodes) == nodes
    assert diagnostic.selections([nodes[0]], nodes) == nodes[:1]


def test_source_smoke_diagnostic_supplies_fresh_evidence_directory(tmp_path, modules, monkeypatch):
    from types import SimpleNamespace
    release, diagnostic = modules
    output = tmp_path / 'diagnostic'
    monkeypatch.setattr(release, 'identity', lambda: dict(source='a'*40))
    monkeypatch.setattr(sys, 'argv', ['run_diagnostics', '--suite', 'source-smoke', '--output', str(output)])
    calls = []

    def run(command, **kwargs):
        destination = Path(command[command.index('--evidence') + 1])
        # Match the real smoke driver's exclusive evidence-directory ownership.
        destination.mkdir(parents=True, exist_ok=False)
        calls.append(destination)
        (destination / 'smoke-result.json').write_text('{"status":"passed"}')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(diagnostic.subprocess, 'run', run)
    assert diagnostic.main() == 0
    assert calls == [output / '1/source-smoke']
    report = json.loads((output / 'diagnostic.json').read_text())
    assert report['publishable'] is False and report['attempts'][0]['exit_code'] == 0




def test_native_pre_attachment_failure_preserves_logs(tmp_path):
    pytest.importorskip('playwright.sync_api')
    spec = importlib.util.spec_from_file_location('native_evidence_test', ROOT/'tools/window_smoke.py')
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    root = tmp_path/'synthetic'
    root.mkdir()
    destination = tmp_path/'evidence'
    with pytest.raises(ConnectionRefusedError):
        with native.process_evidence(root, destination, 'welcome') as record:
            (root/'server.log').write_text('Invented server stopped before debugger attachment')
            record('launched', pid=42)
            record('debugger-ready')
            raise ConnectionRefusedError('Invented debugger closed')
    assert (destination/'welcome/logs/server.log').read_text().startswith('Invented server')
    events = json.loads((destination/'welcome/process.json').read_text())
    assert [e['event'] for e in events] == ['launched', 'debugger-ready', 'failure']


@pytest.mark.parametrize('fault', ['source', 'lock', 'system', 'python', 'coverage', 'execution', 'failed_exit'])
def test_source_evidence_rejects_mismatch_and_partial_tests(tmp_path, modules, fault):
    from test_candidate_test_runner import evidence, finish
    release, _ = modules
    groups, results, nodes = evidence(tmp_path)
    report = finish(tmp_path, groups, results, nodes)
    report.update(source='a'*40, lock_sha256='b'*64, system='Linux', python='3.12.14')
    manifest = dict(source='a'*40, groups=groups)
    canonical = dict(nodeids=nodes, skipped_browsers=[])
    if fault in ('source', 'lock', 'system', 'python'):
        report[{'lock':'lock_sha256'}.get(fault, fault)] = 'invented mismatch'
    elif fault == 'coverage':
        canonical['nodeids'] += ['tests/test_missing.py::test_missing']
    elif fault == 'execution':
        path = tmp_path/'browser-0.collection.json'
        saved = json.loads(path.read_text());saved['executed_nodeids'] = [];path.write_text(json.dumps(saved))
    else:
        report['exit_codes']['unit-0'] = 1
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    (tmp_path/'results.json').write_text(json.dumps(report))
    (tmp_path/'canonical.collection.json').write_text(json.dumps(canonical))
    with pytest.raises(ValueError):
        release.check_source(tmp_path, source='a'*40, lock='b'*64, target='linux-x64')


@pytest.mark.parametrize('fault', ['missing', 'checksum', 'source', 'lock', 'stage', 'run', 'build_attempt',
                                  'acceptance_attempt', 'failed_job', 'partial_source', 'diagnostic'])
def test_publish_rejects_incomplete_or_stale_acceptance(tmp_path, modules, fault):
    import zipfile
    from test_release import FakeGitHub, promote
    api = FakeGitHub(tmp_path)
    name = 'acceptance-windows-x64-native-startup'
    artifact = next(a for a in api.artifacts if a['name'] == name)
    if fault == 'missing':
        api.artifacts.remove(artifact)
    elif fault == 'failed_job':
        next(j for j in api.browser_jobs if j['name'] == 'accept-windows-x64-native-startup')['conclusion'] = 'failure'
    elif fault == 'partial_source':
        api.artifacts[:] = [a for a in api.artifacts if a['name'] != 'source-evidence-windows-x64']
    elif fault == 'diagnostic':
        api.run['path'] = '.github/workflows/diagnostics.yml'
    else:
        archive = tmp_path/f"{artifact['id']}.zip"
        with zipfile.ZipFile(archive) as zipped:
            receipt = json.loads(zipped.read('acceptance.json'))
        key = dict(checksum='manifest_sha256', source='source', lock='lock_sha256', stage='stage', run='run_id',
                   build_attempt='build_attempt', acceptance_attempt='run_attempt')[fault]
        receipt[key] = '99' if 'attempt' in key else 'invented mismatch'
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.writestr('acceptance.json', json.dumps(receipt))
    with pytest.raises(ValueError):
        promote.promote(api, 'invented/project', '123', linux_native_run_id='789')
    assert not api.mutations and not api.uploaded


def test_acceptance_writes_no_success_receipt_after_failed_or_mutated_operation(tmp_path, modules, monkeypatch):
    from hashlib import sha256
    from test_release import candidate, rewrite_candidate_manifest
    release, _ = modules
    directory = candidate(tmp_path/'candidate')
    output = tmp_path/'acceptance.json'
    monkeypatch.setattr(release, 'identity', lambda: dict(source='a'*40, source_clean=True, lock_sha256='b'*64, run_id='123', run_attempt='1'))
    def failed():
        raise RuntimeError('Invented failed acceptance')
    with pytest.raises(RuntimeError):
        release.acceptance(directory, 'packaged-browser', failed, output)
    assert not output.exists()
    def changed():
        rewrite_candidate_manifest(directory, run_attempt='2')
    with pytest.raises(ValueError):
        release.acceptance(directory, 'packaged-browser', changed, output)
    assert not output.exists()
    release.acceptance(directory, 'packaged-browser', lambda: None, output)
    assert json.loads(output.read_text())['manifest_sha256'] == sha256((directory/'manifest.json').read_bytes()).hexdigest()


def test_workflows_keep_builds_and_selected_acceptance_independent():
    import yaml
    workflow = yaml.safe_load((ROOT/'.github/workflows/candidate.yml').read_text())
    jobs = workflow['jobs']
    for target in ('linux-x64', 'windows-x64'):
        build = jobs['build-'+target]
        assert any(s.get('with', {}).get('name') == 'unaccepted-'+target for s in build['steps'])
        assert not any('release_evidence.py accept' in s.get('run', '') for s in build['steps'])
        for name, job in jobs.items():
            if name.startswith('accept-'+target+'-'):
                assert job['needs'] == 'build-'+target
                assert not any('release.py build' in s.get('run', '') or 'pytest' in s.get('run', '') for s in job['steps'])
        assert jobs['accepted-'+target]['needs'] == [name for name in jobs if name.startswith('accept-'+target+'-')]
    assert jobs['linux-compatibility']['needs'] == 'build-linux-x64'
    diagnostic = yaml.safe_load((ROOT/'.github/workflows/diagnostics.yml').read_text())
    assert set(diagnostic['on']) == {'workflow_dispatch'}
    assert diagnostic['permissions'] == dict(contents='read', actions='read')
