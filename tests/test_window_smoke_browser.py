"""Delayed native-window navigation with a real browser and synthetic pages."""
import importlib.util
import os
from pathlib import Path

import pytest

playwright = pytest.importorskip('playwright.sync_api')
spec = importlib.util.spec_from_file_location('window_smoke', Path(__file__).resolve().parents[1] / 'tools/window_smoke.py')
window_smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(window_smoke)


def test_native_smoke_receives_navigation_after_attaching():
    with playwright.sync_playwright() as runner:
        browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
        try:
            context = browser.new_context()
            context.route('http://example.invalid/**', lambda route: route.fulfill(
                content_type='text/html', body='<p>Invented native startup</p>'))
            page = context.new_page()
            page.goto('http://example.invalid/starting')
            page.evaluate("setTimeout(() => location.href = '/__portfolio_window__', 250)")
            assert page.url.endswith('/starting')
            found = window_smoke.wait_for_window_page(browser, timeout=5000)
            assert found is page
            assert found.url.endswith('/__portfolio_window__')
            playwright.expect(found.get_by_text('Invented native startup', exact=True)).to_be_visible()
        finally:
            browser.close()
