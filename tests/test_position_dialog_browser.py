"""Delayed invented prices must update the chart without rebuilding its dialog."""
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
import time
import streamlit as st
from portfolio_app.positions import read_snapshot
from portfolio_app.position_ui import request_position, render_position_dialog
from portfolio_app.history import HistoryResult
from portfolio_app.ui import render_market_status
from portfolio_app.market_data import coordinator
import portfolio_app.position_detail as detail
root=Path(__file__).parent
st.session_state.runs=st.session_state.get('runs',0)+1
st.html(f'<span id="page-runs">{st.session_state.runs}</span>')
class InventedHistory:
    def __init__(self): self.started=None
    def pending(self,*args): return self.started is not None and time.monotonic()-self.started<2
    def get(self,*args,**kwargs):
        if self.started is None:self.started=time.monotonic()
        if self.pending():return HistoryResult()
        return HistoryResult(('2024-01-01','2024-01-02'),(10.,11.),'EUR','cached')
@st.cache_resource
def history():return InventedHistory()
detail.history_for=lambda _:history()
st.button('Open position',on_click=request_position,args=('position-0',))
if st.session_state.get('position_edit_dialog'):
    if not st.session_state.get('scheduled'):
        coordinator.request(root,'invented-refresh',lambda:time.sleep(.5))
        st.session_state.scheduled=True
    render_market_status(root,0,show_caption=False)
render_position_dialog(root/'holdings.csv',read_snapshot(root/'holdings.csv'),{},demo=False)
''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    process=subprocess.Popen([sys.executable,'-m','streamlit','run',str(app),
        '--server.address=127.0.0.1',f'--server.port={port}','--server.headless=true',
        '--server.fileWatcherType=none','--browser.gatherUsageStats=false'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:urlopen(f'http://127.0.0.1:{port}/_stcore/health',timeout=1);break
            except OSError:time.sleep(.1)
        with playwright.sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'),args=['--no-sandbox'])
            page=browser.new_page();page.set_default_timeout(10000)
            page.goto(f'http://127.0.0.1:{port}')
            page.get_by_role('button',name='Open position',exact=True).click()
            dialog=page.get_by_role('dialog');dialog.wait_for()
            dialog.evaluate('el=>window.originalDialog=el')
            dialog.get_by_text('Updating market-price history in the background…',exact=True).wait_for()
            page.wait_for_timeout(3000)
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
