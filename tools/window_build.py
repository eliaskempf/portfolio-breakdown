"""Build a local experimental Windows artifact; never modifies release candidates."""
import argparse
from hashlib import sha256
from importlib import metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from release import ROOT, archive_bundle, frozen_check, notices, package_check


def interop_notices(sdk: Path, webview_root: Path, bundle: Path):
    """Match supplied official SDK bytes, then preserve its license and notices."""
    with zipfile.ZipFile(sdk) as archive:
        dlls = [p for p in webview_root.rglob('*.dll')
                if p.name.startswith('Microsoft.Web.WebView2.') or p.name == 'WebView2Loader.dll']
        if not dlls:
            raise ValueError('Missing WebView2 interop DLLs.')
        for dll in dlls:
            candidates = [name for name in archive.namelist() if name.endswith('/' + dll.name)]
            if not any(archive.read(name) == dll.read_bytes() for name in candidates):
                raise ValueError(f'SDK does not match bundled {dll.name}.')
        license_text = archive.read('LICENSE.txt').decode('utf-8')
        notice_text = archive.read('NOTICE.txt').decode('utf-8')
    with (bundle / 'THIRD_PARTY_NOTICES.txt').open('a', encoding='utf-8') as handle:
        handle.write('\n=== Microsoft WebView2 SDK interop (matched against supplied NuGet package) ===\n')
        handle.write(license_text + '\n' + notice_text + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--webview2-sdk', type=Path, required=True,
                        help='Official Microsoft.Web.WebView2 1.0.3856.49 .nupkg, used for matched DLL notices')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/window-prototype')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('Build on native Windows 11 x64; Linux/WSL is not Windows acceptance.')
    if metadata.version('pywebview') != '6.2.1':
        parser.error('Run uv sync --locked --extra window --group release first.')
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error('Output must be empty; do not overwrite an earlier artifact.')
    output.mkdir(parents=True, exist_ok=True)
    source = output / 'source'
    subprocess.run(['uv', 'build', '--out-dir', str(source)], cwd=ROOT, check=True)
    package_check(source)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm',
                    '--distpath', str(output / 'frozen'), '--workpath', str(output / 'build'),
                    str(ROOT / 'packaging/window.spec')], cwd=ROOT, check=True)
    bundle = output / 'frozen/portfolio-window'
    shutil.copy(ROOT / 'LICENSE', bundle / 'LICENSE')
    shutil.copy(ROOT / 'docs/window-session-result.md', bundle / 'EXPERIMENTAL.md')
    notices(bundle)
    # Python wheel metadata does not cover WebView2's managed/native interop DLLs.
    import webview
    dlls = sorted(Path(webview.__file__).parent.rglob('*.dll'))
    if not dlls:
        raise RuntimeError('pywebview interop DLL inventory is empty.')
    inventory = [{'file': str(p.relative_to(Path(webview.__file__).parent)),
                  'sha256': sha256(p.read_bytes()).hexdigest(), 'bytes': p.stat().st_size} for p in dlls]
    (bundle / 'webview-interop.json').write_text(json.dumps(inventory, indent=2), encoding='utf-8')
    interop_notices(args.webview2_sdk, Path(webview.__file__).parent, bundle)
    frozen_check(bundle)
    archive = Path(archive_bundle(bundle, output / 'portfolio-window-experimental-windows-x64', windows=True))
    report = dict(experimental=True, windows_acceptance='UNVERIFIED',
                  source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                  source_clean=not subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT).strip(),
                  lock_sha256=sha256((ROOT / 'uv.lock').read_bytes()).hexdigest(),
                  archive_sha256=sha256(archive.read_bytes()).hexdigest(),
                  archive_bytes=archive.stat().st_size,
                  unpacked_bytes=sum(p.stat().st_size for p in bundle.rglob('*') if p.is_file()),
                  runtime='System Evergreen WebView2; not bundled')
    (output / 'experimental-build.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f'Experimental artifact: {archive}. Native acceptance and interaction checks remain required.')


if __name__ == '__main__':
    main()
