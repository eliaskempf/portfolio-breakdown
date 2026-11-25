"""Delayed invented prices must update the chart without rebuilding its dialog."""
from browser_runtime import browser_session, start_server
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

playwright = pytest.importorskip('playwright.sync_api')


def test_background_completion_keeps_position_dialog_and_page_stable(tmp_path):
    (tmp_path/'holdings.csv').write_text('id,name,ticker,shares,acquisition_price,acquisition_currency\ninvented,Invented Holding,SYNTH,10,10,EUR\n')
    app=tmp_path/'app.py'
    app.write_text('''
from pathlib import Path
from threading import Event
import streamlit as st
from portfolio_app.portfolio.positions import read_snapshot
from portfolio_app.interface.views.positions.position_ui import request_position, render_position_dialog
from portfolio_app.market.history import HistoryResult
from portfolio_app.interface.ui import render_market_status
from portfolio_app.market.market_data import coordinator
import portfolio_app.interface.views.positions.position_detail as detail
root=Path(__file__).parent
st.session_state.runs=st.session_state.get('runs',0)+1
st.html(f'<span id="page-runs">{st.session_state.runs}</span>')
class InventedHistory:
    def __init__(self): self.release=Event()
    def pending(self,*args):
        if not (root/'history-ready').exists():return True
        self.release.set()
        return coordinator.pending(root,'invented-refresh')
    def get(self,*args,**kwargs):
        if self.pending():return HistoryResult()
        return HistoryResult(('2024-01-01','2024-01-02'),(10.,11.),'EUR','cached')
@st.cache_resource
def history():return InventedHistory()
detail.history_for=lambda _:history()
if not st.session_state.get('scheduled'):
    release = history().release
    coordinator.request(root,'invented-refresh',lambda:release.wait(60))
    st.session_state.scheduled=True
render_market_status(root,coordinator.revision(root))
render_position_dialog(root/'holdings.csv',read_snapshot(root/'holdings.csv'),{},demo=False)
# The opener exercises fragment navigation once its target is registered.
st.button('Open position',on_click=request_position,args=('position-0',))
''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    process=start_server([sys.executable,'-m','streamlit','run',str(app),
        '--server.address=127.0.0.1',f'--server.port={port}','--server.headless=true',
        '--server.fileWatcherType=none','--browser.gatherUsageStats=false'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:urlopen(f'http://127.0.0.1:{port}/_stcore/health',timeout=1);break
            except OSError:time.sleep(.1)
        with browser_session() as p:
            browser=p.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'),args=['--no-sandbox'])
            page=browser.new_page();page.set_default_timeout(10000)
            page.goto(f'http://127.0.0.1:{port}')
            market_pending=page.get_by_text('Updating prices in the background',exact=False)
            market_pending.wait_for()
            page.get_by_role('button',name='Open position',exact=True).click()
            dialog=page.get_by_role('dialog');dialog.wait_for()
            dialog.evaluate('el=>window.originalDialog=el')
            dialog.get_by_text('Updating market-price history in the background…',exact=True).wait_for()
            (tmp_path/'history-ready').touch()
            assert not page.get_by_test_id('stException').count(), page.get_by_test_id('stException').all_text_contents()
            dialog.get_by_test_id('stPlotlyChart').wait_for()
            dialog.get_by_test_id('stPlotlyChart').evaluate('el=>window.originalChart=el')
            page.wait_for_timeout(2500)
            assert page.locator('#page-runs').inner_text()=='1'
            assert dialog.evaluate('el=>el===window.originalDialog')
            assert dialog.get_by_test_id('stPlotlyChart').evaluate('el=>el===window.originalChart')
            assert dialog.get_by_test_id('stPlotlyChart').count()==1
            assert not page.get_by_test_id('stException').count()
            dialog.get_by_role('button',name='Close',exact=True).last.click()
            playwright.expect(dialog).to_have_count(0)
            browser.close()
    finally:
        process.terminate();process.wait(timeout=10)
