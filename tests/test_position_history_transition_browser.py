"""Slow synthetic history calculations must not leave outgoing views on screen."""
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest
from portfolio_app.account_journal import save_journal
from portfolio_app.history_observations import save_observations, ObservationStore, Observation
from test_history_download import setup
from browser_support import settle

playwright = pytest.importorskip('playwright.sync_api')


@pytest.mark.parametrize('viewport', [{'width':1440, 'height':1100}, {'width':1280, 'height':720}])
def test_position_view_switches_clear_old_content_and_keep_card_geometry(tmp_path, viewport):
    from datetime import date, timedelta
    journal, _ = setup(tmp_path)
    save_journal(tmp_path, journal, None)
    save_observations(tmp_path, ObservationStore(tuple(
        Observation(str(i), 'price', 'position-0', stamp, 10+i, 'EUR')
        for i, stamp in enumerate(['2024-01-02', (date.today()-timedelta(days=5)).isoformat(), date.today().isoformat()]))
        + (Observation('fx', 'fx', 'GBP/EUR', '2024-01-02', 1, 'GBP', target='EUR'),)), None)
    script = tmp_path/'preview.py'
    script.write_text('''
from pathlib import Path
import time
import streamlit as st
from portfolio_app.positions import read_snapshot
from portfolio_app.position_ui import request_position, render_position_dialog
from portfolio_app.history import HistoryResult
from portfolio_app.presentation import apply_style
from portfolio_app.personal_history_refresh import refresher, RefreshStatus
import portfolio_app.history_view as history
import portfolio_app.position_detail as detail
root=Path(__file__).parent
if not hasattr(history, '_test_original'):
    history._test_original=history.history_view
    def delayed(*args, **kwargs):
        with (root/'calculations.txt').open('a') as f: f.write('calculation\\n')
        time.sleep(1)
        return history._test_original(*args, **kwargs)
    history.history_view=delayed
class InventedHistory:
    def pending(self,*args):return False
    def get(self,*args,**kwargs):return HistoryResult(('2024-01-02','2025-01-02'),(10.,11.),'EUR','cached')
detail.history_for=lambda _:InventedHistory()
refresher.request=lambda *a,**k:RefreshStatus()
apply_style()
st.button('Open position',on_click=request_position,args=('position-0',))
render_position_dialog(root/'holdings.csv',read_snapshot(root/'holdings.csv'),{},demo=False)
''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    process = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run', str(script),
        '--server.address=127.0.0.1', f'--server.port={port}', '--server.headless=true',
        '--server.fileWatcherType=none', '--browser.gatherUsageStats=false'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=1); break
            except OSError:
                time.sleep(.1)
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
            page = browser.new_page(viewport=viewport)
            page.goto(f'http://127.0.0.1:{port}')
            page.get_by_role('button', name='Open position', exact=True).click()
            dialog = page.get_by_role('dialog'); dialog.wait_for(); settle(page)
            assert (tmp_path/'calculations.txt').read_text().count('calculation') == 1
            dialog.evaluate('e => window.originalDialog=e')
            metrics = dialog.get_by_test_id('stMetric')
            playwright.expect(metrics).to_have_count(3)
            dialog.locator('.js-plotly-plot').wait_for(); settle(page)
            dialog.evaluate('''e => {
                window.dialogBounds=[];
                const sample=()=>{
                    if(!e.isConnected)return;
                    const r=e.getBoundingClientRect();
                    window.dialogBounds.push({height:r.height,top:r.top,bottom:r.bottom});
                    window.dialogFrame=requestAnimationFrame(sample);
                };
                sample();
            }''')
            history_sizes = metrics.evaluate_all('es=>es.map(e=>({width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height}))')
            dialog.get_by_text('Price history', exact=True).click(); settle(page)
            playwright.expect(metrics).to_have_count(3)
            dialog.locator('.js-plotly-plot').wait_for(); settle(page)
            price_sizes = metrics.evaluate_all('es=>es.map(e=>({width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height}))')
            for a, b in zip(history_sizes, price_sizes):
                assert a['width'] == pytest.approx(b['width'], abs=2)
                assert a['height'] == pytest.approx(b['height'], abs=2)
            assert (tmp_path/'calculations.txt').read_text().count('calculation') == 1
            dialog.get_by_text('History', exact=True).click()
            page.wait_for_timeout(200)
            # Calculation is still sleeping. The old price chart/heading must be gone or hidden.
            assert dialog.get_by_text('Market-price history', exact=True).count() == 0 or not dialog.get_by_text('Market-price history', exact=True).is_visible()
            assert dialog.locator('.js-plotly-plot').evaluate_all('es=>es.every(e=>e.data[0].name!=="Price" || getComputedStyle(e).visibility==="hidden")')
            settle(page)
            assert dialog.evaluate('e=>e===window.originalDialog')
            assert (tmp_path/'calculations.txt').read_text().count('calculation') == 2
            assert not dialog.get_by_text('Required inputs', exact=True).count()
            assert not dialog.get_by_text('History inputs', exact=True).count()
            dialog.get_by_role('button', name='Review transactions', exact=True).click(); settle(page)
            assert dialog.get_by_role('button', name='Confirm complete history', exact=True).count() == 1
            assert not dialog.get_by_role('checkbox').count()
            assert not page.get_by_test_id('stException').count()
            # A long review remains scrollable without growing the outer window.
            assert dialog.evaluate('e=>{const s=e.lastElementChild; return s.scrollHeight>s.clientHeight && getComputedStyle(s).overflowY==="auto";}')
            dialog.get_by_role('button', name='Confirm complete history', exact=True).scroll_into_view_if_needed()
            bounds = page.evaluate('cancelAnimationFrame(window.dialogFrame); window.dialogBounds')
            assert max(b['height'] for b in bounds)-min(b['height'] for b in bounds) <= 1
            assert max(b['top'] for b in bounds)-min(b['top'] for b in bounds) <= 1
            assert all(b['top'] >= 0 and b['bottom'] <= viewport['height'] for b in bounds), bounds
            dialog.get_by_role('button', name='Close', exact=True).first.click()
            playwright.expect(dialog).to_have_count(0)
            browser.close()
    finally:
        process.terminate(); process.wait(timeout=10)
