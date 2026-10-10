"""Public documentation contracts, using only invented temporary workspaces."""
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from portfolio_app.demo import create_demo_data
from portfolio_app.privacy import DEMO_IMAGE_FILES, path_problem
from portfolio_app.workspace import inventory

ROOT = Path(__file__).resolve().parents[1]
spec = spec_from_file_location('docs_site', ROOT / 'tools/docs_site.py')
docs = module_from_spec(spec)
spec.loader.exec_module(docs)


def cli(*args, check=True):
    return subprocess.run([sys.executable, '-m', 'portfolio_app.app', *map(str, args)],
                          capture_output=True, text=True, check=check)


def test_documented_recovery_commands_preserve_synthetic_files(tmp_path):
    source = create_demo_data(tmp_path / 'invented source ü', live=False)
    before = inventory(source)
    backup, restored, migrated = (tmp_path / name for name in ['backup', 'restored', 'migrated'])
    assert str(source) in cli('--data-dir', source, '--show-data-dir').stdout
    assert 'No managed server' in cli('--data-dir', source, '--stop').stdout
    cli('--data-dir', source, '--backup-to', backup)
    cli('--data-dir', restored, '--restore-from', backup)
    cli('--data-dir', migrated, '--migrate-from', restored)
    assert inventory(source) == inventory(backup) == inventory(restored) == inventory(migrated) == before
    assert cli('--data-dir', restored, '--restore-from', backup, check=False).returncode != 0
    assert inventory(restored) == before


def test_documented_cli_flags_and_version():
    help_text = cli('--help').stdout
    for flag in ['--demo', '--offline-demo', '--foreground', '--no-browser', '--server.port',
                 '--show-data-dir', '--open-data-dir', '--install-shortcut', '--stop']:
        assert flag in help_text
    assert cli('--version').stdout.strip() == docs.tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']


def synthetic_site(path, channel='candidate', revision='a' * 40):
    path.mkdir()
    (path / 'index.html').write_text('<h1 id="home">Invented docs</h1><a href="guide/#topic">Guide</a>', encoding='utf-8')
    (path / 'guide').mkdir()
    (path / 'guide/index.html').write_text('<h1 id="topic">Invented topic</h1><a href="../#home">Home</a>', encoding='utf-8')
    route = f'candidates/{revision}/' if channel == 'candidate' else 'dev/'
    info = dict(schema=1, source_sha=revision, app_version='0.1.0', channel=channel, dirty=False,
                route=route, site_url='https://example.invalid/project/' + route,
                topics={'topic': 'guide/#topic'}, files=docs.file_hashes(path))
    (path / 'build-info.json').write_text(json.dumps(info), encoding='utf-8')
    return path


def update_inventory(site):
    info = json.loads((site / 'build-info.json').read_text(encoding='utf-8'))
    info['files'] = docs.file_hashes(site)
    (site / 'build-info.json').write_text(json.dumps(info), encoding='utf-8')


@pytest.mark.parametrize('link', ['guide/#absent', 'missing/', '/guide/#topic', '../outside/'])
def test_internal_checker_rejects_broken_anchors_and_subpath_escapes(tmp_path, link):
    site = synthetic_site(tmp_path / 'site')
    (site / 'index.html').write_text(f'<a href="{link}">Broken</a>', encoding='utf-8')
    update_inventory(site)
    with pytest.raises(ValueError, match='missing|escapes'):
        docs.check_site(site)


def test_external_availability_is_not_a_test_dependency(tmp_path):
    site = synthetic_site(tmp_path / 'site')
    with (site / 'index.html').open('a', encoding='utf-8') as handle:
        handle.write('<a href="https://unreachable.invalid/">External</a>')
    update_inventory(site)
    docs.check_site(site)


def test_archive_preserves_candidates_and_promotes_exact_bytes(tmp_path):
    first = synthetic_site(tmp_path / 'first')
    second = synthetic_site(tmp_path / 'second', revision='b' * 40)
    archive = tmp_path / 'archive'
    docs.assemble(first, archive)
    docs.assemble(second, archive)
    docs.promote(archive, 'a' * 40, '0.1.0')
    assert docs.file_hashes(archive / 'releases/0.1.0') == docs.file_hashes(first)
    with pytest.raises(ValueError, match='replace immutable release'):
        docs.promote(archive, 'b' * 40, '0.1.0')
    with (first / 'index.html').open('a', encoding='utf-8') as handle:
        handle.write('Changed text')
    update_inventory(first)
    with pytest.raises(ValueError, match='replace immutable candidate'):
        docs.assemble(first, archive)


def test_dirty_candidates_rejected_and_dev_is_replaceable(tmp_path, monkeypatch):
    monkeypatch.setattr(docs, 'git', lambda *args: 'a' * 40 if args[0] == 'rev-parse' else ' M docs/user/index.md')
    with pytest.raises(ValueError, match='clean committed'):
        docs.provenance('candidate')
    site = synthetic_site(tmp_path / 'site', channel='dev')
    archive = tmp_path / 'archive'
    docs.assemble(site, archive)
    with (site / 'index.html').open('a', encoding='utf-8') as handle:
        handle.write('New development text')
    update_inventory(site)
    docs.assemble(site, archive)
    assert docs.file_hashes(archive / 'dev') == docs.file_hashes(site)


def test_default_documentation_prefers_latest_stable_release_over_main_and_prerelease(tmp_path):
    archive = tmp_path / 'archive'
    docs.assemble(synthetic_site(tmp_path / 'main', channel='dev'), archive)
    assert docs.archive_index(archive) == 'dev/'
    for index, version in enumerate(['0.9.0', '0.10.0', '1.0.0rc1']):
        revision = str(index) * 40
        site = synthetic_site(tmp_path / version, revision=revision)
        manifest = site / 'build-info.json'
        info = json.loads(manifest.read_text(encoding='utf-8'))
        info['app_version'] = version
        manifest.write_text(json.dumps(info), encoding='utf-8')
        docs.assemble(site, archive)
        docs.promote(archive, revision, version)
    assert docs.archive_index(archive) == 'releases/0.10.0/'
    assert 'content="0; url=releases/0.10.0/"' in (archive / 'index.html').read_text(encoding='utf-8')
    # Republishing Main must not take over the default from a stable release.
    docs.assemble(synthetic_site(tmp_path / 'new-main', channel='dev'), archive)
    assert docs.archive_index(archive) == 'releases/0.10.0/'


def test_hosted_navigation_preserves_frozen_archive_and_checks_cross_version_links(tmp_path):
    archive = tmp_path / 'archive'
    for channel in ['candidate', 'dev']:
        site = synthetic_site(tmp_path / channel, channel=channel)
        for page in site.rglob('*.html'):
            page.write_text('<article class="md-content__inner md-typeset">'
                            '<blockquote><p><strong>Frozen candidate documentation</strong> · source abc</p></blockquote>'
                            '<blockquote><p>Keep this user guidance.</p></blockquote>' + page.read_text(encoding='utf-8') + '</article>', encoding='utf-8')
        update_inventory(site)
        docs.assemble(site, archive)
    docs.promote(archive, 'a' * 40, '0.1.0')
    before = {p.relative_to(archive): p.read_bytes() for p in archive.rglob('*') if p.is_file()}
    hosted = tmp_path / 'hosted'
    docs.host(archive, hosted)
    assert before == {p.relative_to(archive): p.read_bytes() for p in archive.rglob('*') if p.is_file()}
    page = (hosted / 'releases/0.1.0/guide/index.html').read_text(encoding='utf-8')
    assert 'Frozen candidate documentation' not in page
    assert 'Keep this user guidance.' in page
    assert 'href="../../../dev/guide/index.html"' in page
    assert 'aria-current="page">Latest release · 0.1.0' in page
    info = docs.check_site(hosted / 'releases/0.1.0', link_root=hosted)
    assert (hosted / 'releases/0.1.0/assets/favicon.ico').read_bytes() == docs.FAVICON.read_bytes()
    assert info['site_url'] == 'https://example.invalid/project/releases/0.1.0/'
    assert info['hosting']['archive_manifest_sha256'] == docs.sha256(before[Path('releases/0.1.0/build-info.json')]).hexdigest()
    # Missing destinations fail validation even when cross-version links are allowed.
    (hosted / 'dev/guide/index.html').unlink()
    with pytest.raises(ValueError, match='missing local file'):
        docs.check_site(hosted / 'releases/0.1.0', link_root=hosted)
    with pytest.raises(ValueError, match='new directory'):
        docs.host(archive, archive / 'nested')


def test_hosted_copy_requires_main_and_verified_archive(tmp_path):
    archive = tmp_path / 'archive'
    docs.assemble(synthetic_site(tmp_path / 'candidate'), archive)
    with pytest.raises(ValueError, match='requires Main'):
        docs.host(archive, tmp_path / 'hosted')
    docs.assemble(synthetic_site(tmp_path / 'dev', channel='dev'), archive)
    (archive / 'dev/index.html').write_text('Corrupted', encoding='utf-8')
    with pytest.raises(ValueError, match='checksums'):
        docs.host(archive, tmp_path / 'hosted')


def test_docs_privacy_exceptions_are_narrow():
    for name in ['mkdocs.yml', 'docs/user/index.md', 'tools/docs_site.py', '.github/workflows/docs.yml']:
        assert path_problem(name) is None
    for name in ['docs/user/data/holdings.md', 'docs/user/accounts.csv', 'docs/user/screenshot.png',
                 'docs/unreviewed.md', 'tools/arbitrary.py', '.github/workflows/arbitrary.yml']:
        assert path_problem(name)


@pytest.mark.parametrize('name,content,allowed', [
    ('assets/demo-targets.png', b'\x89PNG\r\n\x1a\n', True),
    ('assets/demo-targets.png', b'not an image', False),
    ('assets/personal-targets.png', b'\x89PNG\r\n\x1a\n', False),
    ('assets/private.csv', b'invented data', False),
])
def test_docs_input_allows_only_reviewed_image_paths(tmp_path, monkeypatch, name, content, allowed):
    from types import SimpleNamespace
    monkeypatch.setattr(docs, 'git', lambda *args: '0')
    path = tmp_path / name
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    config = SimpleNamespace(extra={}, docs_dir=str(tmp_path), theme={})
    if allowed:
        docs.on_config(config)
    else:
        with pytest.raises(ValueError, match='Unapproved public documentation input'):
            docs.on_config(config)


def test_public_site_contract():
    pytest.importorskip('mkdocs', reason='Install the docs group to build the public guide')
    docs.build(ROOT / 'dist/docs-test', 'dev', 'https://example.invalid/nested/project/')
    info = docs.check_site(ROOT / 'dist/docs-test')
    assert info['topics'] == docs.TOPICS
    pages = [name for name in info['files'] if name.endswith('.html')]
    assert len(pages) == 15
    assert 'development/index.html' in pages
    assert 'reference/index.html' in pages
    assert (ROOT / 'dist/docs-test/assets/favicon.ico').read_bytes() == docs.FAVICON.read_bytes()
    assert 'href="assets/favicon.ico"' in (ROOT / 'dist/docs-test/index.html').read_text(encoding='utf-8')
    for name in DEMO_IMAGE_FILES:
        if name.startswith('docs/user/'):
            relative = name.removeprefix('docs/user/')
            assert (ROOT / 'dist/docs-test' / relative).read_bytes() == (ROOT / name).read_bytes()
    assert not any('handoff' in name or 'release-plan' in name or name.startswith('data/') for name in info['files'])
    assert all('source <code>' in (ROOT / 'dist/docs-test' / page).read_text(encoding='utf-8') for page in pages if page != '404.html')


@pytest.mark.skipif(sys.platform == 'win32' or shutil.which('bash') is None,
                    reason='Pages workflow runs in Bash on Ubuntu, not Windows/WSL')
@pytest.mark.parametrize('probe_status,fetch_status', [(0, 0), (2, 0), (128, 0), (1, 0), (0, 128)])
def test_pages_archive_probe_does_not_hide_remote_failures(tmp_path, probe_status, fetch_status):
    import yaml
    workflow = yaml.safe_load((ROOT / '.github/workflows/docs-pages.yml').read_text(encoding='utf-8'))
    step = next(step for step in workflow['jobs']['build']['steps']
                if step.get('name') == 'Restore the durable gh-pages archive')
    # Execute the actual workflow shell with offline Git/auth stand-ins. The
    # failure cases must stop before creating a replacement archive branch.
    commands = tmp_path / 'commands.txt'
    stub = '''
git() {
    printf '%s\\n' "$*" >> "$COMMAND_LOG"
    case "$*" in
        *ls-remote*) return "$PROBE_STATUS" ;;
        *fetch*) return "$FETCH_STATUS" ;;
    esac
}
gh() { :; }
'''
    result = subprocess.run([shutil.which('bash'), '-e', '-o', 'pipefail', '-c', stub + step['run']],
                            cwd=tmp_path, capture_output=True, text=True,
                            env={**os.environ, 'COMMAND_LOG': commands.as_posix(),
                                 'GITHUB_REPOSITORY': 'invented/docs',
                                 'PROBE_STATUS': str(probe_status), 'FETCH_STATUS': str(fetch_status)})
    calls = commands.read_text(encoding='utf-8')
    assert ('checkout --orphan gh-pages' in calls) == (probe_status == 2)
    assert ('checkout -B gh-pages FETCH_HEAD' in calls) == (probe_status == 0 and fetch_status == 0)
    assert result.returncode == (fetch_status if probe_status == 0 else 0 if probe_status == 2 else probe_status)
