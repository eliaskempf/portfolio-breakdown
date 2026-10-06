"""Navigation stays usable while synthetic market-data providers are blocked."""
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen

import yaml

from test_ux_browser import playwright, click_slice


def test_navigation_close_and_save_do_not_wait_for_market_data(tmp_path):
    (tmp_path / 'holdings.csv').write_text(
        'position_key,id,name,ticker,shares,acquisition_price,acquisition_currency,bucket_id,within_bucket_target\n'
        'p1,a,Invented Alpha,SYNTH-A,2,80,EUR,core,1\n')
    (tmp_path / 'allocation.yaml').write_text(yaml.safe_dump({
        'version': 2, 'buckets': [dict(id='core', name='Invented Core', target=1)]}))
    cache = tmp_path / '.cache'
    cache.mkdir()
    stamp = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
    (cache / 'prices.json').write_text(json.dumps({'price:SYNTH-A': {
        'quote': dict(price=100, currency='EUR', observed_at=stamp), 'attempted_at': stamp, 'error': ''}}))
    script = tmp_path / 'app.py'
    script.write_text('''
from pathlib import Path
from datetime import datetime, timezone
import time
from portfolio_app import market_data
from portfolio_app.etf_refresh import coordinator as etf_coordinator
from portfolio_app.history import HistoryService, HistoryResult
from portfolio_app.prices import PriceService, Quote
from portfolio_app.ui import render_app
directory = Path(__file__).parent
def wait_for(name):
    for _ in range(1200):
        if (directory / name).exists():
            return
        time.sleep(.05)
    raise TimeoutError('Synthetic request was not released')
class Prices:
    def price(self, ticker):
        (directory / 'price_started').touch()
        wait_for('release_prices')
        return Quote(120, 'EUR', datetime.now(timezone.utc))
class History:
    def history(self, ticker, period):
        with (directory / 'history_calls').open('a') as stream:
            stream.write(period + '\\n')
        (directory / 'history_started').touch()
        wait_for('release_history')
        return HistoryResult(('2026-01-01', '2026-01-02'), (90., 100.), 'EUR', 'fresh')
workspace = str(directory.resolve())
if workspace not in market_data._services:
    market_data._services[workspace] = (PriceService(Prices(), directory / '.cache/prices.json'),
                                      HistoryService(History(), directory / '.cache/history'))
etf_coordinator.schedule = lambda *args, **kwargs: False
render_app(directory)
''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run', str(script),
        '--server.address=127.0.0.1', f'--server.port={port}', '--server.headless=true',
        '--server.fileWatcherType=none', '--browser.gatherUsageStats=false'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    timings = {}
    try:
        for _ in range(100):
            try:
                urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=1)
                break
            except OSError:
                time.sleep(.1)
        with playwright.sync_playwright() as runner:
            browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.goto(f'http://127.0.0.1:{port}')
            page.locator('.js-plotly-plot').first.wait_for()
            playwright.expect(page.get_by_test_id('stMetricValue').first).to_contain_text('200.00')
            assert (tmp_path / 'price_started').exists()
            start = time.monotonic()
            click_slice(page, 'Invented Alpha')
            dialog = page.get_by_role('dialog')
            playwright.expect(dialog.get_by_role('button', name='Close', exact=True).last).to_be_visible()
            timings['open'] = time.monotonic() - start
            # The dialog can render before the background worker starts.
            # Wait for its acknowledgement after the call log has been closed.
            deadline = time.monotonic() + 5
            while not (tmp_path / 'history_started').exists() and time.monotonic() < deadline:
                page.wait_for_timeout(10)
            assert (tmp_path / 'history_started').exists()
            assert (tmp_path / 'history_calls').read_text().splitlines() == ['1y']
            start = time.monotonic()
            dialog.get_by_role('button', name='Close', exact=True).last.click()
            playwright.expect(dialog).not_to_be_visible()
            timings['close'] = time.monotonic() - start
            click_slice(page, 'Invented Alpha')
            playwright.expect(dialog).to_be_visible()
            dialog.get_by_role('button', name='Close', exact=True).last.click()
            playwright.expect(dialog).not_to_be_visible()
            assert (tmp_path / 'history_calls').read_text().splitlines() == ['1y']
            # Closing the dialog schedules a full rerun. Let it finish before
            # clicking navigation, which the rerun can otherwise replace.
            # The synthetic providers remain blocked throughout this wait.
            page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
            start = time.monotonic()
            page.get_by_role('tab', name='Rebalance', exact=True).click()
            page.get_by_role('tab', name='Targets', exact=True).click()
            playwright.expect(page.get_by_role('button', name='Save categories')).to_be_visible()
            timings['navigate_to_targets'] = time.monotonic() - start
            before = (tmp_path / 'allocation.yaml').stat().st_mtime_ns
            start = time.monotonic()
            page.get_by_role('button', name='Save categories').click()
            # Wait for the observable write, not a transient idle indicator
            # which can disappear before the click reaches the server.
            while (tmp_path / 'allocation.yaml').stat().st_mtime_ns == before and time.monotonic() - start < 5:
                page.wait_for_timeout(10)
            playwright.expect(page.get_by_role('button', name='Save categories')).to_be_enabled()
            timings['save'] = time.monotonic() - start
            assert (tmp_path / 'allocation.yaml').stat().st_mtime_ns != before
            # A late history completion must not reopen a dismissed position.
            (tmp_path / 'release_history').touch()
            (tmp_path / 'release_prices').touch()
            page.get_by_role('tab', name='Overview', exact=True).click()
            playwright.expect(page.get_by_test_id('stMetricValue').first).to_contain_text('240.00', timeout=10000)
            playwright.expect(dialog).not_to_be_visible()
            playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
            print('Synthetic browser timings:', {k: round(v, 3) for k, v in timings.items()})
            assert max(timings.values()) < 1.5
            browser.close()
    finally:
        (tmp_path / 'release_prices').touch()
        (tmp_path / 'release_history').touch()
        process.terminate()
        process.wait(timeout=10)
