# Experimental Windows-only build. Production portfolio.spec is unchanged.
import sys
if sys.platform != "win32":
    raise RuntimeError("Build the window prototype on native Windows.")
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata
from portfolio_app.settings import BRANDING_ASSETS

root = Path(SPECPATH).parent
assets = root / 'src' / 'portfolio_app' / 'assets'
datas = collect_data_files('streamlit')
datas += [(str(path), 'portfolio_app') for path in (root / 'src/portfolio_app').glob('*.py')]
datas += [(str(assets / name), 'portfolio_app/assets') for name in BRANDING_ASSETS if (assets / name).is_file()]
datas += copy_metadata('portfolio-breakdown', recursive=True)
datas += copy_metadata('pywebview', recursive=True)
datas += collect_data_files('webview')
datas += [(str(root / 'LICENSE'), '.')]
analysis = Analysis(
    [str(root / 'packaging' / 'window_entrypoint.py')], pathex=[str(root / 'src')],
    datas=datas, hiddenimports=collect_submodules('portfolio_app') + collect_submodules('streamlit') + collect_submodules('python_calamine'),
    excludes=['playwright', 'pytest', 'ruff', 'pip_audit', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'qtpy', 'gi', 'cefpython3'],
)
# Editable-install provenance contains the builder's absolute checkout URL.
# It is unnecessary for runtime version/dependency metadata.
analysis.datas = [entry for entry in analysis.datas if Path(entry[0]).name != 'direct_url.json']
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='portfolio-window',
          console=False, icon=str(assets / 'portfolio-breakdown.png') if (assets / 'portfolio-breakdown.png').exists() else None)
collection = COLLECT(exe, analysis.binaries, analysis.datas, name='portfolio-window')
