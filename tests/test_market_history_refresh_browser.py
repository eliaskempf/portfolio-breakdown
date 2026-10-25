"""Synthetic background price refresh must replace the existing dialog chart."""
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

playwright = pytest.importorskip('playwright.sync_api')


def test_background_price_refresh_keeps_one_chart(tmp_path):
    (tmp_path/'holdings.csv').write_text('id,name,ticker,shares,acquisition_price,acquisition_currency\ninvented,Invented,SYNTH,1,10,EUR\n')
    app = tmp_path/'app.py'
    app.write_text('''
from pathlib import Path
import time
import streamlit as st
from portfolio_app.history import HistoryResult
from portfolio_app.position_ui import request_position, render_position_dialog
from portfolio_app.positions import read_snapshot
import portfolio_app.position_detail as detail
root=Path(__file__).parent
class SyntheticHistory:
    def __init__(self): self.started=time.monotonic()
    def pending(self,*args):return time.monotonic()-self.started<4
    def get(self,*args,**kwargs):
        values=(10.,20.) if self.pending() else (20.,30.)
        return HistoryResult(('2024-01-01','2024-01-02'),values,'EUR','cached')
if 'synthetic_history' not in st.session_state:st.session_state.synthetic_history=SyntheticHistory()
detail.history_for=lambda _:st.session_state.synthetic_history
st.button('Open position',on_click=request_position,args=('position-0',))
render_position_dialog(root/'holdings.csv',read_snapshot(root/'holdings.csv'),{},demo=False)
''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    process = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run', str(app),
        '--server.address=127.0.0.1', f'--server.port={port}', '--server.headless=true',
        '--server.fileWatcherType=none', '--browser.gatherUsageStats=false'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                with urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=1): break
            except OSError: time.sleep(.1)
        with playwright.sync_playwright() as runner:
            browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
            page = browser.new_page(viewport={'width':1440, 'height':1100})
            page.goto(f'http://127.0.0.1:{port}')
            page.get_by_role('button', name='Open position', exact=True).click()
            dialog = page.get_by_role('dialog')
            dialog.locator('.js-plotly-plot').wait_for()
            page.wait_for_function("Array.from(document.querySelectorAll('[role=dialog] .js-plotly-plot')).some(e=>e.data?.[0]?.y?.at(-1)===30)")
            playwright.expect(dialog.locator('.js-plotly-plot')).to_have_count(1)
            assert not page.get_by_test_id('stException').count()
            browser.close()
    finally:
        process.terminate(); process.wait(timeout=10)
