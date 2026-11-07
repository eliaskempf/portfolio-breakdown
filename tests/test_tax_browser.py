"""Real browser acceptance using only an invented local portfolio."""
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest
from browser_runtime import browser_session, start_server
from tax_sample import create_tax_sample
from browser_support import StreamlitReruns, settle

playwright = pytest.importorskip('playwright.sync_api')


def test_tax_year_broker_selection_review_and_navigation(tmp_path):
    data = tmp_path/'synthetic-tax-data'
    create_tax_sample(data)
    before = (data/'account-journal.json').read_bytes()
    app = tmp_path/'app.py'
    app.write_text('from pathlib import Path\nfrom portfolio_app.ui import render_app\n'
                   f'render_app(Path({str(data)!r}), demo=True, ignore_selection=True)\n')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    process = start_server([sys.executable, '-m', 'streamlit', 'run', str(app),
        '--server.address=127.0.0.1', f'--server.port={port}', '--server.headless=true',
        '--server.fileWatcherType=none', '--browser.gatherUsageStats=false'], cwd=tmp_path,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            assert process.poll() is None
            try:
                with urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=1):
                    break
            except OSError:
                time.sleep(.1)
        with browser_session() as runner:
            browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1440, 'height': 1050})
            page.streamlit_reruns = StreamlitReruns(page, settle=settle)
            page.goto(f'http://127.0.0.1:{port}')
            page.get_by_role('tab', name='Taxes', exact=True).click(); settle(page)
            page.get_by_role('heading', name='Vorabpauschale', exact=True).wait_for()
            # Direct Streamlit launch deliberately omits theme flags. The app
            # must enforce its own accent before any result controls appear.
            assert page.get_by_role('tab', name='Taxes', exact=True).evaluate('(el) => getComputedStyle(el).color') == 'rgb(84, 112, 198)'
            assert not page.get_by_role('spinbutton', name='Available allowance · 2026 (€)', exact=True).is_visible()
            page.get_by_role('combobox', name='Calculation year', exact=True).click()
            page.get_by_role('option', name='2025', exact=True).click(); settle(page)
            playwright.expect(page.get_by_text('€442.75', exact=True)).to_be_visible()
            # Removing a broker updates the total and resets an entered allowance.
            page.get_by_role('button', name='Tax settings', exact=True).click()
            allowance = page.get_by_role('spinbutton', name='Available allowance · 2026 (€)', exact=True)
            allowance.fill('100'); allowance.press('Enter'); settle(page)
            playwright.expect(page.get_by_text('€55.36', exact=True)).to_be_visible()
            page.get_by_role('button', name='Tax settings', exact=True).click()
            page.get_by_role('button', name='Filter', exact=True).click()
            account = page.get_by_test_id('stMultiSelect').filter(has=page.get_by_text('Broker / account', exact=True))
            account.get_by_role('button', name='Remove Invented broker B').click(); settle(page)
            playwright.expect(page.get_by_text('€354.20', exact=True)).to_be_visible()
            page.get_by_role('button', name='Filter', exact=True).click()
            page.get_by_role('button', name='Tax settings', exact=True).click()
            playwright.expect(allowance).to_have_value('0.00')
            page.get_by_role('button', name='Tax settings', exact=True).click()
            page.get_by_text('Fund details', exact=True).click(); settle(page)
            page.get_by_role('button', name='Edit inputs', exact=True).click(); settle(page)
            page.get_by_text('Price overrides', exact=True).click(); settle(page)
            page.get_by_role('textbox', name='Year-start price override', exact=True).fill('101')
            page.get_by_role('textbox', name='Evidence / assumption', exact=True).fill('Invented browser assumption')
            page.get_by_role('button', name='Save tax inputs', exact=True).click(); settle(page)
            playwright.expect(page.get_by_text('€355.97', exact=True)).to_be_visible()
            page.get_by_role('combobox', name='Calculation year', exact=True).click()
            page.get_by_role('combobox', name='Calculation year', exact=True).press('ArrowUp')
            page.get_by_role('combobox', name='Calculation year', exact=True).press('Enter'); settle(page)
            playwright.expect(page.get_by_text('€560.00', exact=True)).to_be_visible()
            page.get_by_role('tab', name='Overview', exact=True).click(); settle(page)
            assert page.get_by_test_id('stPlotlyChart').count() > 0
            page.get_by_role('tab', name='Taxes', exact=True).click(); settle(page)
            playwright.expect(page.get_by_text('€560.00', exact=True)).to_be_visible()
            assert not page.get_by_test_id('stException').count()
            assert page.get_by_test_id('stMetric').first.bounding_box()['y'] < 500
            for theme in ('Dark', 'Light'):
                page.get_by_role('button', name='Main menu', exact=True).click()
                page.get_by_test_id(f'stMainMenuItem-theme-{theme}').click()
                page.keyboard.press('Escape'); settle(page)
                assert page.get_by_role('tab', name='Taxes', exact=True).evaluate('(el) => getComputedStyle(el).color') == 'rgb(84, 112, 198)'
            page.reload(); settle(page)
            page.get_by_role('tab', name='Taxes', exact=True).click(); settle(page)
            assert page.get_by_role('tab', name='Taxes', exact=True).evaluate('(el) => getComputedStyle(el).color') == 'rgb(84, 112, 198)'
            assert (data/'account-journal.json').read_bytes() == before
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)
