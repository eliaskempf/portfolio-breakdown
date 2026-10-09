"""Build and verify release candidates. Never creates tags or GitHub releases."""
import argparse
from hashlib import sha256
from importlib import metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
import tomllib
import zipfile
from urllib.parse import quote, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def bundle_guide(source: Path, destination: Path, *, documentation: bool = True) -> None:
    """Relocate a guide's links to bundled HTML or the exact source revision."""
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()

    def relocate(match):
        label, target = match.groups()
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or not parsed.path:
            return match.group(0)
        path = (source.parent / unquote(parsed.path)).resolve()
        relative = path.relative_to(ROOT)
        if not path.is_file():
            raise ValueError(f'Missing source guide link: {relative}')
        fragment = '#' + parsed.fragment if parsed.fragment else ''
        if documentation and relative.parts[:2] == ('docs', 'user') and path.suffix == '.md':
            page = relative.relative_to('docs/user').with_suffix('')
            html = 'index.html' if page.as_posix() == 'index' else page.as_posix() + '/index.html'
            target = 'documentation/' + html + fragment
        else:
            target = f'https://github.com/eliaskempf/portfolio-breakdown/blob/{revision}/{quote(relative.as_posix())}' + fragment
            label += ' (source; internet required)'
        return f'[{label}]({target})'

    content = re.sub(r'\[([^\]\n]+)\]\(([^\s)]+)\)', relocate, source.read_text(encoding='utf-8'))
    destination.write_text(content, encoding='utf-8')


def preflight():
    """Check source packages, documentation and licenses before expensive GUI builds."""
    from docs_site import build as build_docs
    (ROOT / 'dist').mkdir(exist_ok=True)
    with TemporaryDirectory(prefix='portfolio-release-preflight-') as temporary:
        directory = Path(temporary)
        notices(directory)
        subprocess.run(['uv', 'build', '--out-dir', str(directory / 'source')], cwd=ROOT, check=True)
        package_check(directory / 'source')
        # Disposable validation also works before committing a local fix. Real
        # builds still require clean, immutable candidate documentation.
        # The documentation builder deliberately confines output to ignored dist/.
        with TemporaryDirectory(prefix='preflight-docs-', dir=ROOT / 'dist') as docs:
            build_docs(Path(docs), 'dev', 'https://eliaskempf.github.io/portfolio-breakdown/')
    print('Release preflight passed: interpreter/dependency notices, source archives and documentation.', flush=True)


def digest(path):
    with path.open('rb') as handle:
        from hashlib import file_digest
        return file_digest(handle, 'sha256').hexdigest()


def package_check(directory):
    from portfolio_app.privacy import ICON_FILES, DEMO_IMAGE_FILES, content_problem, icon_problem, path_problem
    def check_content(name, content):
        reason = icon_problem(name, content) if name in ICON_FILES | DEMO_IMAGE_FILES else content_problem(content)
        if reason:
            raise ValueError(f'Unapproved package content: {name}: {reason}')
    wheels = list(directory.glob('*.whl'))
    sources = list(directory.glob('portfolio_breakdown-*.tar.gz'))
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError('Expected exactly one wheel and one source archive.')
    with zipfile.ZipFile(wheels[0]) as archive:
        names = archive.namelist()
        for name in names:
            if name.endswith('/'):
                continue
            path = PurePosixPath(name)
            if '..' in path.parts or path.is_absolute():
                raise ValueError('Unsafe wheel member')
            if name.startswith('portfolio_app/'):
                if path_problem('src/' + name):
                    raise ValueError(f'Unapproved wheel member: {name}')
            elif '.dist-info/' not in name:
                raise ValueError(f'Unexpected wheel member: {name}')
            check_content('src/' + name if name.startswith('portfolio_app/') else name, archive.read(name))
        if 'portfolio_app/ui.py' not in names or not any(n.endswith('/LICENSE') for n in names):
            raise ValueError('Wheel is missing application source or license.')
    with tarfile.open(sources[0]) as archive:
        for member in archive.getmembers():
            if member.isdir():
                continue
            path = PurePosixPath(member.name)
            relative = '/'.join(path.parts[1:])
            if not member.isfile() or '..' in path.parts or path.is_absolute():
                raise ValueError('Unsafe source archive member')
            if relative != 'PKG-INFO' and path_problem(relative):
                raise ValueError(f'Unapproved source archive member: {relative}')
            check_content(relative, archive.extractfile(member).read())
    print('Wheel and source archive content checks passed.')


def notices(destination):
    """Retain installed dependency license texts; no workspace files are read."""
    blocks = ['Portfolio Breakdown: GPL-3.0-only. See LICENSE.',
              'Third-party distributions retain their own licenses. Inventory includes build tools.']
    inventory = []
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata['Name'].lower()):
        name = dist.metadata['Name']
        if name.lower() == 'portfolio-breakdown':
            continue
        license_name = dist.metadata.get('License-Expression') or dist.metadata.get('License', 'See upstream license')
        inventory.append({'name': name, 'version': dist.version, 'license': license_name})
        blocks.append(f'\n=== {name} {dist.version} ===\n{license_name}')
        for item in dist.files or []:
            if any(part.lower().startswith(('license', 'copying', 'notice')) for part in item.parts):
                path = Path(dist.locate_file(item))
                if path.is_file() and path.suffix.lower() not in {'.py', '.pyc', '.so', '.dll'}:
                    try:
                        blocks.append(f'\n--- {item} ---\n' + path.read_text(encoding='utf-8'))
                    except UnicodeError:
                        pass
    import sysconfig
    # Windows CPython installs LICENSE.txt beside python.exe, outside Lib.
    # Use the base interpreter rather than the project's virtual environment.
    candidates = (Path(sysconfig.get_path('stdlib')) / 'LICENSE.txt',
                  Path(sys.base_prefix) / 'LICENSE', Path(sys.base_prefix) / 'LICENSE.txt')
    python_license = next((path for path in candidates if path.is_file()), None)
    if python_license is None:
        raise ValueError('Python license not found in the build interpreter.')
    blocks.append('\n=== Python ===\n' + python_license.read_text(encoding='utf-8'))
    (destination / 'THIRD_PARTY_NOTICES.txt').write_text('\n'.join(blocks), encoding='utf-8')
    (destination / 'dependencies.json').write_text(json.dumps(inventory, indent=2), encoding='utf-8')


def frozen_check(bundle):
    """Audit first-party frozen files and exclude local installation provenance."""
    from portfolio_app.privacy import ICON_FILES, content_problem, icon_problem, path_problem
    if any(bundle.rglob('direct_url.json')):
        raise ValueError('Frozen bundle contains local installation provenance.')
    package = bundle / '_internal' / 'portfolio_app'
    if not (package / 'ui.py').is_file():
        raise ValueError('Frozen bundle is missing application source.')
    for file in package.rglob('*'):
        if not file.is_file():
            continue
        name = 'src/portfolio_app/' + file.relative_to(package).as_posix()
        reason = path_problem(name)
        if not reason:
            content = file.read_bytes()
            reason = icon_problem(name, content) if name in ICON_FILES else content_problem(content)
        if reason:
            raise ValueError(f'Unapproved frozen application content: {name}: {reason}')
    for metadata in (bundle / '_internal').glob('portfolio_breakdown-*.dist-info'):
        for file in metadata.rglob('*'):
            if file.is_file() and (reason := content_problem(file.read_bytes())):
                raise ValueError(f'Unapproved frozen application metadata: {file.name}: {reason}')
    print('Frozen application source and installation-provenance checks passed.')


def archive_bundle(bundle, destination, *, windows):
    """Linux archives must not disclose the builder's user/group names or IDs."""
    if windows:
        return shutil.make_archive(str(destination), 'zip', bundle.parent, bundle.name)
    def neutral_owner(member):
        member.uid = member.gid = 0
        member.uname = member.gname = ''
        return member
    archive = str(destination) + '.tar.gz'
    with tarfile.open(archive, 'w:gz') as output:
        output.add(bundle, arcname=bundle.name, filter=neutral_owner)
    return archive


def build(output):
    from portfolio_app.settings import BRANDING_ASSETS
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError('Candidate output must be empty; never mix builds.')
    subprocess.run(['uv', 'build', '--out-dir', str(output)], cwd=ROOT, check=True)
    # uv adds this helper to new output directories; it is not a release asset.
    (output / '.gitignore').unlink(missing_ok=True)
    package_check(output)
    from docs_site import build as build_docs, check_site
    docs_directory = ROOT / 'dist/docs-site'
    build_docs(docs_directory, 'candidate', 'https://eliaskempf.github.io/portfolio-breakdown/')
    docs_info = check_site(docs_directory)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                    '--distpath', str(ROOT / 'dist/frozen'), str(ROOT / 'packaging/portfolio.spec')], cwd=ROOT, check=True)
    bundle = ROOT / 'dist/frozen/portfolio-app'
    shutil.copy(ROOT / 'LICENSE', bundle / 'LICENSE')
    bundle_guide(ROOT / 'docs/install.md', bundle / 'INSTALL.md')
    notices(bundle)
    windows_metadata = {}
    if os.name == 'nt':
        from windows_bundle import prepare_notices
        prepare_notices(bundle, ROOT / 'dist/windows-prerequisites')
    shutil.copytree(docs_directory, bundle / 'documentation', dirs_exist_ok=True)
    # Frozen help uses the exact bundled site, including before Pages publication.
    help_metadata = dict(source_sha=docs_info['source_sha'], route=docs_info['route'],
                         site_url=docs_info['site_url'], topics=docs_info['topics'])
    (bundle / '_internal/portfolio_app/documentation-build.json').write_text(
        json.dumps(help_metadata, indent=2), encoding='utf-8')
    frozen_check(bundle)
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    target = 'windows-x64' if os.name == 'nt' else 'linux-x64'
    name = f'portfolio-breakdown-{version}-{target}'
    archive_bundle(bundle, output / name, windows=os.name == 'nt')
    if os.name == 'nt':
        from windows_bundle import build_installer
        windows_metadata = build_installer(ROOT, bundle, output, version)
    # Identical documentation bytes accompany both platforms and stay separately
    # downloadable for Pages archival/promotion without rebuilding the guides.
    shutil.make_archive(str(output / f'portfolio-breakdown-{version}-docs'), 'zip', docs_directory)
    shutil.copy(bundle / 'THIRD_PARTY_NOTICES.txt', output / 'THIRD_PARTY_NOTICES.txt')
    shutil.copy(bundle / 'dependencies.json', output / 'dependencies.json')
    # Corresponding source, build instructions and dependency lock accompany every bundle.
    files = {p.name: digest(p) for p in sorted(output.iterdir()) if p.is_file()}
    manifest = dict(schema=2, version=version, platform=target,
                    documentation=dict(source_sha=docs_info['source_sha'], route=docs_info['route'],
                        build_info_sha256=digest(docs_directory / 'build-info.json')),
                    windows=windows_metadata,
                    source_clean=not subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip(),
                    icon_ready=all((ROOT / "src/portfolio_app/assets" / name).is_file() for name in BRANDING_ASSETS),
                    commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                    run_id=os.environ.get('GITHUB_RUN_ID', 'local'),
                    run_attempt=os.environ.get('GITHUB_RUN_ATTEMPT', '1'),
                    lock_sha256=digest(ROOT / 'uv.lock'), python=platform.python_version(), files=files)
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    (output / 'SHA256SUMS').write_text(''.join(f'{value}  {name}\n' for name, value in files.items()) +
                                         f'{digest(output / "manifest.json")}  manifest.json\n', encoding='utf-8')


def extract_bundle(directory, destination):
    bundles = list(directory.glob('*-windows-x64.zip')) + list(directory.glob('*-linux-x64.tar.gz'))
    if len(bundles) != 1:
        raise ValueError('Expected one platform bundle.')
    if bundles[0].suffix == '.zip':
        with zipfile.ZipFile(bundles[0]) as archive:
            archive.extractall(destination)
    else:
        with tarfile.open(bundles[0]) as archive:
            archive.extractall(destination, filter='data')
    return destination / 'portfolio-app' / ('portfolio-app.exe' if os.name == 'nt' else 'portfolio-app')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['preflight', 'build', 'check', 'test'])
    parser.add_argument('--directory', type=Path, default=ROOT / 'dist/candidate')
    args = parser.parse_args()
    directory = args.directory.resolve()
    if args.action == 'preflight':
        preflight()
    elif args.action == 'build':
        build(directory)
    elif args.action == 'check':
        package_check(directory)
    else:
        from promote import verify_candidate
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
        verify_candidate(directory, commit=manifest['commit'], run_id=manifest['run_id'],
                         run_attempt=manifest['run_attempt'], platform=manifest['platform'],
                         require_publishable=False)
        from tempfile import TemporaryDirectory
        with TemporaryDirectory(prefix='portfolio-package-') as temporary:
            executable = extract_bundle(directory, Path(temporary))
            frozen_check(executable.parent)
            subprocess.run([sys.executable, str(ROOT / 'tools/package_smoke.py'), str(executable)], check=True)
            if os.name == 'nt':
                subprocess.run([sys.executable, str(ROOT / 'tools/window_smoke.py'),
                                str(executable.with_name('Portfolio Breakdown.exe'))], check=True)


if __name__ == '__main__':
    main()
