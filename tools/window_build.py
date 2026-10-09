"""Build a local experimental Windows artifact; never modifies release candidates."""
import argparse
from hashlib import sha256
from importlib import metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys

from release import ROOT, archive_bundle, bundle_guide, frozen_check, notices, package_check


from windows_bundle import interop_notices


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
    bundle_guide(ROOT / 'docs/windows-desktop.md', bundle / 'EXPERIMENTAL.md', documentation=False)
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
