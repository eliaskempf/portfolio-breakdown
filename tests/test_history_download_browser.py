"""The empty-history action is visible and produces a chart with synthetic prices."""
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

from portfolio_app.account_journal import save_journal, load_journal
from test_history_download import setup
from browser_support import settle

playwright = pytest.importorskip('playwright.sync_api')


@pytest.mark.parametrize('mode', ['manual', 'position', 'portfolio'])
def test_empty_history_download_in_browser(tmp_path, mode):
    automatic = mode != 'manual'
    journal, _ = setup(tmp_path)
    save_journal(tmp_path, journal, None)
    script = tmp_path/'preview.py'
    script.write_text(f'''
from pathlib import Path
import streamlit as st
from datetime import date, timedelta
from decimal import Decimal
from portfolio_app.account_journal import load_journal
from portfolio_app.history_observations import HistoryScope, Observation
from portfolio_app.history_ui import render_history_results
import portfolio_app.personal_market as market
root = Path({str(tmp_path)!r})
def prices(ticker, identity, start, end):
    import time
    time.sleep(1)
    with (root/'downloads.txt').open('a') as handle: handle.write('request\\n')
    stamps = [start, (date.today()-timedelta(days=5)).isoformat(), end]
    return tuple(Observation(str(i), 'price', identity, stamp, Decimal(10+i), 'EUR', source='Invented feed')
                 for i, stamp in enumerate(stamps))
market.fetch_security_prices = prices
market.fetch_fx = lambda source, target, start, end: (Observation('fx', 'fx', source+'/'+target, start, Decimal(1), source, target=target, source='Invented FX'),)
if {mode!r} == 'position':
    from portfolio_app.position_ui import request_position, render_position_dialog
    from portfolio_app.positions import read_snapshot
    st.button('Open position', on_click=request_position, args=('position-0',))
    render_position_dialog(root/'holdings.csv', read_snapshot(root/'holdings.csv'), {{ }}, demo=False)
else:
    def toggle(): st.session_state.percent = not st.session_state.get('percent', False)
    render_history_results(root, load_journal(root)[0], HistoryScope(('position-0',)), key='test_',
        automatic={automatic!r}, percent=st.session_state.get('percent', False), on_toggle_gain=toggle)
''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    process = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run', str(script),
        '--server.address=127.0.0.1', f'--server.port={port}', '--server.headless=true',
        '--server.fileWatcherType=none', '--browser.gatherUsageStats=false'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            assert process.poll() is None
            try:
                urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=1); break
            except OSError:
                time.sleep(.1)
        with playwright.sync_playwright() as runner:
            browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1440, 'height': 1100})
            page.goto(f'http://127.0.0.1:{port}')
            if mode == 'position':
                page.get_by_role('button', name='Open position', exact=True).click()
                page.get_by_role('dialog').wait_for()
                page.get_by_role('dialog').evaluate('e => window.originalDialog=e')
            button = page.get_by_role('button', name='Load prices and FX', exact=True)
            if not automatic:
                playwright.expect(button).to_be_visible(); settle(page)
                playwright.expect(page.locator('.js-plotly-plot')).to_have_count(0)
                button.click(); settle(page)
            else:
                playwright.expect(button).to_have_count(0)
            playwright.expect(page.locator('.js-plotly-plot')).to_have_count(1, timeout=15000)
            assert page.locator('.js-plotly-plot').evaluate('e => e.data[0].x.length') >= 2
            playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
            assert load_journal(tmp_path)[0] == journal
            if mode == 'position':
                assert page.get_by_role('dialog').evaluate('e => e===window.originalDialog')
                page.locator('.js-plotly-plot').evaluate('e => window.originalChart=e')
                page.wait_for_timeout(2500)
                assert page.locator('.js-plotly-plot').evaluate('e => e===window.originalChart')
            if mode == 'portfolio':
                page.get_by_role('button', name='Show gain as percentage', exact=True).click(); settle(page)
                playwright.expect(page.get_by_role('button', name='Show gain as amount', exact=True)).to_be_visible()
            page.reload(); settle(page)
            if mode == 'position':
                page.get_by_role('button', name='Open position', exact=True).click(); settle(page)
            playwright.expect(page.locator('.js-plotly-plot')).to_have_count(1, timeout=15000)
            assert (tmp_path/'downloads.txt').read_text().count('request') == 1
            browser.close()
    finally:
        process.terminate(); process.wait(timeout=10)
