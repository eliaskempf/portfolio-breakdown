"""Native Linux release promotion uses invented packages and a fake GitHub API."""
from hashlib import sha256
import importlib.util
import json
import zipfile

import pytest
import yaml

from test_macos_release import MacGitHub
from test_release import ROOT, promote, rewrite_candidate_manifest


@pytest.fixture
def api(tmp_path, monkeypatch):
    api = MacGitHub(tmp_path, monkeypatch)
    spec = importlib.util.spec_from_file_location('linux_candidate_build', ROOT / 'tools/desktop_build.py')
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    monkeypatch.setattr(build, 'ROOT', tmp_path / 'synthetic-checkout')
    monkeypatch.setenv('GITHUB_RUN_ID', '789')
    output = tmp_path / 'native-staged'
    output.mkdir()
    artifact = output / 'portfolio-breakdown-experimental_0.1.0_amd64.deb'
    artifact.write_bytes(b'Invented tested Debian package')
    build.desktop_candidate(output, tmp_path / 'mac/bundle', artifact,
                            tmp_path / 'mac/source', '0.1.0', 'linux-native-x64')
    api.native_folder = output / 'candidate'
    api.repack_native()
    return api


def publish(api, *, mac=True):
    promote.promote(api, 'invented/project', '123', linux_native_run_id='789',
                    macos_run_id='456' if mac else None)


def test_complete_release_inventory_and_identical_installer_bytes(api):
    publish(api)
    expected = {'SHA256SUMS', 'portfolio-breakdown-0.1.0-docs.zip',
                'portfolio-breakdown-0.1.0-windows-x64.zip',
                'portfolio-breakdown-0.1.0-windows-x64-setup.exe',
                'portfolio-breakdown-0.1.0-linux-x64.tar.gz',
                'portfolio-breakdown-experimental_0.1.0_amd64.deb',
                'portfolio-breakdown-experimental-0.1.0-macos-arm64.dmg',
                'portfolio_breakdown-0.1.0-py3-none-any.whl', 'portfolio_breakdown-0.1.0.tar.gz'}
    for platform in ['linux-x64', 'windows-x64', 'linux-native-x64', 'macos-arm64']:
        expected.update(f'{platform}-{name}' for name in
                        ['manifest.json', 'THIRD_PARTY_NOTICES.txt', 'dependencies.json'])
    for platform in ['linux-native-x64', 'macos-arm64']:
        expected.update(f'{platform}-{name}' for name in
                        ['portfolio_breakdown-0.1.0-py3-none-any.whl', 'portfolio_breakdown-0.1.0.tar.gz'])
    assert set(api.uploaded) == expected
    assert api.uploaded['portfolio-breakdown-experimental_0.1.0_amd64.deb'] == b'Invented tested Debian package'
    assert api.uploaded[api.dmg_name] == b'Invented tested DMG bytes'
    sums = dict(line.split('  ', 1)[::-1] for line in api.uploaded['SHA256SUMS'].decode().splitlines())
    assert set(sums) == expected - {'SHA256SUMS'}
    for name, digest in sums.items():
        assert sha256(api.uploaded[name]).hexdigest() == digest
    manifest = json.loads(api.uploaded['linux-native-x64-manifest.json'])
    assert manifest['run_id'] == '789'
    for name in ['THIRD_PARTY_NOTICES.txt', 'dependencies.json',
                 'portfolio_breakdown-0.1.0-py3-none-any.whl', 'portfolio_breakdown-0.1.0.tar.gz']:
        assert sha256(api.uploaded['linux-native-x64-' + name]).hexdigest() == manifest['files'][name]


@pytest.mark.parametrize('failure', [
    'failed-build', 'failed-x11', 'failed-wayland', 'skipped-job', 'missing-job', 'duplicate-job',
    'wrong-job-sha', 'other-commit', 'fork', 'branch', 'running', 'workflow', 'event',
    'missing-artifact', 'expired', 'duplicate-artifact', 'corrupt-deb', 'lock', 'version',
    'wrong-attempt', 'wrong-run', 'dirty', 'docs', 'missing-source', 'extra-file',
])
def test_invalid_linux_evidence_prevents_all_remote_mutations(api, failure):
    if failure.startswith('failed-'):
        index = {'failed-build': 0, 'failed-x11': 1, 'failed-wayland': 2}[failure]
        api.native_jobs[index]['conclusion'] = 'failure'
    elif failure == 'skipped-job':
        api.native_jobs[1]['conclusion'] = 'skipped'
    elif failure == 'missing-job':
        api.native_jobs.pop()
    elif failure == 'duplicate-job':
        api.native_jobs.append(api.native_jobs[0])
    elif failure == 'wrong-job-sha':
        api.native_jobs[1]['head_sha'] = 'c' * 40
    elif failure in {'other-commit', 'fork', 'branch', 'running', 'workflow', 'event'}:
        api.native_run.update({
            'other-commit': dict(head_sha='c' * 40), 'fork': dict(head_repository={'full_name': 'other/project'}),
            'branch': dict(head_branch='other'), 'running': dict(status='in_progress'),
            'workflow': dict(path='.github/workflows/other.yml'), 'event': dict(event='push'),
        }[failure])
    elif failure == 'missing-artifact':
        api.native_artifacts.clear()
    elif failure == 'expired':
        api.native_artifacts[0]['expired'] = True
    elif failure == 'duplicate-artifact':
        api.native_artifacts.append(api.native_artifacts[0])
    elif failure == 'corrupt-deb':
        (api.native_folder / 'portfolio-breakdown-experimental_0.1.0_amd64.deb').write_bytes(b'Changed')
    elif failure == 'missing-source':
        (api.native_folder / 'portfolio_breakdown-0.1.0.tar.gz').unlink()
    elif failure == 'extra-file':
        (api.native_folder / 'unexpected.txt').write_text('Invented')
    else:
        rewrite_candidate_manifest(api.native_folder, **{
            'lock': dict(lock_sha256='c' * 64), 'version': dict(version='0.2.0'),
            'wrong-attempt': dict(run_attempt='2'), 'wrong-run': dict(run_id='456'),
            'dirty': dict(source_clean=False), 'docs': dict(documentation={}),
        }[failure])
    api.repack_native()
    with pytest.raises(ValueError):
        publish(api)
    assert api.mutations == [] and api.uploaded == {}


@pytest.mark.parametrize('build,artifact,x11,wayland,accepted', [
    (1, '1', 2, 1, True), (1, '1', 2, 2, True), (2, '2', 2, 2, True),
    (2, '2', 1, 2, False), (2, '2', 2, 1, False), (2, '1', 2, 2, False),
    (1, '1', None, 2, False), (1, '1', 3, 2, False),
])
def test_linux_retry_evidence_tracks_retained_installer(api, build, artifact, x11, wayland, accepted):
    api.native_run['run_attempt'] = 2
    for job, attempt in zip(api.native_jobs, [build, x11, wayland]):
        job['run_attempt'] = attempt
    rewrite_candidate_manifest(api.native_folder, run_attempt=artifact)
    api.repack_native()
    if accepted:
        publish(api)
        assert 'portfolio-breakdown-experimental_0.1.0_amd64.deb' in api.uploaded
    else:
        with pytest.raises(ValueError, match='job evidence'):
            publish(api)
        assert api.mutations == [] and api.uploaded == {}


def test_same_desktop_run_can_supply_both_native_platforms(api, monkeypatch):
    rewrite_candidate_manifest(api.native_folder, run_id='456')
    api.repack_native()
    api.jobs = [job for job in api.jobs if job['name'] != 'build-linux-x64'] + api.native_jobs
    api.mac_artifacts += api.native_artifacts
    promote.promote(api, 'invented/project', '123', linux_native_run_id='456', macos_run_id='456')
    assert api.dmg_name in api.uploaded
    assert 'portfolio-breakdown-experimental_0.1.0_amd64.deb' in api.uploaded


@pytest.mark.parametrize('draft,assets', [(False, []), (True, [{'name': 'partially-uploaded.deb'}])])
def test_published_or_partial_release_is_never_overwritten(api, monkeypatch, draft, assets):
    call = api.call
    def existing(path, **kwargs):
        if path.startswith('/git/ref/tags/'):
            return {'object': {'sha': 'a' * 40}}
        if path.startswith('/releases/tags/'):
            return dict(id=42, draft=draft, assets=assets)
        return call(path, **kwargs)
    monkeypatch.setattr(api, 'call', existing)
    with pytest.raises(ValueError, match='already'):
        publish(api)
    assert api.mutations == [] and api.uploaded == {}


def test_native_linux_is_required_even_if_macos_is_deferred(api):
    with pytest.raises(ValueError, match='native Linux'):
        promote.promote(api, 'invented/project', '123', linux_native_run_id='')
    assert api.mutations == []
    publish(api, mac=False)
    assert not any('macos' in name for name in api.uploaded)


def test_incomplete_job_listing_cannot_accept_native_linux(api, monkeypatch):
    call = api.call
    def incomplete(path, **kwargs):
        result = call(path, **kwargs)
        if path.startswith('/actions/runs/789/jobs'):
            result['total_count'] += 1
        return result
    monkeypatch.setattr(api, 'call', incomplete)
    with pytest.raises(ValueError, match='Incomplete'):
        publish(api)
    assert api.mutations == [] and api.uploaded == {}


def test_valid_but_different_native_documentation_is_rejected(api):
    manifest = json.loads((api.native_folder / 'manifest.json').read_text())
    name = 'portfolio-breakdown-0.1.0-docs.zip'
    with zipfile.ZipFile(api.native_folder / name) as archive:
        info = json.loads(archive.read('build-info.json'))
    content = b'Different invented guide at the same source SHA'
    info['files'] = {'index.html': sha256(content).hexdigest()}
    info_bytes = json.dumps(info).encode()
    with zipfile.ZipFile(api.native_folder / name, 'w') as archive:
        archive.writestr('index.html', content)
        archive.writestr('build-info.json', info_bytes)
    manifest['files'][name] = sha256((api.native_folder / name).read_bytes()).hexdigest()
    manifest['documentation']['build_info_sha256'] = sha256(info_bytes).hexdigest()
    rewrite_candidate_manifest(api.native_folder, **manifest)
    api.repack_native()
    with pytest.raises(ValueError, match='different documentation'):
        publish(api)
    assert api.mutations == [] and api.uploaded == {}


def test_linux_workflow_contract():
    workflow = yaml.load((ROOT / '.github/workflows/desktop-experiment.yml').read_text(), Loader=yaml.BaseLoader)
    steps = workflow['jobs']['build']['steps']
    upload = next(step for step in steps if step.get('with', {}).get('name') == 'candidate-linux-native-x64')
    assert upload['if'] == "runner.os == 'Linux'"
    assert upload['with']['overwrite'] == 'true'
    assert upload['with']['retention-days'] == '30'
    assert steps.index(upload) > next(i for i, step in enumerate(steps) if step.get('name') == 'Install Linux artifact and test native binary')
    compatibility = workflow['jobs']['linux-compatibility']
    assert compatibility['name'] == 'linux-compatibility-${{ matrix.backend }}'
    assert compatibility['strategy']['matrix']['backend'] == ['x11', 'wayland']
    assert any(step.get('with', {}).get('name') == 'candidate-linux-native-x64' for step in compatibility['steps'])
    publish_workflow = yaml.load((ROOT / '.github/workflows/publish.yml').read_text(), Loader=yaml.BaseLoader)
    assert publish_workflow['on']['workflow_dispatch']['inputs']['linux_native_candidate_run']['required'] == 'true'
