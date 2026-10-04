# Experimental native-window artifacts only; production release.spec is untouched.
from pathlib import Path
from importlib.metadata import version
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata
from portfolio_app.settings import BRANDING_ASSETS

if sys.platform not in {'linux', 'darwin'}:
    raise RuntimeError('Build on the target Linux/macOS host.')
root = Path(SPECPATH).parent
assets = root / 'src/portfolio_app/assets'
datas = collect_data_files('streamlit') + collect_data_files('webview')
datas += [(str(p), 'portfolio_app') for p in (root / 'src/portfolio_app').glob('*.py')]
datas += [(str(assets / n), 'portfolio_app/assets') for n in BRANDING_ASSETS]
datas += [(str(root / 'src/portfolio_app/intro_frontend/index.html'), 'portfolio_app/intro_frontend')]
datas += copy_metadata('portfolio-breakdown', recursive=True) + copy_metadata('pywebview', recursive=True)
datas += [(str(root / 'LICENSE'), '.')]
backend = 'webview.platforms.qt' if sys.platform == 'linux' else 'webview.platforms.cocoa'
analysis = Analysis([str(root / 'packaging/window_entrypoint.py')], pathex=[str(root / 'src')],
    datas=datas, hiddenimports=collect_submodules('portfolio_app') + collect_submodules('streamlit') +
        collect_submodules('python_calamine') + [backend],
    excludes=['playwright', 'pytest', 'ruff', 'pip_audit', 'mkdocs', 'PyQt5', 'PySide2', 'PySide6',
              'gi', 'cefpython3', 'webview.platforms.winforms', 'webview.platforms.gtk',
              *(['webview.platforms.cocoa'] if sys.platform == 'linux' else ['webview.platforms.qt', 'PyQt6', 'qtpy'])])
analysis.datas = [entry for entry in analysis.datas if Path(entry[0]).name != 'direct_url.json']
if sys.platform == 'linux':
    # Host Mesa drivers load into Qt's process. An older bundled C++/GBM runtime
    # can break those drivers on newer Ubuntu releases; use the declared distro
    # dependencies, as we already do for glibc and the graphics driver itself.
    system_runtime = {'libstdc++.so.6', 'libgcc_s.so.1', 'libgbm.so.1'}
    analysis.binaries = [entry for entry in analysis.binaries
                         if Path(entry[0]).name not in system_runtime]
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='portfolio-window',
          console=sys.platform != 'darwin', target_arch='arm64' if sys.platform == 'darwin' else None,
          codesign_identity=None)
executables = [exe]
if sys.platform == 'darwin':
    # The .app remains console-free; a companion preserves stdout for diagnostics
    # and packaged browser checks, as the Windows release already does.
    executables.append(EXE(pyz, analysis.scripts, [], exclude_binaries=True,
        name='portfolio-cli', console=True, target_arch='arm64', codesign_identity=None))
collection = COLLECT(*executables, analysis.binaries, analysis.datas, name='portfolio-window')
if sys.platform == 'darwin':
    app = BUNDLE(collection, name='Portfolio Breakdown Experimental.app',
        icon=str(assets / 'portfolio-breakdown.png'),
        bundle_identifier='io.github.eliaskempf.portfolio-breakdown.experimental',
        info_plist={'CFBundleShortVersionString': version('portfolio-breakdown'),
                    'CFBundleVersion': version('portfolio-breakdown'),
                    'NSHighResolutionCapable': True, 'LSMinimumSystemVersion': '14.0',
                    'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True}})
