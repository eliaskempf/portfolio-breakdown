"""Market controls and source details follow pending synthetic quotes locally."""
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

from browser_runtime import browser_session, start_server
from browser_support import settle

playwright = pytest.importorskip('playwright.sync_api')


@pytest.mark.parametrize('kind', ['position', 'position-revised', 'futures'])
def test_background_history_publishes_controls_and_details(tmp_path, kind):
    script = tmp_path/'preview.py'
    script.write_text("""
from pathlib import Path
import streamlit as st
from portfolio_app.market.history import HistoryResult
from portfolio_app.interface.views.history import market_history_ui as market
from portfolio_app.interface.views.derivatives.futures_chart_ui import render_futures_chart
from portfolio_app.derivatives.derivative_market import make_contract

root = Path(__file__).parent
class History:
    def get(self, ticker, period, **kwargs):
        if self.pending(ticker, period):
            if KIND == 'position-revised' and ticker == 'SYNTH':
                return HistoryResult(('2024-01-01','2024-01-02'), (100.,110.), 'EUR', 'stale')
            return HistoryResult(note='Invented pending history')
        currency = {'^FTSE': 'GBP', '^GDAXI': 'EUR', 'USDEUR=X': 'EUR', 'GBPEUR=X': 'EUR'}.get(
            ticker, 'GBP' if period == '6M' else 'USD')
        fx = ticker.endswith('EUR=X')
        return HistoryResult(('2024-01-01','2024-01-02'), (.9,.8) if fx else (100.,110.),
            currency, 'cached', '2024-01-03', 'Invented source coverage')
    def pending(self, ticker, period):
        if KIND == 'position-revised' and ticker.endswith('EUR=X'):
            (root/'requested-rate').touch()
            return not (root/'release-rate').exists()
        return not (root/'release').exists()
market.history_for = lambda _: History()
@st.dialog('Synthetic chart')
def dialog():
    with (root/'renders').open('a') as out:
        print('dialog',file=out)
    st.session_state.position_edit_dialog = True
    if KIND.startswith('position'):
        market.render_market_history('SYNTH',root,key='market_',service=History())
    else:
        render_futures_chart(root,[make_contract(code,'2027-03-19') for code in ('FDAX','FTUK')],key='future_')
dialog()
""".replace('KIND', repr(kind)))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    process = start_server([sys.executable, '-m', 'streamlit', 'run', str(script),
        '--server.address=127.0.0.1', f'--server.port={port}', '--server.headless=true',
        '--server.fileWatcherType=none', '--browser.gatherUsageStats=false'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            assert process.poll() is None
            try:
                urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=1)
                break
            except OSError:
                time.sleep(.1)
        with browser_session() as runner:
            browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
            page = browser.new_page()
            page.goto(f'http://127.0.0.1:{port}')
            dialog = page.get_by_role('dialog')
            dialog.get_by_text('Updating market-price history in the background…', exact=True).wait_for()
            details = dialog.get_by_test_id('stExpander')
            if kind == 'futures':
                details.get_by_text('Chart details', exact=True).click()
            chart = dialog.locator('.js-plotly-plot')
            if kind == 'position-revised':
                chart.wait_for()
                chart.evaluate('el => window.originalMarketChart = el')
            (tmp_path/'release').touch()
            if kind == 'position-revised':
                for _ in range(100):
                    if (tmp_path/'requested-rate').exists():
                        break
                    time.sleep(.1)
                assert (tmp_path/'requested-rate').exists()
                page.wait_for_timeout(2200)
                assert chart.evaluate('el => el === window.originalMarketChart && el.data[0].y[0] === 100')
                (tmp_path/'release-rate').touch()
            chart.wait_for()
            if kind.startswith('position'):
                page.wait_for_function("() => document.querySelector('.js-plotly-plot')?.data[0].y[0] === 90")
                assert chart.evaluate('el => Array.from(el.data[0].y)') == [90, 88]
                dialog.get_by_role('combobox', name='Currency').click(timeout=6000)
                page.get_by_role('option', name='USD', exact=True).click()
                page.wait_for_function("() => document.querySelector('.js-plotly-plot')?.data[0].y[0] === 100")
                dialog.get_by_role('radio', name='6M', exact=True).click()
                page.wait_for_function("() => document.querySelector('.js-plotly-plot')?.data[0].y[0] === 90")
                dialog.get_by_role('combobox', name='Currency').click()
                page.get_by_role('option', name='GBP', exact=True).click()
                page.wait_for_function("() => document.querySelector('.js-plotly-plot')?.data[0].y[0] === 100")
                settle(page)
                assert chart.evaluate('el => Array.from(el.data[0].y)') == [100, 110]
            else:
                playwright.expect(details.get_by_text('History last retrieved: 2024-01-03')).to_be_visible(timeout=6000)
                playwright.expect(details.get_by_text('Invented source coverage')).to_be_visible()
                details.get_by_role('combobox', name='Contract market').click()
                page.get_by_role('option', name='FTUK', exact=False).click()
                playwright.expect(dialog.get_by_text('Source: Yahoo Finance · ^FTSE · GBP')).to_have_count(1)
                playwright.expect(chart).to_have_count(1)
            assert (tmp_path/'renders').read_text().splitlines() == ['dialog']
            assert not page.get_by_test_id('stException').count()
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)
