"""Public documentation contracts, using only invented temporary workspaces."""
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import subprocess
import sys

import pytest

from portfolio_app.demo import create_demo_data
from portfolio_app.privacy import path_problem
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
    assert cli('--version').stdout.strip() == docs.tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']


def synthetic_site(path, channel='candidate', revision='a' * 40):
    path.mkdir()
    (path / 'index.html').write_text('<h1 id="home">Invented docs</h1><a href="guide/#topic">Guide</a>')
    (path / 'guide').mkdir()
    (path / 'guide/index.html').write_text('<h1 id="topic">Invented topic</h1><a href="../#home">Home</a>')
    route = f'candidates/{revision}/' if channel == 'candidate' else 'dev/'
    info = dict(schema=1, source_sha=revision, app_version='0.1.0', channel=channel, dirty=False,
                route=route, site_url='https://example.invalid/project/' + route,
                topics={'topic': 'guide/#topic'}, files=docs.file_hashes(path))
    (path / 'build-info.json').write_text(json.dumps(info))
    return path


def update_inventory(site):
    info = json.loads((site / 'build-info.json').read_text())
    info['files'] = docs.file_hashes(site)
    (site / 'build-info.json').write_text(json.dumps(info))


@pytest.mark.parametrize('link', ['guide/#absent', 'missing/', '/guide/#topic', '../outside/'])
def test_internal_checker_rejects_broken_anchors_and_subpath_escapes(tmp_path, link):
    site = synthetic_site(tmp_path / 'site')
    (site / 'index.html').write_text(f'<a href="{link}">Broken</a>')
    update_inventory(site)
    with pytest.raises(ValueError, match='missing|escapes'):
        docs.check_site(site)


def test_external_availability_is_not_a_test_dependency(tmp_path):
    site = synthetic_site(tmp_path / 'site')
    with (site / 'index.html').open('a') as handle:
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
    with (first / 'index.html').open('a') as handle:
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
    with (site / 'index.html').open('a') as handle:
        handle.write('New development text')
    update_inventory(site)
    docs.assemble(site, archive)
    assert docs.file_hashes(archive / 'dev') == docs.file_hashes(site)


def test_docs_privacy_exceptions_are_narrow():
    for name in ['mkdocs.yml', 'docs/user/index.md', 'tools/docs_site.py', '.github/workflows/docs.yml']:
        assert path_problem(name) is None
    for name in ['docs/user/data/holdings.md', 'docs/user/accounts.csv', 'docs/user/screenshot.png',
                 'docs/unreviewed.md', 'tools/arbitrary.py', '.github/workflows/arbitrary.yml']:
        assert path_problem(name)


def test_public_site_contract():
    pytest.importorskip('mkdocs', reason='Install the docs group to build the public guide')
    docs.build(ROOT / 'dist/docs-test', 'dev', 'https://example.invalid/nested/project/')
    info = docs.check_site(ROOT / 'dist/docs-test')
    assert info['topics'] == docs.TOPICS
    pages = [name for name in info['files'] if name.endswith('.html')]
    assert len(pages) == 13
    assert not any('handoff' in name or 'release-plan' in name or name.startswith('data/') for name in info['files'])
    assert all('source <code>' in (ROOT / 'dist/docs-test' / page).read_text() for page in pages if page != '404.html')
