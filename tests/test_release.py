"""Candidate promotion tests use invented archives and a fake GitHub API only."""
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import shutil
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_promote', ROOT / 'tools/promote.py')
promote = importlib.util.module_from_spec(spec)
spec.loader.exec_module(promote)


def candidate(directory, platform='linux-x64'):
    directory.mkdir()
    version = '0.1.0'
    suffix = '.zip' if platform == 'windows-x64' else '.tar.gz'
    names = [f'portfolio-breakdown-{version}-{platform}{suffix}',
             f'portfolio_breakdown-{version}-py3-none-any.whl',
             f'portfolio_breakdown-{version}.tar.gz', 'THIRD_PARTY_NOTICES.txt', 'dependencies.json']
    files = {}
    for name in names:
        content = ('Synthetic candidate ' + name).encode()
        (directory / name).write_bytes(content)
        files[name] = sha256(content).hexdigest()
    manifest = dict(schema=1, version=version, commit='a' * 40, run_id='123', run_attempt='1',
                    platform=platform, lock_sha256='b' * 64, icon_ready=True, source_clean=True, files=files)
    (directory / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    sums = dict(files, **{'manifest.json': sha256((directory / 'manifest.json').read_bytes()).hexdigest()})
    (directory / 'SHA256SUMS').write_text(''.join(f'{value}  {name}\n' for name, value in sums.items()))
    return directory


def verify(directory, **kwargs):
    return promote.verify_candidate(directory, **(dict(commit='a'*40, run_id='123', run_attempt=1, platform='linux-x64') | kwargs))


def test_candidate_checks_identity_and_every_checksum(tmp_path):
    folder = candidate(tmp_path / 'candidate')
    assert verify(folder)['version'] == '0.1.0'
    with pytest.raises(ValueError, match='identity'):
        verify(folder, commit='c'*40)
    with pytest.raises(ValueError, match='identity'):
        verify(folder, run_attempt=2)
    (folder / 'dependencies.json').write_text('changed after testing')
    with pytest.raises(ValueError, match='Checksum'):
        verify(folder)


def test_unapproved_artwork_blocks_publication(tmp_path):
    folder = candidate(tmp_path / 'candidate')
    manifest = json.loads((folder / 'manifest.json').read_text())
    manifest['icon_ready'] = False
    (folder / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='artwork'):
        verify(folder)


def test_preliminary_candidates_still_require_exact_files_and_checksums(tmp_path):
    folder = candidate(tmp_path / 'candidate')
    manifest = json.loads((folder / 'manifest.json').read_text())
    manifest.update(icon_ready=False, source_clean=False)
    (folder / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    sums = dict(manifest['files'], **{'manifest.json': sha256((folder / 'manifest.json').read_bytes()).hexdigest()})
    (folder / 'SHA256SUMS').write_text(''.join(f'{value}  {name}\n' for name, value in sums.items()))
    assert verify(folder, require_publishable=False)['icon_ready'] is False
    (folder / '.gitignore').write_text('*\n')
    with pytest.raises(ValueError, match='Unexpected candidate content'):
        verify(folder, require_publishable=False)
    (folder / '.gitignore').unlink()
    (folder / 'dependencies.json').write_text('altered')
    with pytest.raises(ValueError, match='Checksum'):
        verify(folder, require_publishable=False)


def test_artifacts_cannot_escape_extraction_directory(tmp_path):
    archive = tmp_path / 'unsafe.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('../outside', 'synthetic')
    with pytest.raises(ValueError, match='layout'):
        promote.safe_extract(archive, tmp_path / 'extract')
    assert not (tmp_path / 'outside').exists()


class FakeGitHub:
    def __init__(self, root):
        self.root = root
        self.mutations = []
        self.uploaded = {}
        self.run = dict(conclusion='success', status='completed', event='workflow_dispatch',
                        path='.github/workflows/candidate.yml', head_branch='main',
                        head_repository={'full_name': 'invented/project'}, head_sha='a'*40, run_attempt=1)
        self.artifacts = []
        for number, platform in enumerate(['linux-x64', 'windows-x64']):
            folder = candidate(root / platform, platform)
            shutil.make_archive(str(root / str(number)), 'zip', folder)
            self.artifacts.append(dict(id=number, name='candidate-' + platform, expired=False))

    def call(self, path, *, method='GET', body=None):
        if method != 'GET':
            self.mutations.append((path, body))
            return {'id': 42, 'draft': True}
        if path == '':
            return {'default_branch': 'main'}
        if path == '/actions/runs/123':
            return self.run
        if path.startswith('/actions/runs/123/artifacts'):
            return {'artifacts': self.artifacts}
        from urllib.error import HTTPError
        raise HTTPError(path, 404, 'not found', {}, None)

    def download(self, artifact, destination):
        shutil.copy(self.root / f'{artifact}.zip', destination)

    def upload(self, release_id, path):
        self.uploaded[path.name] = path.read_bytes()


def test_promotion_uploads_identical_tested_bytes(tmp_path):
    api = FakeGitHub(tmp_path)
    promote.promote(api, 'invented/project', '123')
    for platform in ['linux-x64', 'windows-x64']:
        suffix = '.zip' if platform == 'windows-x64' else '.tar.gz'
        name = f'portfolio-breakdown-0.1.0-{platform}{suffix}'
        assert api.uploaded[name] == (tmp_path / platform / name).read_bytes()
    assert api.mutations[-1] == ('/releases/42', {'draft': False})


@pytest.mark.parametrize('failure', ['expired', 'failed', 'fork', 'branch', 'workflow', 'different-platform-version'])
def test_invalid_candidate_never_creates_a_tag_or_release(tmp_path, failure):
    api = FakeGitHub(tmp_path)
    if failure == 'expired':
        api.artifacts[0]['expired'] = True
    elif failure == 'failed':
        api.run['conclusion'] = 'failure'
    elif failure == 'fork':
        api.run['head_repository']['full_name'] = 'elsewhere/project'
    elif failure == 'branch':
        api.run['head_branch'] = 'untested-branch'
    elif failure == 'workflow':
        api.run['path'] = '.github/workflows/other.yml'
    else:
        folder = tmp_path / 'windows-x64'
        manifest = json.loads((folder / 'manifest.json').read_text())
        manifest['version'] = '0.2.0'
        (folder / 'manifest.json').write_text(json.dumps(manifest))
        shutil.make_archive(str(tmp_path / '1'), 'zip', folder)
    with pytest.raises(ValueError):
        promote.promote(api, 'invented/project', '123')
    assert api.mutations == []
