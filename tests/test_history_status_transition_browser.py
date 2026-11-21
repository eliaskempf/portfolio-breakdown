"""Synthetic refresh transitions must not briefly duplicate valuation status."""
import os
import re
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

from browser_runtime import browser_session, start_server
from browser_support import settle
from portfolio_app.ledger.account_journal import save_journal
from portfolio_app.performance.history_observations import Observation, ObservationStore, save_observations
from test_history_download import setup

playwright = pytest.importorskip('playwright.sync_api')


@pytest.mark.parametrize('refresh_error', [False, True])
def test_completed_history_refresh_keeps_one_valuation_status(tmp_path, refresh_error):
    journal, _ = setup(tmp_path)
    save_journal(tmp_path, journal, None)
    prices = tuple(Observation('price'+str(i), 'price', 'position-0', stamp, 10+i, 'USD',
        calendar='XETR', exchange_timezone='Europe/Berlin', listing='INVENTED')
        for i, stamp in enumerate(('2024-01-02', '2024-12-27')))
    fx = tuple(Observation(currency+stamp, 'fx', currency+'/EUR', stamp, 1, currency, target='EUR')
        for currency in ('GBP', 'USD') for stamp in ('2024-01-02', '2024-12-27'))
    save_observations(tmp_path, ObservationStore((*prices, *fx)), None)
    script = tmp_path/'preview.py'
    script.write_text('''
from pathlib import Path
from datetime import date
import streamlit as st, time
from portfolio_app.ledger.account_journal import load_journal
from portfolio_app.performance.history_observations import HistoryScope
from portfolio_app.performance.personal_history_refresh import RefreshStatus
import portfolio_app.performance.personal_history_refresh as refresh_module
import portfolio_app.interface.views.history.history_ui as history
root=Path(__file__).parent
class FixedDate(date):
    @classmethod
    def today(cls): return cls(2024,12,29)
history.date=FixedDate
class SyntheticRefresh:
    def pending(self,*args): return not (root/'complete').exists()
    def request(self,*args,**kwargs):
        return RefreshStatus(pending=self.pending(), errors=('Invented refresh failure',) if REFRESH_ERROR and not self.pending() else ())
refresh_module.refresher=SyntheticRefresh()
if not hasattr(history,'_status_original'):
    history._status_original=history._render_history_results
    def delayed(*args,**kwargs):
        history._status_original(*args,**kwargs)
        # Keep the streamed transition observable before Streamlit retires stale nodes.
        time.sleep(.5)
        if (root/'complete').exists(): (root/'rendered').touch()
    history._render_history_results=delayed
@st.dialog('Invented position')
def dialog():
    history.render_history_results(root,load_journal(root)[0],HistoryScope(('position-0',)),
        key='synthetic_',automatic=True,show_summary=False)
if st.button('Open position'): dialog()
'''.replace('REFRESH_ERROR', repr(refresh_error)))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    process = start_server([sys.executable, '-m', 'streamlit', 'run', str(script),
        '--server.address=127.0.0.1', f'--server.port={port}', '--server.headless=true',
        '--server.fileWatcherType=none', '--browser.gatherUsageStats=false'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            assert process.poll() is None
            try:
                urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=1); break
            except OSError: time.sleep(.1)
        with browser_session() as runner:
            browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
            page = browser.new_page(viewport={'width':1280, 'height':1000})
            page.goto(f'http://127.0.0.1:{port}')
            page.get_by_role('button', name='Open position', exact=True).click()
            dialog = page.get_by_role('dialog')
            status = dialog.get_by_test_id('stCaptionContainer').filter(has_text=re.compile('Market closed · Quotes as of'))
            playwright.expect(status).to_have_count(1)
            playwright.expect(status).to_contain_text('Prior close · FX as of 2024-12-27')
            settle(page)
            page.evaluate('''() => {
                window.statusCounts=[];
                const sample=()=>window.statusCounts.push([...document.querySelectorAll('[role="dialog"] [data-testid="stCaptionContainer"]')]
                    .filter(e=>e.textContent.includes('Market closed · Quotes as of') && e.getClientRects().length
                        && getComputedStyle(e).visibility!=='hidden').length);
                window.statusObserver=new MutationObserver(sample);
                window.statusObserver.observe(document.body,{subtree:true,childList:true,attributes:true,characterData:true});
                sample();
            }''')
            (tmp_path/'complete').touch()
            playwright.expect(dialog.get_by_text('Loading historical prices and FX…', exact=True)).to_have_count(0, timeout=10000)
            deadline = time.monotonic()+10
            while not (tmp_path/'rendered').exists() and time.monotonic()<deadline:
                page.wait_for_timeout(50)
            assert (tmp_path/'rendered').exists()
            settle(page)
            counts = page.evaluate('() => {window.statusObserver.disconnect();return window.statusCounts;}')
            assert counts and max(counts) == 1, f'Duplicate valuation status during refresh: {max(counts)}'
            playwright.expect(status).to_have_count(1)
            playwright.expect(dialog.get_by_role('button', name='Retry prices', exact=True)).to_have_count(int(refresh_error))
            playwright.expect(dialog.locator('.js-plotly-plot')).to_have_count(1)
            playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
            browser.close()
    finally:
        process.terminate(); process.wait(timeout=10)
