"""Windows packaging prerequisites and matched Microsoft interop notices."""
from pathlib import Path
import zipfile
from hashlib import sha256
import json
import os
import shutil
import subprocess
from urllib.request import urlopen

SDK_VERSION = '1.0.3856.49'
SDK_SHA256 = 'bc0f76eb911b569838dc4aa8f8d325269b966bedb592863d26211aef3a099f1a'
SDK_URL = f'https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/{SDK_VERSION}/microsoft.web.webview2.{SDK_VERSION}.nupkg'
BOOTSTRAPPER_URL = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703'


def download(url, destination):
    with urlopen(url, timeout=90) as response:
        destination.write_bytes(response.read())


def compiler_path():
    """Find the compiler installation, not a Chocolatey PATH shim.

    Notices must come from the same installation used to compile the installer.
    An explicit override is authoritative and must include its adjacent license.
    """
    override = os.environ.get('PORTFOLIO_ISCC')
    candidates = [override] if override else [
        shutil.which('ISCC'),
        str(Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Inno Setup 6/ISCC.exe'),
        str(Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Inno Setup 6/ISCC.exe'),
    ]
    for candidate in candidates:
        if candidate:
            compiler = Path(candidate)
            if compiler.is_file() and compiler.with_name('license.txt').is_file():
                return str(compiler)
    raise ValueError('Inno Setup compiler and adjacent license.txt not found. '
                     'Set PORTFOLIO_ISCC to the installed ISCC.exe, not a launcher shim.')


def signed_bootstrapper(cache):
    cache.mkdir(parents=True, exist_ok=True)
    bootstrapper = cache / 'MicrosoftEdgeWebview2Setup.exe'
    if not bootstrapper.exists():
        download(BOOTSTRAPPER_URL, bootstrapper)
    env = os.environ | {'PORTFOLIO_BOOTSTRAPPER': str(bootstrapper)}
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
        "$s = Get-AuthenticodeSignature -LiteralPath $env:PORTFOLIO_BOOTSTRAPPER; "
        "if ($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation(?:,|$)') { exit 1 }"],
        env=env, check=True)
    return bootstrapper


def prepare_notices(bundle, cache):
    cache.mkdir(parents=True, exist_ok=True)
    sdk = cache / f'webview2-{SDK_VERSION}.nupkg'
    if not sdk.exists():
        download(SDK_URL, sdk)
    if sha256(sdk.read_bytes()).hexdigest() != SDK_SHA256:
        raise ValueError('WebView2 SDK checksum mismatch.')
    # Audit the DLLs actually shipped, not only the installed build dependencies.
    interop_notices(sdk, bundle, bundle)
    dlls = sorted((bundle / '_internal').rglob('*.dll'))
    inventory = [{'file': p.relative_to(bundle).as_posix(),
                  'sha256': sha256(p.read_bytes()).hexdigest(), 'bytes': p.stat().st_size}
                 for p in dlls if 'webview' in p.as_posix().lower() or p.name.startswith('Python.Runtime')]
    (bundle / 'webview-interop.json').write_text(json.dumps(inventory, indent=2), encoding='utf-8')
    license_path = Path(compiler_path()).parent / 'license.txt'
    with (bundle / 'THIRD_PARTY_NOTICES.txt').open('a', encoding='utf-8') as handle:
        handle.write('\n=== Inno Setup installer engine ===\n' + license_path.read_text(encoding='utf-8'))


def build_installer(root, bundle, output, version):
    """Build one per-user installer; never install it on the build host."""
    cache = root / 'dist/windows-prerequisites'
    bootstrapper = signed_bootstrapper(cache)
    # Evergreen bootstrappers change. Require a valid Microsoft Authenticode
    # signature instead of trusting unversioned downloaded executable bytes.
    subprocess.run([compiler_path(), '/Qp', f'/DAppVersion={version}', f'/DBundleDir={bundle}',
                    f'/DOutputDir={output}', f'/DBootstrapper={bootstrapper}',
                    str(root / 'packaging/windows.iss')], check=True)
    installer = output / f'portfolio-breakdown-{version}-windows-x64-setup.exe'
    if not installer.is_file():
        raise ValueError('Windows installer was not produced.')
    return {'webview2_bootstrapper_sha256': sha256(bootstrapper.read_bytes()).hexdigest(),
            'webview2_sdk_version': SDK_VERSION, 'webview2_sdk_sha256': SDK_SHA256}

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


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Prepare the Windows CI runtime for native smoke tests.')
    parser.add_argument('--ensure-runtime', action='store_true', required=True)
    parser.parse_args()
    from portfolio_app.window import runtime_available
    if not runtime_available():
        bootstrapper = signed_bootstrapper(Path('dist/windows-prerequisites').resolve())
        subprocess.run([str(bootstrapper), '/silent', '/install'], check=True, timeout=300)
        if not runtime_available():
            raise RuntimeError('WebView2 installation did not produce an available runtime.')
