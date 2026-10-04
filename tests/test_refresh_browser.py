"""The running app refreshes its tables after an offline background update."""
import json
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import yaml
from test_ux_browser import playwright


def test_background_refresh_keeps_ui_usable_and_reloads_finished_snapshot(tmp_path):
    funds = tmp_path / 'etfs'
    funds.mkdir()
    (tmp_path / 'holdings.csv').write_text('id,name,ticker,isin,shares,instrument_type\na,Invented Alpha,SYN-A,ZZ1111111111,1,equity\nf,Invented ETF,SYN-F,ZZ9999999999,1,etf\n')
    (tmp_path / 'prices.json').write_text(json.dumps({'prices': {
        ticker: dict(price=value, currency='EUR', observed_at='2026-09-01T12:00:00+00:00')
        for ticker, value in [('SYN-A', 20), ('SYN-F', 100)]}, 'fx': {}}))
    (funds / 'holdings.csv').write_text('constituent_id,name,ticker,isin,weight\na,Invented Alpha,SYN-A,ZZ1111111111,0.5\n')
    (funds / 'fund.yaml').write_text(yaml.safe_dump(dict(fund_id='invented', name='Invented ETF',
        isin='ZZ9999999999', tickers=['SYN-F'], as_of='2000-01-01', source='https://example.invalid', holdings_file='holdings.csv')))
    script = tmp_path / 'app.py'
    script.write_text('''
from pathlib import Path
from datetime import date
import time
import yaml
from portfolio_app.etf import load_funds
from portfolio_app.etf_sources import SOURCES
from portfolio_app.etf_refresh import coordinator
from portfolio_app.prices import PriceService, StaticProvider
from portfolio_app.ui import render_app
directory = Path(__file__).parent
SOURCES['ZZ9999999999'] = None
def refresh(fund):
    # The test releases this gate only after it has used the rendered UI.
    for _ in range(600):
        if (directory / 'release').exists():
            break
        time.sleep(.05)
    else:
        raise RuntimeError('Synthetic refresh was not released')
    root = directory / 'etfs'
    (root / 'updated.csv').write_text((root / 'holdings.csv').read_text().replace('0.5', '0.8'))
    raw = yaml.safe_load(fund.manifest_path.read_text())
    raw.update(holdings_file='updated.csv', as_of=date.today().isoformat())
    temporary = root / 'manifest.tmp'
    temporary.write_text(yaml.safe_dump(raw))
    temporary.replace(fund.manifest_path)
    return load_funds(root)[0]
coordinator.refresh = refresh
render_app(directory, price_service=PriceService(StaticProvider(directory / 'prices.json')))
''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run', str(script),
        '--server.address=127.0.0.1', f'--server.port={port}', '--server.headless=true',
        '--server.fileWatcherType=none', '--browser.gatherUsageStats=false'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=1)
                break
            except OSError:
                time.sleep(.1)
        with playwright.sync_playwright() as runner:
            browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1440, 'height': 1000}, locale='en-US')
            page.goto(f'http://127.0.0.1:{port}')
            page.get_by_role('tab', name='Exposure', exact=True).click()
            refresh_status = page.locator('.st-key-refresh_status').get_by_text('Discovering and updating ETF holdings', exact=False)
            results = page.locator('.st-key-exposure_results')
            page.get_by_role('button', name='Data & settings', exact=True).click()
            page.get_by_text('ETF refresh & snapshots', exact=True).click()
            playwright.expect(refresh_status).to_be_visible(timeout=15000)
            page.keyboard.press('Escape')
            playwright.expect(results.get_by_role('cell', name='70.00', exact=True)).to_have_count(1)
            page.get_by_role('textbox', name='Search exposure').fill('Alpha')
            page.get_by_role('textbox', name='Search exposure').press('Enter')
            playwright.expect(results).to_contain_text('1 matching assets')
            (tmp_path / 'release').touch()
            playwright.expect(results.get_by_role('table', name='Exposure assets').get_by_role('cell', name='100.00', exact=True)).to_have_count(1, timeout=15000)
            playwright.expect(refresh_status).to_have_count(0)
            playwright.expect(page.get_by_role('textbox', name='Search exposure')).to_have_value('Alpha')
            playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
            status = json.loads((tmp_path / '.cache/etf-refresh/status.json').read_text())
            assert status['ZZ9999999999']['status'] == 'checked'
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)
