"""Optional real-browser onboarding coverage using exclusively invented holdings."""
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

from portfolio_app.positions import read_snapshot

playwright = pytest.importorskip('playwright.sync_api')


@pytest.fixture
def import_page(tmp_path):
    app = tmp_path / 'app.py'
    app.write_text('''
from pathlib import Path
from portfolio_app.ui import render_app
from portfolio_app.prices import PriceService, UnavailableProvider
render_app(Path(__file__).parent / 'synthetic-portfolio', demo=True,
           price_service=PriceService(UnavailableProvider()))
''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, '-m', 'streamlit', 'run', str(app), '--server.address=127.0.0.1',
         f'--server.port={port}', '--server.headless=true', '--server.fileWatcherType=none',
         '--browser.gatherUsageStats=false'], cwd=tmp_path,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            assert process.poll() is None
            try:
                with urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=1):
                    break
            except OSError:
                time.sleep(.1)
        else:
            pytest.fail('Synthetic import preview did not start')
        with playwright.sync_playwright() as runner:
            browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.goto(f'http://127.0.0.1:{port}')
            page.get_by_role('button', name='Import portfolio — experimental', exact=True).wait_for()
            yield page, tmp_path / 'synthetic-portfolio' / 'holdings.csv'
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_upload_review_chart_allocation_and_listing(import_page):
    page, path = import_page
    page.get_by_role('button', name='Import portfolio — experimental', exact=True).click()
    page.locator('input[type=file]').set_input_files({
        'name': 'invented-holdings.csv', 'mimeType': 'text/csv',
        'buffer': ('Name;Stück/Nennwert Bank;Stück/Nennwert FinanzManager;ISIN;Kurs;Kursdatum;Währung\n'
                   'Invented stock;2,5;1,5;US67066G1040;12,00;2026-01-02;EUR\n').encode(),
    })
    save = page.get_by_role('button', name='Import reviewed positions', exact=True)
    playwright.expect(save).to_be_disabled()
    page.get_by_role('combobox', name='Quantity (required)', exact=True).click()
    page.get_by_role('combobox', name='Quantity (required)', exact=True).fill('Stück/Nennwert Bank')
    page.get_by_role('option', name='Stück/Nennwert Bank', exact=True).click()
    page.get_by_text('Quantities are shares/units and prices are amounts per unit (not nominal values or percent quotes)', exact=True).click()
    playwright.expect(save).to_be_enabled()
    page.get_by_role('tab', name='Overview', exact=True).click()
    playwright.expect(page.get_by_text('Open Positions and choose Add position to get started.', exact=True)).to_be_visible()
    page.get_by_role('tab', name='Positions', exact=True).click()
    playwright.expect(save).to_be_enabled()
    playwright.expect(page.get_by_role('radio', name='Import portfolio', exact=True)).to_be_checked()
    playwright.expect(page.get_by_role('combobox', name='Quantity (required)', exact=True)).to_have_value('Stück/Nennwert Bank')
    save.click()
    page.locator('.js-plotly-plot').first.wait_for()
    assert page.locator('.js-plotly-plot').first.evaluate('el => el.data[0].type') == 'sunburst'
    stored = read_snapshot(path).holdings
    assert stored.shares.tolist() == [2.5]
    assert stored.manual_price.tolist() == [12.]
    assert page.locator('[data-testid="stException"]').count() == 0
    page.get_by_role('button', name='Set up allocation', exact=True).click()
    page.get_by_role('button', name='Enable reviewed allocation', exact=True).wait_for()
    page.get_by_role('button', name='Enable reviewed allocation', exact=True).click()
    page.get_by_role('button', name='Save categories', exact=True).wait_for()
    assert path.with_name('allocation.yaml').exists()
    page.get_by_role('button', name='Connect live prices', exact=True).first.click()
    page.get_by_role('button', name='Search listings', exact=True).click()
    page.get_by_role('combobox', name='Price listing', exact=True).click()
    page.get_by_role('combobox', name='Price listing', exact=True).fill('NVDA')
    page.get_by_role('option', name='NVDA', exact=False).click()
    page.get_by_text('Switch to live prices and clear dated manual prices for this instrument', exact=True).click()
    page.get_by_text('I confirm this is the same instrument/share class and intended exchange listing', exact=True).click()
    page.get_by_role('button', name='Save listing', exact=True).click()
    playwright.expect(page.get_by_text('Listing saved. Live pricing enabled.', exact=True)).to_be_visible()
    linked = read_snapshot(path).holdings
    assert linked.ticker.tolist() == ['NVDA']
    assert linked.manual_price.isna().all()
    assert linked.position_key.tolist() == stored.position_key.tolist()
    page.get_by_role('tab', name='Exposure', exact=True).click()
    playwright.expect(page.locator('[data-testid="stException"]')).to_have_count(0)


def test_cancel_upload_writes_nothing(import_page):
    page, path = import_page
    page.get_by_role('button', name='Import portfolio — experimental', exact=True).click()
    page.locator('input[type=file]').set_input_files({
        'name': 'invented.csv', 'mimeType': 'text/csv', 'buffer': b'Name;Quantity\nInvented asset;2,5\n',
    })
    page.get_by_role('button', name='Import reviewed positions', exact=True).wait_for()
    page.get_by_role('button', name='Cancel import', exact=True).click()
    page.get_by_role('button', name='Import portfolio — experimental', exact=True).wait_for()
    assert not path.exists()
    assert page.locator('[data-testid="stException"]').count() == 0
