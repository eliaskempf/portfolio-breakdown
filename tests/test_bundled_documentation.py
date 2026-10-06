"""The local guide server must never serve a portfolio workspace."""
import json
import sys
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from portfolio_app.documentation import documentation_server, guide_url


@pytest.mark.parametrize('platform', ['linux', 'win32', 'darwin'])
def test_frozen_help_serves_only_the_bundled_public_site(tmp_path, monkeypatch, platform):
    bundle = tmp_path / 'app'
    docs = bundle / ('Contents/Resources/documentation' if platform == 'darwin' else 'documentation')
    docs.mkdir(parents=True)
    (docs / 'build-info.json').write_text(json.dumps({'source_sha': 'a' * 40}))
    (docs / 'index.html').write_text('<h1>Synthetic public guide</h1>')
    (bundle / 'synthetic-private.txt').write_text('Invented private sentinel')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'platform', platform)
    executable = bundle / ('Contents/MacOS/portfolio-window' if platform == 'darwin' else 'Portfolio Breakdown.exe')
    monkeypatch.setattr(sys, 'executable', str(executable))
    monkeypatch.setenv('PORTFOLIO_DOCS_URL', 'https://example.invalid/previous/')
    with documentation_server():
        assert guide_url().startswith('http://127.0.0.1:')
        assert urlopen(guide_url(), timeout=3).read() == b'<h1>Synthetic public guide</h1>'
        with pytest.raises(HTTPError) as error:
            urlopen(guide_url('../synthetic-private.txt'), timeout=3)
        assert error.value.code == 404
    assert guide_url() == 'https://example.invalid/previous/'


@pytest.mark.parametrize('platform', ['linux', 'win32', 'darwin'])
@pytest.mark.parametrize('missing', ['build-info.json', 'index.html'])
def test_incomplete_bundle_never_falls_back_to_other_guides(tmp_path, monkeypatch, platform, missing):
    bundle = tmp_path / 'app'
    docs = bundle / ('Contents/Resources/documentation' if platform == 'darwin' else 'documentation')
    docs.mkdir(parents=True)
    for name in ('build-info.json', 'index.html'):
        if name != missing:
            (docs / name).write_text('{}' if name.endswith('.json') else '<h1>Invented guide</h1>')
    executable = bundle / ('Contents/MacOS/portfolio-window' if platform == 'darwin' else 'portfolio-app')
    monkeypatch.setattr(sys, 'executable', str(executable))
    monkeypatch.setattr(sys, 'platform', platform)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setenv('PORTFOLIO_DOCS_URL', 'https://example.invalid/unmatched/')
    with documentation_server():
        assert guide_url() is None
        assert guide_url('getting-started/') is None
        # Even a separately supervised interpreter must see the unavailable marker.
        monkeypatch.setattr(sys, 'frozen', False)
        assert guide_url() is None
    assert guide_url() == 'https://example.invalid/unmatched/'


def test_default_help_distinguishes_source_and_packaged_launch(monkeypatch):
    monkeypatch.delenv('PORTFOLIO_DOCS_URL', raising=False)
    monkeypatch.setattr(sys, 'frozen', False, raising=False)
    assert guide_url('getting-started/') == 'https://eliaskempf.github.io/portfolio-breakdown/dev/getting-started/'
    monkeypatch.setattr(sys, 'frozen', True)
    assert guide_url() is None


def test_unavailable_help_explains_recovery_and_keeps_tour(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('PORTFOLIO_DOCS_URL', '')
    app = AppTest.from_string(
        'from pathlib import Path\n'
        'from portfolio_app.workspace_ui import app_header\n'
        f'app_header(Path({str(tmp_path)!r}), None, demo=False)\n'
    ).run()
    assert not app.exception
    assert 'Reinstall the complete app' in app.warning[0].value
    assert not app.get('link_button')
    assert any(button.label == 'Take the tour' for button in app.button)
