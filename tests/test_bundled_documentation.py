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
