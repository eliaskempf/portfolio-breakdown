"""Build/check public docs and assemble an archive without rewriting frozen versions.

No application data is read. Build inputs are docs/user and reviewed MkDocs config.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from html import escape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tomllib
from urllib.parse import unquote, urljoin, urlsplit

from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parents[1]
FAVICON = ROOT / 'src/portfolio_app/assets/favicon.ico'
TOPICS = {
    'home': 'index.html#home',
    'install': 'install/#install',
    'startup': 'install/#launch-stop',
    'demo': 'getting-started/#demo',
    'first-portfolio': 'getting-started/#empty',
    'targets': 'allocation/#targets',
    'classifications': 'allocation/#classifications',
    'positions': 'positions/#positions',
    'buy-ins': 'positions/#buy-ins',
    'purchases': 'positions/#purchases',
    'balances': 'positions/#balances',
    'import': 'import/#import',
    'live-listings': 'import/#live-listings',
    'exposure': 'exposure/#filters',
    'etfs': 'exposure/#look-through',
    'etf-other': 'exposure/#other',
    'etf-setup': 'exposure/#sources',
    'bond-funds': 'exposure/#bonds',
    'company-merges': 'exposure/#merges',
    'prices': 'performance/#valuation',
    'performance': 'performance/#performance',
    'history': 'performance/#history',
    'risk': 'analytics/#risk',
    'metrics': 'analytics/#metrics',
    'fund-fees': 'analytics/#sources',
    'rebalance': 'rebalance/#planning',
    'rebalance-options': 'rebalance/#constraints',
    'recovery': 'storage/#backup',
    'privacy': 'storage/#privacy',
    'troubleshooting': 'troubleshooting/#troubleshooting',
}


def git(*args: str) -> str:
    return subprocess.check_output(['git', '-C', str(ROOT), *args], text=True).strip()


def provenance(channel: str = 'dev') -> dict:
    revision = git('rev-parse', 'HEAD')
    dirty = bool(git('status', '--porcelain', '--untracked-files=normal'))
    if channel != 'dev' and dirty:
        raise ValueError('Immutable candidate docs require a clean committed checkout')
    return {
        'schema': 1, 'source_sha': revision,
        'app_version': tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version'],
        'channel': channel, 'dirty': dirty,
        'route': 'dev/' if channel == 'dev' else f'candidates/{revision}/',
    }


# MkDocs hooks also apply to `uv run mkdocs build --strict`.
def on_config(config):
    from portfolio_app.privacy import DEMO_IMAGE_FILES, icon_problem
    config.extra.setdefault('build', provenance())
    config.theme['favicon'] = 'assets/favicon.ico'
    os.environ['SOURCE_DATE_EPOCH'] = git('show', '-s', '--format=%ct', 'HEAD')
    source = Path(config.docs_dir)
    for path in source.rglob('*'):
        if path.is_symlink():
            raise ValueError(f'Unapproved public documentation input: {path.relative_to(source)}')
        if path.is_file() and path.suffix != '.md':
            name = 'docs/user/' + path.relative_to(source).as_posix()
            if name not in DEMO_IMAGE_FILES or icon_problem(name, path.read_bytes()):
                raise ValueError(f'Unapproved public documentation input: {path.relative_to(source)}')
    return config


def on_page_markdown(markdown, *, config, **kwargs):
    info = config.extra['build']
    label = 'Development' if info['channel'] == 'dev' else 'Frozen candidate'
    dirty = ' · uncommitted local changes' if info['dirty'] else ''
    return (f'> **{label} documentation** · app {info["app_version"]} · '
            f'source `{info["source_sha"]}`{dirty}\n\n' + markdown)


def file_hashes(directory: Path) -> dict[str, str]:
    result = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError('Symlinks are not allowed in documentation output')
        if path.is_file() and path.name != 'build-info.json':
            result[path.relative_to(directory).as_posix()] = sha256(path.read_bytes()).hexdigest()
    return result


def on_post_build(config, **kwargs):
    directory = Path(config.site_dir)
    (directory / 'assets').mkdir(exist_ok=True)
    shutil.copyfile(FAVICON, directory / 'assets/favicon.ico')
    info = dict(config.extra['build'], site_url=config.site_url, topics=TOPICS,
                files=file_hashes(directory))
    (directory / 'build-info.json').write_text(json.dumps(info, indent=2) + '\n', encoding='utf-8', newline='\n')


class Document(HTMLParser):
    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.links: list[str] = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get('id'):
            self.ids.add(values['id'])
        for key in ('href', 'src'):
            if values.get(key):
                self.links.append(values[key])


def check_site(directory: Path, *, link_root: Path | None = None) -> dict:
    directory = directory.resolve()
    info = json.loads((directory / 'build-info.json').read_text())
    if info['files'] != file_hashes(directory):
        raise ValueError('Generated inventory/checksums do not match build-info.json')
    base = info['site_url']
    base_url = urlsplit(base)
    if not base.endswith('/') or not base_url.netloc:
        raise ValueError('site_url must be an absolute URL ending with /')
    # Hosted navigation may cross versions, but must stay inside the public archive.
    link_root = directory if link_root is None else link_root.resolve()
    prefix = directory.relative_to(link_root).parts
    link_base = urlsplit(urljoin(base, '../' * len(prefix)))
    documents = {path.relative_to(directory).as_posix(): Document(path.read_text(encoding='utf-8'))
                 for path in directory.rglob('*.html')}
    errors = []

    def check_link(page: str, link: str):
        target = urlsplit(urljoin(base + page, link))
        if target.scheme not in {'http', 'https'} or target.netloc != base_url.netloc:
            return  # External availability is intentionally not checked.
        if not target.path.startswith(link_base.path):
            errors.append(f'{page}: link escapes documentation version: {link}')
            return
        relative = unquote(target.path[len(link_base.path):])
        relative = relative + 'index.html' if not relative or relative.endswith('/') else relative
        path = (link_root / relative).resolve()
        if not path.is_relative_to(link_root) or not path.is_file():
            errors.append(f'{page}: missing local file: {link}')
        elif target.fragment and path.suffix == '.html' and unquote(target.fragment) not in Document(path.read_text(encoding='utf-8')).ids:
            errors.append(f'{page}: missing anchor: {link}')

    for page, document in documents.items():
        # MkDocs 404 deliberately uses site-absolute navigation and is validated too.
        for link in document.links:
            check_link(page, link)
    for link in info['topics'].values():
        check_link('index.html', link)
    if errors:
        raise ValueError('\n'.join(errors))
    print(f'Checked {len(documents)} HTML pages, {len(info["topics"])} help topics and {len(info["files"])} files')
    return info


def build(directory: Path, channel: str, base_url: str) -> None:
    from mkdocs.commands.build import build as mkdocs_build
    from mkdocs.config import load_config
    info = provenance(channel)
    directory = directory.resolve()
    if not directory.is_relative_to(ROOT / 'dist'):
        raise ValueError('Build output must be under this checkout’s ignored dist/ directory')
    config = load_config(str(ROOT / 'mkdocs.yml'), site_dir=str(directory), strict=True,
                         site_url=base_url.rstrip('/') + '/' + info['route'], extra={'build': info})
    mkdocs_build(config)
    check_site(directory)


def assemble(site: Path, archive: Path) -> None:
    """Update dev or add a content-checked immutable candidate to an existing archive."""
    info = check_site(site)
    route = info['route']
    expected = 'dev/' if info['channel'] == 'dev' else f'candidates/{info["source_sha"]}/'
    if route != expected or not re.fullmatch(r'[a-f0-9]{40}', info['source_sha']):
        raise ValueError('Invalid documentation route/source identity')
    if info['channel'] != 'dev' and info['dirty']:
        raise ValueError('Cannot archive a dirty candidate')
    destination = archive / route
    if destination.exists():
        if info['channel'] != 'dev':
            if (destination / 'build-info.json').read_bytes() != (site / 'build-info.json').read_bytes():
                raise ValueError('Refusing to replace immutable candidate docs')
            check_site(destination)
            archive_index(archive)
            return
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(site, destination)
    (archive / '.nojekyll').touch()
    archive_index(archive)


def archive_index(archive: Path) -> str:
    releases = []
    for manifest in (archive / 'releases').glob('*/build-info.json'):
        try:
            version = Version(manifest.parent.name)
        except InvalidVersion:
            continue
        if not version.is_prerelease and not version.is_devrelease:
            releases.append((version, manifest.parent.name))
    if releases:
        route = f'releases/{max(releases)[1]}/'
    elif (archive / 'dev/build-info.json').is_file():
        route = 'dev/'
    else:
        # First candidate publication can precede the first Main publication.
        candidates = sorted((archive / 'candidates').glob('*/build-info.json'))
        if not candidates:
            raise ValueError('No documentation version to open')
        route = candidates[-1].parent.relative_to(archive).as_posix() + '/'
    target = escape(route, quote=True)
    (archive / 'index.html').write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta http-equiv="refresh" content="0; url={target}">'
        '<title>Portfolio Breakdown documentation</title>'
        f'<a href="{target}">Open documentation</a></html>', encoding='utf-8', newline='\n')
    return route


def host(archive: Path, destination: Path) -> None:
    """Add hosted navigation to a separate copy; preserve frozen/bundled bytes."""
    archive, destination = archive.resolve(), destination.resolve()
    if destination.exists() or destination.is_relative_to(archive) or archive.is_relative_to(destination):
        raise ValueError('Hosted output must be a new directory outside the archive')
    manifests = sorted(archive.rglob('build-info.json'))
    for manifest in manifests:
        check_site(manifest.parent)
    if not (archive / 'dev/build-info.json').is_file():
        raise ValueError('Hosted navigation requires Main documentation')
    shutil.copytree(archive, destination, ignore=shutil.ignore_patterns('.git'))
    latest = archive_index(destination)
    choices = [('dev/', 'Main')]
    if latest.startswith('releases/'):
        choices.insert(0, (latest, f'Latest release · {latest.split("/")[1]}'))
    for manifest in manifests:
        relative = manifest.parent.relative_to(archive)
        site = destination / relative
        info = json.loads(manifest.read_text())
        base = info['site_url'].removesuffix(info['route'])
        info['site_url'] = base + relative.as_posix() + '/'
        (site / 'assets').mkdir(exist_ok=True)
        shutil.copyfile(FAVICON, site / 'assets/favicon.ico')
        for name in info['files']:
            if not name.endswith('.html'):
                continue
            page = site / name
            source = page.read_text(encoding='utf-8')
            favicon = Path(os.path.relpath(site / 'assets/favicon.ico', page.parent)).as_posix()
            source = re.sub(r'(<link\s+rel="icon"\s+href=")[^"]+("[^>]*>)',
                            lambda match: match[1] + favicon + match[2], source)
            links = []
            for route, label in choices:
                target = destination / route / name
                if not target.is_file():
                    target = destination / route / 'index.html'
                href = Path(os.path.relpath(target, page.parent)).as_posix()
                current = ' aria-current="page"' if relative.as_posix() + '/' == route else ''
                links.append(f'<a href="{escape(href, quote=True)}"{current}>{escape(label)}</a>')
            label = ''
            if relative.as_posix() + '/' not in {route for route, _ in choices}:
                kind = 'Release' if relative.parts[0] == 'releases' else 'Candidate'
                label = f'<span>{kind} · {escape(info["app_version"])}</span>'
            navigation = '<nav class="docs-versions" aria-label="Documentation version">' + label + ''.join(links) + '</nav>'
            style = ('<style>.docs-versions{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;margin:0 0 1.5rem;font-size:.7rem}'
                     '.docs-versions a,.docs-versions span{padding:.3rem .65rem;border-radius:.3rem;border:1px solid var(--md-default-fg-color--lightest)}'
                     '.docs-versions [aria-current]{background:var(--md-accent-fg-color--transparent);color:var(--md-typeset-a-color)}'
                     '.docs-versions span{color:var(--md-default-fg-color--light)}</style>')
            # This exact generated banner is presentation; provenance stays in metadata.
            source = re.sub(r'<blockquote>\s*<p><strong>(?:Frozen candidate|Development) documentation</strong>.*?</blockquote>', '', source, count=1, flags=re.DOTALL)
            source, count = re.subn(r'(<article\b[^>]*>)', lambda match: match[0] + style + navigation, source, count=1)
            if count != 1:
                raise ValueError(f'Missing documentation article: {relative / name}')
            page.write_text(source, encoding='utf-8', newline='\n')
        info['hosting'] = {'archive_manifest_sha256': sha256(manifest.read_bytes()).hexdigest()}
        info['files'] = file_hashes(site)
        (site / 'build-info.json').write_text(json.dumps(info, indent=2) + '\n', encoding='utf-8', newline='\n')
    for manifest in destination.rglob('build-info.json'):
        check_site(manifest.parent, link_root=destination)


def promote(archive: Path, source_sha: str, version: str) -> None:
    """Copy frozen candidate bytes to a release URL; do not rebuild or overwrite."""
    if not re.fullmatch(r'[a-f0-9]{40}', source_sha) or not re.fullmatch(r'[0-9][A-Za-z0-9._-]*', version):
        raise ValueError('Expected a full source SHA and safe release version')
    candidate = archive / 'candidates' / source_sha
    info = check_site(candidate)
    if info['source_sha'] != source_sha or info['dirty'] or info['channel'] != 'candidate':
        raise ValueError('Release requires the matching clean candidate')
    if info['app_version'] != version:
        raise ValueError('Release version does not match candidate app version')
    destination = archive / 'releases' / version
    if destination.exists():
        if (destination / 'build-info.json').read_bytes() != (candidate / 'build-info.json').read_bytes():
            raise ValueError('Refusing to replace immutable release docs')
        check_site(destination)
        archive_index(archive)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(candidate, destination)
    archive_index(archive)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    command = sub.add_parser('build')
    command.add_argument('--channel', choices=['dev', 'candidate'], default='dev')
    command.add_argument('--site-dir', type=Path, default=ROOT / 'dist/docs-site')
    command.add_argument('--base-url', default='https://eliaskempf.github.io/portfolio-breakdown/')
    command = sub.add_parser('check')
    command.add_argument('site', type=Path)
    command = sub.add_parser('assemble')
    command.add_argument('site', type=Path)
    command.add_argument('archive', type=Path)
    command = sub.add_parser('promote')
    command.add_argument('archive', type=Path)
    command.add_argument('--source-sha', required=True)
    command.add_argument('--version', required=True)
    command = sub.add_parser('host')
    command.add_argument('archive', type=Path)
    command.add_argument('destination', type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'build':
            build(args.site_dir, args.channel, args.base_url)
        elif args.action == 'check':
            check_site(args.site)
        elif args.action == 'assemble':
            assemble(args.site, args.archive)
        elif args.action == 'promote':
            promote(args.archive, args.source_sha, args.version)
        else:
            host(args.archive, args.destination)
    except ValueError as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
