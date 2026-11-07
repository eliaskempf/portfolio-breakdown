"""Browser reuse must not carry a test's storage or pages into the next context."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytest.importorskip('playwright.sync_api')
from browser_runtime import browser_session

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def isolated_pool(browser_pool):
    # Test reuse with a known lifecycle. The session pool may be on its final
    # allowed use after unrelated tests, where replacement is required.
    # Keep the session's Playwright driver; close only its idle browser leases.
    for browser in browser_pool.available.values():
        if browser.is_connected():
            browser.close()
    browser_pool.available.clear()
    browser_pool.uses.clear()
    return browser_pool


def test_reused_browser_has_fresh_contexts_and_storage(isolated_pool):
    with browser_session() as runner:
        first = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
        raw = first.raw
        context = first.new_context()
        context.add_cookies([dict(name='invented', value='first-test', domain='example.invalid', path='/')])
        page = context.new_page()
        page.set_content('<h1>Invented first test</h1>')
        assert context.cookies()
    assert page.is_closed() and not raw.contexts
    with browser_session() as runner:
        second = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
        assert second.raw is raw
        context = second.new_context()
        assert context.cookies() == [] and context.pages == []


def test_reuse_recycles_process_before_trace_storage_grows_without_bound(isolated_pool, monkeypatch):
    monkeypatch.setattr(isolated_pool, 'max_uses', 2)
    raw = []
    for _ in range(3):
        with browser_session() as runner:
            browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'),
                                             args=['--no-sandbox', '--disable-extensions'])
            raw.append(browser.raw)
    assert raw[0] is raw[1] and raw[2] is not raw[0]
    assert not raw[0].is_connected()


@pytest.mark.parametrize('phase', ['setup', 'call', 'teardown', 'pass'])
def test_real_pytest_retains_failure_before_browser_cleanup(tmp_path, phase):
    pytest.importorskip('playwright.sync_api')
    project = tmp_path/'project'
    project.mkdir()
    (project/'conftest.py').write_text(f'''
import importlib.util, sys
sys.path.insert(0, {str(ROOT/'tests')!r})
spec = importlib.util.spec_from_file_location('repository_fixtures', {str(ROOT/'tests/conftest.py')!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
browser_pool = module.browser_pool
browser_diagnostics = module.browser_diagnostics
pytest_runtest_makereport = module.pytest_runtest_makereport
''')
    (project/'test_invented_browser.py').write_text(f'''
import os, sys
import pytest
from browser_runtime import browser_session, start_server
@pytest.fixture
def page(tmp_path):
    log = (tmp_path/'supplied.log').open('w') if {phase!r} == 'call' else None
    child = start_server([sys.executable, '-c', 'print("Invented server output", flush=True)'], stdout=log)
    child.wait(timeout=5)
    if log: log.close()
    with browser_session() as runner:
        browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
        try:
            page = browser.new_page()
            page.set_content('<h1>Invented evidence</h1>')
            if {phase!r} == 'setup': raise RuntimeError('Invented setup failure')
            yield page
            if {phase!r} == 'teardown': raise RuntimeError('Invented teardown failure')
        finally:
            browser.close()
def test_failure(page):
    if {phase!r} == 'call': raise RuntimeError('Invented call failure')
''')
    evidence = tmp_path/'evidence'
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q'], cwd=project,
                            env=dict(os.environ, PORTFOLIO_BROWSER_EVIDENCE=str(evidence), PORTFOLIO_REQUIRE_BROWSER='0'),
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == (0 if phase == 'pass' else 1), result.stdout + result.stderr
    reports = list(evidence.glob('*/failure.json'))
    if phase == 'pass':
        assert not reports and not list(evidence.glob('*'))
        return
    assert len(reports) == 1, result.stdout + result.stderr
    report = json.loads(reports[0].read_text())
    assert report['failures'][0]['phase'] == phase
    assert 'Invented '+phase+' failure' in report['failures'][0]['error']
    folder = reports[0].parent
    assert list(folder.glob('trace-*.zip')) and list(folder.glob('page-*.png'))
    assert 'Invented server output' in (folder/('supplied-server-0.log' if phase == 'call' else 'server-0.log')).read_text()
    assert json.loads((folder/'lifecycle.json').read_text())
