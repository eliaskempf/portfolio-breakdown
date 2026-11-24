"""Build the production PyInstaller spec with a measurement-only runtime hook."""
from pathlib import Path
import subprocess
import sys
from tempfile import NamedTemporaryFile


def build(root, output):
    harness = Path(__file__).resolve().parent
    spec = root / 'packaging' / ('portfolio.spec' if sys.platform == 'win32' else 'posix-window.spec')
    source = spec.read_text(encoding='utf-8')
    marker = 'analysis = Analysis('
    if source.count(marker) != 1:
        raise ValueError('Unrecognized packaging spec')
    # Preserve SPECPATH so package discovery, assets and the runtime match the release.
    with NamedTemporaryFile(dir=spec.parent, suffix='.spec', delete=False, mode='w', encoding='utf-8') as handle:
        temp = Path(handle.name)
        handle.write(source.replace(marker, f"sys.path.insert(0, {str(harness.parent)!r})\n" + marker).replace(
            "pathex=[str(root / 'src')]", f"runtime_hooks=[{str(harness/'runtime_hook.py')!r}], pathex=[str(root / 'src'), {str(harness.parent)!r}]").replace(
            "hiddenimports=collect_submodules('portfolio_app')", "hiddenimports=['perf_benchmark.instrumentation', 'perf_benchmark.renderer'] + collect_submodules('portfolio_app')"))
    try:
        subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--distpath', str(output),
            '--workpath', str(output.parent / 'benchmark-build'), str(temp)], cwd=root, check=True)
    finally:
        temp.unlink()


if __name__ == '__main__':
    build(Path.cwd(), Path(sys.argv[1]).resolve())
