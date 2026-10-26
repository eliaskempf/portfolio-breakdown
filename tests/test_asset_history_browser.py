"""Real-browser forward chart checks with entirely invented data and prices."""
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

playwright = pytest.importorskip('playwright.sync_api')
from test_asset_history_ui import setup


def test_forward_import_diagnostic_chart_modes(tmp_path):
    directory=tmp_path/'synthetic';directory.mkdir()
    setup(directory,active=True)
    app=tmp_path/'app.py'
    app.write_text('''
from pathlib import Path
from portfolio_app.history import HistoryResult
from portfolio_app import market_data
from portfolio_app.prices import PriceService,UnavailableProvider
from portfolio_app.ui import render_app
class StaticHistory:
    def get(self,*args,**kwargs):
        return HistoryResult(('2024-01-01','2024-01-02','2024-01-03'),(10.,10.,10.),'EUR','cached')
    def pending(self,*args):return False
market_data.history_for=lambda _:StaticHistory()
from portfolio_app.account_journal import load_journal
from portfolio_app.asset_history_store import load_settings
from portfolio_app.history_observations import HistoryScope
from portfolio_app.asset_history_ui import render_asset_history
root=Path(__file__).parent/'synthetic'
render_asset_history(root,load_journal(root)[0],HistoryScope(('aur',)),load_settings(root)[0],key='diagnostic_')
''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    process=subprocess.Popen([sys.executable,'-m','streamlit','run',str(app),'--server.address=127.0.0.1',
        f'--server.port={port}','--server.headless=true','--server.fileWatcherType=none','--browser.gatherUsageStats=false'],
        cwd=tmp_path,env={**os.environ,'PORTFOLIO_STATE_DIR':str(tmp_path/'state')},
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            assert process.poll() is None
            try:
                with urlopen(f'http://127.0.0.1:{port}/_stcore/health',timeout=1):break
            except OSError:time.sleep(.1)
        with playwright.sync_playwright() as runner:
            browser=runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'),args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1100})
            def settle():
                page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
                assert not page.get_by_test_id('stException').count()
            page.goto(f'http://127.0.0.1:{port}')
            page.get_by_text('Approx. return',exact=True).wait_for();settle()
            dialog=page
            playwright.expect(dialog.get_by_text('Approx. return',exact=True)).to_be_visible()
            assert dialog.get_by_test_id('stPlotlyChart').count()==1
            for measure in ('Gain','Return','Units','Value'):
                dialog.get_by_text(measure,exact=True).click()
                expected={'Gain':'Period gain','Return':'Approx. return','Units':'AUR units','Value':'Asset value'}[measure]
                page.wait_for_function("name => document.querySelector('.js-plotly-plot')?.data?.[0]?.name === name",arg=expected)
                settle()
                assert dialog.get_by_test_id('stPlotlyChart').count()==1
                if measure=='Units':
                    assert dialog.locator('.js-plotly-plot').evaluate('el => Array.from(el.data[0].y).at(-1)')==12
            dialog.get_by_text('Calculation and evidence',exact=True).click();settle()
            playwright.expect(dialog.get_by_text('Signed changes, not absolute balances.',exact=False)).to_be_visible()
            browser.close()
    finally:
        process.terminate();process.wait(timeout=10)
