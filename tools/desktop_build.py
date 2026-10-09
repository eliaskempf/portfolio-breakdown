"""Build isolated experimental installers. Never publish or promote a release."""
import argparse
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

from portfolio_app.settings import BRANDING_ASSETS
from release import ROOT, digest, frozen_check, notices, package_check

DEB_NAME = 'portfolio-breakdown-experimental'
APP_NAME = 'Portfolio Breakdown Experimental.app'
LINUX_DEPENDS = ('libc6 (>= 2.35), libstdc++6, libgcc-s1, libgbm1, libgl1, libegl1, libopengl0, libnss3, '
                 'libasound2 | libasound2t64, libxkbcommon0, libxkbcommon-x11-0, '
                 'libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, '
                 'libxcb-render-util0, libxcb-xinerama0, libxcb-randr0, libxcb-shape0, '
                 'libxcb-xfixes0, libxcomposite1, libxdamage1, libxrandr2, libxtst6, '
                 'libdbus-1-3, libfontconfig1, libwayland-client0, libwayland-server0')


def linux_package(bundle, stage, output, version):
    destination = stage / 'opt' / DEB_NAME
    shutil.copytree(bundle, destination)
    control = stage / 'DEBIAN/control'
    control.parent.mkdir(parents=True)
    control.write_text(f'Package: {DEB_NAME}\nVersion: {version}\nArchitecture: amd64\n'
                       'Maintainer: Portfolio Breakdown contributors\nSection: utils\nPriority: optional\n'
                       f'Depends: {LINUX_DEPENDS}\n'
                       'Description: Experimental local portfolio desktop window\n'
                       ' Bundled Python application; user portfolio data is preserved on removal.\n', encoding='utf-8')
    launcher = stage / 'usr/bin' / DEB_NAME
    launcher.parent.mkdir(parents=True)
    launcher.write_text(f'#!/bin/sh\nexec /opt/{DEB_NAME}/portfolio-window "$@"\n', encoding='utf-8')
    launcher.chmod(0o755)
    desktop = stage / 'usr/share/applications' / (DEB_NAME + '.desktop')
    desktop.parent.mkdir(parents=True)
    desktop.write_text('[Desktop Entry]\nType=Application\nName=Portfolio Breakdown Experimental\n'
                       f'Exec={DEB_NAME}\nIcon={DEB_NAME}\nTerminal=false\nCategories=Office;Finance;\n', encoding='utf-8')
    icon = stage / 'usr/share/icons/hicolor/256x256/apps' / (DEB_NAME + '.png')
    icon.parent.mkdir(parents=True)
    shutil.copy(ROOT / 'src/portfolio_app/assets/portfolio-breakdown.png', icon)
    artifact = output / f'{DEB_NAME}_{version}_amd64.deb'
    subprocess.run(['dpkg-deb', '-Zgzip', '-z6', '--root-owner-group', '--build', str(stage), str(artifact)], check=True)
    return artifact


def macos_candidate(output, bundle, artifact, source, version):
    """Stage exact built bytes for optional, separately gated release promotion."""
    destination = output / 'candidate'
    destination.mkdir()
    docs = bundle / 'documentation'
    info = json.loads((docs / 'build-info.json').read_text(encoding='utf-8'))
    for path in [artifact, *source.glob('*.whl'), *source.glob('*.tar.gz'),
                 bundle / 'THIRD_PARTY_NOTICES.txt', bundle / 'dependencies.json']:
        shutil.copyfile(path, destination / path.name)
    shutil.make_archive(str(destination / f'portfolio-breakdown-{version}-docs'), 'zip', docs)
    files = {p.name: digest(p) for p in sorted(destination.iterdir())}
    manifest = dict(schema=2, version=version, platform='macos-arm64',
                    experimental=True, developer_id_signed=False, notarized=False,
                    manual_installation_verified=False,
                    documentation=dict(source_sha=info['source_sha'], route=info['route'],
                        build_info_sha256=digest(docs / 'build-info.json')),
                    source_clean=info['dirty'] is False, commit=info['source_sha'],
                    icon_ready=all((ROOT / 'src/portfolio_app/assets' / name).is_file() for name in BRANDING_ASSETS),
                    run_id=os.environ.get('GITHUB_RUN_ID', 'local'),
                    run_attempt=os.environ.get('GITHUB_RUN_ATTEMPT', '1'),
                    lock_sha256=digest(ROOT / 'uv.lock'), python=platform.python_version(), files=files)
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    (destination / 'SHA256SUMS').write_text(''.join(f'{value}  {name}\n' for name, value in files.items()) +
        f'{digest(destination / "manifest.json")}  manifest.json\n', encoding='utf-8')


def build(output):
    target = (sys.platform, platform.machine().lower())
    if target not in {('linux', 'x86_64'), ('darwin', 'arm64')}:
        raise ValueError('Supported build hosts: Linux x64 or Apple Silicon macOS.')
    if sys.platform == 'darwin' and sys.version_info < (3, 13):
        raise ValueError('Build macOS with UV_PYTHON=3.13 (waitid is required).')
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError('Output must be empty; never mix candidate builds.')
    source = output / 'source'
    subprocess.run(['uv', 'build', '--out-dir', str(source)], cwd=ROOT, check=True)
    package_check(source)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm',
                    '--distpath', str(output / 'frozen'), '--workpath', str(output / 'build'),
                    str(ROOT / 'packaging/posix-window.spec')], cwd=ROOT, check=True)
    bundle = output / 'frozen/portfolio-window'
    notices(bundle)
    shutil.copy(ROOT / 'LICENSE', bundle / 'LICENSE')
    shutil.copy(ROOT / 'docs/desktop-experiment.md', bundle / 'EXPERIMENTAL.md')
    from docs_site import build as build_docs
    build_docs(bundle / 'documentation', 'candidate', 'https://eliaskempf.github.io/portfolio-breakdown/')
    frozen_check(bundle)
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    if sys.platform == 'linux':
        artifact = linux_package(bundle, output / 'deb-root', output, version)
    else:
        app = output / 'frozen' / APP_NAME
        resources = app / 'Contents/Resources'
        for name in ['THIRD_PARTY_NOTICES.txt', 'dependencies.json', 'LICENSE', 'EXPERIMENTAL.md']:
            shutil.copy(bundle / name, resources / name)
        shutil.copytree(bundle / 'documentation', resources / 'documentation')
        # Ad-hoc integrity signing is not Developer ID signing/notarization.
        subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(app)], check=True)
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
        stage = output / 'dmg-root'
        stage.mkdir()
        shutil.copytree(app, stage / APP_NAME, symlinks=True)
        (stage / 'Applications').symlink_to('/Applications')
        artifact = output / f'portfolio-breakdown-experimental-{version}-macos-arm64.dmg'
        subprocess.run(['hdiutil', 'create', '-volname', 'Portfolio Breakdown Experimental',
                        '-srcfolder', str(stage), '-ov', '-format', 'UDZO', str(artifact)], check=True)
    for name in ['THIRD_PARTY_NOTICES.txt', 'dependencies.json']:
        shutil.copy(bundle / name, output / name)
    report = dict(experimental=True, publishable=False, platform=f'{target[0]}-{target[1]}',
                  commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                  source_clean=not subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT).strip(),
                  lock_sha256=digest(ROOT / 'uv.lock'), python=platform.python_version(),
                  system=platform.platform(), developer_id_signed=False, notarized=False,
                  artifact=artifact.name, artifact_sha256=digest(artifact), artifact_bytes=artifact.stat().st_size,
                  native_acceptance='UNVERIFIED',
                  files={str(p.relative_to(output)): digest(p) for p in [artifact, *source.glob('*.whl'), *source.glob('*.tar.gz')]})
    (output / 'experimental-build.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    (output / 'SHA256SUMS').write_text(''.join(f'{v}  {k}\n' for k, v in report['files'].items()), encoding='utf-8')
    if sys.platform == 'darwin':
        macos_candidate(output, bundle, artifact, source, version)
    print(f'Experimental artifact: {artifact}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/desktop-experiment')
    build(parser.parse_args().output.resolve())
