"""Optional Mac promotion uses synthetic bytes; never contacts GitHub or a Mac."""
from hashlib import sha256
import importlib.util
import json
import shutil
import zipfile

import pytest

from test_release import ROOT, FakeGitHub, promote, rewrite_candidate_manifest


class MacGitHub(FakeGitHub):
    def __init__(self, root, monkeypatch):
        super().__init__(root)
        monkeypatch.syspath_prepend(str(ROOT / 'tools'))
        spec = importlib.util.spec_from_file_location('mac_candidate_build', ROOT / 'tools/desktop_build.py')
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        # Exercise the actual staging writer against invented source, docs and DMG.
        checkout = root / 'synthetic-checkout'
        checkout.mkdir()
        (checkout / 'uv.lock').write_text('Invented dependency lock')
        for name in build.BRANDING_ASSETS:
            icon = checkout / 'src/portfolio_app/assets' / name
            icon.parent.mkdir(parents=True, exist_ok=True)
            icon.write_bytes(b'Invented artwork placeholder')
        monkeypatch.setattr(build, 'ROOT', checkout)
        monkeypatch.setenv('GITHUB_RUN_ID', '456')
        monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '1')
        output = root / 'mac'
        bundle = output / 'bundle'
        bundle.mkdir(parents=True)
        source = output / 'source'
        source.mkdir()
        linux = root / 'linux-x64'
        for path in linux.iterdir():
            if path.name.startswith('portfolio_breakdown-'):
                shutil.copyfile(path, source / path.name)
            elif path.name in {'THIRD_PARTY_NOTICES.txt', 'dependencies.json'}:
                shutil.copyfile(path, bundle / path.name)
        with zipfile.ZipFile(linux / 'portfolio-breakdown-0.1.0-docs.zip') as docs:
            docs.extractall(bundle / 'documentation')
        self.dmg_name = 'portfolio-breakdown-experimental-0.1.0-macos-arm64.dmg'
        artifact = output / self.dmg_name
        artifact.write_bytes(b'Invented tested DMG bytes')
        build.desktop_candidate(output, bundle, artifact, source, '0.1.0', 'macos-arm64')
        self.mac_folder = output / 'candidate'
        lock = sha256((checkout / 'uv.lock').read_bytes()).hexdigest()
        for number, platform in enumerate(['linux-x64', 'windows-x64']):
            rewrite_candidate_manifest(root / platform, lock_sha256=lock)
            shutil.make_archive(str(root / str(number)), 'zip', root / platform)
        rewrite_candidate_manifest(self.native_folder, lock_sha256=lock)
        self.repack_native()
        self.repack()
        self.mac_run = dict(self.run, path='.github/workflows/desktop-experiment.yml', conclusion='failure')
        self.jobs = [dict(name=name, status='completed', conclusion='success', run_attempt=1, head_sha='a' * 40) for name in
                     ['build-macos-arm64', 'macos-compatibility-macos-15', 'macos-compatibility-macos-26']]
        self.jobs.append(dict(name='build-linux-x64', status='completed', conclusion='failure'))
        self.mac_artifacts = [dict(id=2, name='candidate-macos-arm64', expired=False)]
        self.existing_draft = False

    def repack(self):
        shutil.make_archive(str(self.root / '2'), 'zip', self.mac_folder)

    def call(self, path, *, method='GET', body=None):
        if method == 'GET':
            if path == '/actions/runs/456':
                return self.mac_run
            if path.startswith('/actions/runs/456/jobs'):
                return dict(total_count=len(self.jobs), jobs=self.jobs)
            if path.startswith('/actions/runs/456/artifacts'):
                return dict(artifacts=self.mac_artifacts)
            if path.startswith('/releases/tags/') and self.existing_draft:
                return dict(id=42, draft=True, assets=[], body='Existing reviewed notes.')
        return super().call(path, method=method, body=body)


@pytest.mark.parametrize('existing_draft', [False, True])
def test_macos_publishes_exact_bytes_and_limitations_despite_unrelated_linux_failure(tmp_path, monkeypatch, existing_draft):
    api = MacGitHub(tmp_path, monkeypatch)
    api.existing_draft = existing_draft
    promote.promote(api, 'invented/project', '123', linux_native_run_id='789', macos_run_id='456')
    assert api.uploaded[api.dmg_name] == b'Invented tested DMG bytes'
    assert 'macos-arm64-THIRD_PARTY_NOTICES.txt' in api.uploaded
    assert 'macos-arm64-portfolio_breakdown-0.1.0.tar.gz' in api.uploaded
    assert json.loads(api.uploaded['macos-arm64-manifest.json'])['run_id'] == '456'
    body = api.mutations[-1][1]['body']
    assert 'Experimental macOS installer' in body
    assert 'Gatekeeper' in body and 'unverified' in body
    if existing_draft:
        assert body.startswith('Existing reviewed notes.')
    assert sha256(api.uploaded[api.dmg_name]).hexdigest().encode() in api.uploaded['SHA256SUMS']


@pytest.mark.parametrize('failure', ['failed-job', 'skipped-job', 'missing-job', 'duplicate-job',
    'other-commit', 'fork', 'branch', 'running', 'workflow', 'missing-artifact', 'expired',
    'corrupt-dmg', 'lock', 'version', 'signed-claim', 'undisclosed-experimental', 'wrong-attempt', 'wrong-run'])
def test_invalid_mac_input_never_mutates_release(tmp_path, monkeypatch, failure):
    api = MacGitHub(tmp_path, monkeypatch)
    changes = {}
    if failure in {'failed-job', 'skipped-job'}:
        api.jobs[1]['conclusion'] = 'failure' if failure == 'failed-job' else 'skipped'
    elif failure == 'missing-job':
        api.jobs.pop(1)
    elif failure == 'duplicate-job':
        api.jobs.append(api.jobs[1])
    elif failure == 'other-commit':
        api.mac_run['head_sha'] = 'c' * 40
    elif failure == 'fork':
        api.mac_run['head_repository'] = dict(full_name='other/project')
    elif failure == 'branch':
        api.mac_run['head_branch'] = 'other'
    elif failure == 'running':
        api.mac_run['status'] = 'in_progress'
    elif failure == 'workflow':
        api.mac_run['path'] = '.github/workflows/other.yml'
    elif failure == 'missing-artifact':
        api.mac_artifacts.clear()
    elif failure == 'expired':
        api.mac_artifacts[0]['expired'] = True
    elif failure == 'corrupt-dmg':
        (api.mac_folder / api.dmg_name).write_bytes(b'Changed after testing')
    else:
        changes = {'lock': dict(lock_sha256='c' * 64), 'version': dict(version='0.2.0'),
            'signed-claim': dict(developer_id_signed=True), 'undisclosed-experimental': dict(experimental=False),
            'wrong-attempt': dict(run_attempt='2'), 'wrong-run': dict(run_id='789')}[failure]
    if changes:
        rewrite_candidate_manifest(api.mac_folder, **changes)
    api.repack()
    with pytest.raises(ValueError):
        promote.promote(api, 'invented/project', '123', linux_native_run_id='789', macos_run_id='456')
    assert api.mutations == [] and api.uploaded == {}


def test_mac_can_be_deferred_without_blocking_supported_release(tmp_path, monkeypatch):
    api = MacGitHub(tmp_path, monkeypatch)
    api.jobs[0]['conclusion'] = 'failure'
    promote.promote(api, 'invented/project', '123', linux_native_run_id='789')
    assert not any('macos' in name for name in api.uploaded)
    assert api.mutations[-1][1]['draft'] is False
    assert 'Experimental macOS' not in api.mutations[-1][1].get('body', '')


def test_compatibility_retry_can_accept_same_earlier_mac_build(tmp_path, monkeypatch):
    api = MacGitHub(tmp_path, monkeypatch)
    api.mac_run['run_attempt'] = 2
    api.jobs[1]['run_attempt'] = 2
    promote.promote(api, 'invented/project', '123', linux_native_run_id='789', macos_run_id='456')
    assert api.dmg_name in api.uploaded


@pytest.mark.parametrize('new_build', [False, True])
def test_mac_checks_must_identify_the_current_artifact_not_a_replaced_build(tmp_path, monkeypatch, new_build):
    api = MacGitHub(tmp_path, monkeypatch)
    api.mac_run['run_attempt'] = 2
    api.jobs[0]['run_attempt'] = 2
    if new_build:
        rewrite_candidate_manifest(api.mac_folder, run_attempt='2')
        api.repack()
    with pytest.raises(ValueError, match='job evidence'):
        promote.promote(api, 'invented/project', '123', linux_native_run_id='789', macos_run_id='456')
    assert api.mutations == [] and api.uploaded == {}


def test_workflow_mac_jobs_and_upload_match_publisher_contract():
    import yaml
    workflow = yaml.load((ROOT / '.github/workflows/desktop-experiment.yml').read_text(), Loader=yaml.BaseLoader)
    build = workflow['jobs']['build']
    assert build['name'] == 'build-${{ matrix.target }}'
    upload = next(step for step in build['steps'] if step.get('with', {}).get('name') == 'candidate-macos-arm64')
    assert upload['if'] == "runner.os == 'macOS'"  # Default success guard, never always().
    assert upload['with']['overwrite'] == 'true'
    compatibility = workflow['jobs']['macos-compatibility']
    assert compatibility['name'] == 'macos-compatibility-${{ matrix.os }}'
    assert compatibility['strategy']['matrix']['os'] == ['macos-15', 'macos-26']
