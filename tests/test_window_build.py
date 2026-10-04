"""The prototype build inventory uses only deliberately invented SDK archives."""
import importlib.util
from pathlib import Path
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
# tools are release-only modules, not application imports.
spec = importlib.util.spec_from_file_location('window_build', ROOT / 'tools/window_build.py')
module = importlib.util.module_from_spec(spec)
release_spec = importlib.util.spec_from_file_location('release', ROOT / 'tools/release.py')
release = importlib.util.module_from_spec(release_spec)
release_spec.loader.exec_module(release)


@pytest.mark.parametrize('matching', [True, False])
def test_interop_inventory_requires_exact_sdk_bytes(tmp_path, monkeypatch, matching):
    monkeypatch.syspath_prepend(str(ROOT / 'tools'))
    monkeypatch.setitem(sys.modules, 'release', release)
    spec.loader.exec_module(module)
    sdk = tmp_path / 'synthetic-sdk.nupkg'
    with zipfile.ZipFile(sdk, 'w') as archive:
        archive.writestr('lib/net462/Microsoft.Web.WebView2.Core.dll', b'synthetic SDK bytes')
        archive.writestr('LICENSE.txt', 'Invented SDK license')
        archive.writestr('NOTICE.txt', 'Invented SDK notices')
    webview = tmp_path / 'webview'
    webview.mkdir()
    (webview / 'Microsoft.Web.WebView2.Core.dll').write_bytes(
        b'synthetic SDK bytes' if matching else b'different synthetic bytes')
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    if matching:
        module.interop_notices(sdk, webview, bundle)
        content = (bundle / 'THIRD_PARTY_NOTICES.txt').read_text()
        assert 'Invented SDK license' in content and 'Invented SDK notices' in content
    else:
        with pytest.raises(ValueError, match='does not match'):
            module.interop_notices(sdk, webview, bundle)
        assert not (bundle / 'THIRD_PARTY_NOTICES.txt').exists()


def test_public_document_and_packaging_paths_are_exactly_allowlisted():
    from portfolio_app.privacy import path_problem
    for path in ['packaging/window.spec', 'packaging/window_entrypoint.py',
                 'tools/window_build.py', 'docs/window-session-result.md']:
        assert path_problem(path) is None
    assert path_problem('docs/window-private-report.md')
    assert path_problem('packaging/private.log')
