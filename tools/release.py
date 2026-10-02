"""Build and verify release candidates. Never creates tags or GitHub releases."""
import argparse
from hashlib import sha256
from importlib import metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open('rb') as handle:
        from hashlib import file_digest
        return file_digest(handle, 'sha256').hexdigest()


def package_check(directory):
    from portfolio_app.privacy import path_problem
    wheels = list(directory.glob('*.whl'))
    sources = list(directory.glob('portfolio_breakdown-*.tar.gz'))
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError('Expected exactly one wheel and one source archive.')
    with zipfile.ZipFile(wheels[0]) as archive:
        names = archive.namelist()
        for name in names:
            path = PurePosixPath(name)
            if '..' in path.parts or path.is_absolute():
                raise ValueError('Unsafe wheel member')
            if name.startswith('portfolio_app/'):
                if path_problem('src/' + name):
                    raise ValueError(f'Unapproved wheel member: {name}')
            elif '.dist-info/' not in name:
                raise ValueError(f'Unexpected wheel member: {name}')
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
    python_license = Path(sysconfig.get_path('stdlib')) / 'LICENSE.txt'
    if not python_license.exists():
        python_license = Path(sys.base_prefix) / 'LICENSE'
    if not python_license.exists():
        raise ValueError('Python license not found in the build interpreter.')
    blocks.append('\n=== Python ===\n' + python_license.read_text(encoding='utf-8'))
    (destination / 'THIRD_PARTY_NOTICES.txt').write_text('\n'.join(blocks), encoding='utf-8')
    (destination / 'dependencies.json').write_text(json.dumps(inventory, indent=2), encoding='utf-8')


def build(output):
    from portfolio_app.settings import BRANDING_ASSETS
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError('Candidate output must be empty; never mix builds.')
    subprocess.run(['uv', 'build', '--out-dir', str(output)], cwd=ROOT, check=True)
    # uv adds this helper to new output directories; it is not a release asset.
    (output / '.gitignore').unlink(missing_ok=True)
    package_check(output)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                    '--distpath', str(ROOT / 'dist/frozen'), str(ROOT / 'packaging/portfolio.spec')], cwd=ROOT, check=True)
    bundle = ROOT / 'dist/frozen/portfolio-app'
    shutil.copy(ROOT / 'LICENSE', bundle / 'LICENSE')
    shutil.copy(ROOT / 'docs/install.md', bundle / 'INSTALL.md')
    notices(bundle)
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    target = 'windows-x64' if os.name == 'nt' else 'linux-x64'
    name = f'portfolio-breakdown-{version}-{target}'
    shutil.make_archive(str(output / name), 'zip' if os.name == 'nt' else 'gztar', bundle.parent, bundle.name)
    shutil.copy(bundle / 'THIRD_PARTY_NOTICES.txt', output / 'THIRD_PARTY_NOTICES.txt')
    shutil.copy(bundle / 'dependencies.json', output / 'dependencies.json')
    # Corresponding source, build instructions and dependency lock accompany every bundle.
    files = {p.name: digest(p) for p in sorted(output.iterdir()) if p.is_file()}
    manifest = dict(schema=1, version=version, platform=target,
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
    parser.add_argument('action', choices=['build', 'check', 'test'])
    parser.add_argument('--directory', type=Path, default=ROOT / 'dist/candidate')
    args = parser.parse_args()
    directory = args.directory.resolve()
    if args.action == 'build':
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
            subprocess.run([sys.executable, str(ROOT / 'tools/package_smoke.py'), str(executable)], check=True)


if __name__ == '__main__':
    main()
