# Build using the locked release environment, on the target OS.
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata
from portfolio_app.settings import BRANDING_ASSETS

root = Path(SPECPATH).parent
assets = root / 'src' / 'portfolio_app' / 'assets'
datas = collect_data_files('streamlit')
datas += [(str(path), 'portfolio_app') for path in (root / 'src/portfolio_app').glob('*.py')]
datas += [(str(root / 'src/portfolio_app/intro_frontend/index.html'), 'portfolio_app/intro_frontend')]
datas += [(str(assets / name), 'portfolio_app/assets') for name in BRANDING_ASSETS if (assets / name).is_file()]
datas += copy_metadata('portfolio-breakdown', recursive=True)
datas += [(str(root / 'LICENSE'), '.')]
analysis = Analysis(
    [str(root / 'packaging' / 'entrypoint.py')], pathex=[str(root / 'src')],
    datas=datas, hiddenimports=collect_submodules('portfolio_app') + collect_submodules('streamlit') + collect_submodules('python_calamine'),
    excludes=['playwright', 'pytest', 'ruff', 'pip_audit'],
)
# Editable-install provenance contains the builder's absolute checkout URL.
# It is unnecessary for runtime version/dependency metadata.
analysis.datas = [entry for entry in analysis.datas if Path(entry[0]).name != 'direct_url.json']
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='portfolio-app',
          console=True, icon=str(assets / 'portfolio-breakdown.png') if (assets / 'portfolio-breakdown.png').exists() else None)
executables = [exe]
if sys.platform == 'win32':
    # Share the same package; keep a console entry for scripts and diagnostics.
    executables.append(EXE(pyz, analysis.scripts, [], exclude_binaries=True,
        name='Portfolio Breakdown', console=False,
        icon=str(assets / 'portfolio-breakdown.png')))
collection = COLLECT(*executables, analysis.binaries, analysis.datas, name='portfolio-app')
