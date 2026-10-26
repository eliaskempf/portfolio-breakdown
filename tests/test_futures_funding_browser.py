"""Real-browser funding navigation on invented data only."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

from test_futures_funding_ui import synthetic_funding_workspace
from browser_support import settle

playwright = pytest.importorskip('playwright.sync_api')


@pytest.fixture
def funding_page(tmp_path):
    data = synthetic_funding_workspace(tmp_path)
    script = tmp_path/'preview.py'
    script.write_text('from pathlib import Path\nfrom portfolio_app.ui import render_app\n'
                      f'render_app(Path({str(data)!r}), demo=True, ignore_selection=True)\n')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    process = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run', str(script),
        '--server.address=127.0.0.1', f'--server.port={port}', '--server.headless=true',
        '--server.fileWatcherType=none', '--browser.gatherUsageStats=false'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=1); break
            except OSError:
                time.sleep(.1)
        with playwright.sync_playwright() as browser_tools:
            browser = browser_tools.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1440, 'height': 1050})
            page.set_default_timeout(20000)
            page.goto(f'http://127.0.0.1:{port}')
            page.get_by_role('tab', name='Futures', exact=True).click()
            page.get_by_role('button', name='Run funding study', exact=True).wait_for()
            yield page
            browser.close()
    finally:
        process.terminate(); process.wait(timeout=10)


def fill_number(page, label, value):
    item = page.get_by_role('spinbutton', name=label, exact=True)
    item.fill(str(value)); item.press('Enter')
    settle(page)


def test_funding_stress_outcomes_and_shocks_render(funding_page):
    page = funding_page
    playwright.expect(page.get_by_role('button', name='Run funding study', exact=True)).to_be_disabled()
    fill_number(page, 'Total study reserve (€)', 1000)
    fill_number(page, 'Baseline cash target or loss buffer (€)', 700)
    targets = page.get_by_role('textbox', name='Candidate broker targets (€; comma-separated)', exact=True)
    targets.fill('400, 600'); targets.press('Enter')
    settle(page)
    playwright.expect(page.get_by_role('spinbutton', name='Total study reserve (€)', exact=True)).to_have_value('1000.00')
    playwright.expect(page.get_by_role('spinbutton', name='Baseline cash target or loss buffer (€)', exact=True)).to_have_value('700.00')
    playwright.expect(targets).to_have_value('400, 600')
    page.get_by_role('button', name='Run funding study', exact=True).click()
    playwright.expect(page.locator('.js-plotly-plot')).to_have_count(3)
    assert page.locator('[data-testid="stException"]').count() == 0
    first_chart = page.locator('.js-plotly-plot').first.bounding_box()
    run_button = page.get_by_role('button', name='Run funding study', exact=True).bounding_box()
    assert first_chart['y'] > run_button['y']
    page.get_by_text('Stress & history', exact=True).first.click()
    playwright.expect(page.get_by_text('Actual opening broker cash', exact=True)).to_be_visible()
    playwright.expect(page.locator('.js-plotly-plot')).to_have_count(2)
    page.get_by_text('Price scenarios', exact=True).click()
    playwright.expect(page.get_by_text('Move the market', exact=True)).to_be_visible()
    playwright.expect(page.locator('.js-plotly-plot')).to_have_count(1)
    page.get_by_text('Investment outcomes', exact=True).first.click()
    playwright.expect(page.get_by_text('Futures median at end', exact=True)).to_be_visible()
    settle(page)
    assert page.locator('.js-plotly-plot').count() >= 2
    page.get_by_text('Historical quarters', exact=True).click()
    playwright.expect(page.get_by_text('Load historical comparison data in Settings & data below to draw the past comparison.', exact=True)).to_be_visible()
    page.get_by_text('Funding policies', exact=True).first.click()
    playwright.expect(page.locator('.js-plotly-plot')).to_have_count(3)
    page.set_viewport_size({'width': 750, 'height': 1000})
    settle(page)
    assert page.locator('[data-testid="stException"]').count() == 0
