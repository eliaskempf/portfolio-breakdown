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
            expected_summary = metrics.all_text_contents()
            assert dialog.get_by_test_id('stMetricLabel').all_text_contents() == ['Position value', 'Invested capital', 'Annualized return']
            chart = dialog.locator('.js-plotly-plot')
            assert chart.evaluate('e=>e.layout.meta.historyInspection.metrics.length') == 0
            chart.scroll_into_view_if_needed()
            point = chart.evaluate('''e=>{
                const i=e.data[0].y.findLastIndex(v=>v!==null), r=e.getBoundingClientRect();
                return {x:r.x+e._fullLayout.xaxis._offset+e._fullLayout.xaxis.d2p(e.data[0].x[i]),
                        y:r.y+e._fullLayout.yaxis._offset+e._fullLayout.yaxis.d2p(e.data[0].y[i])};
            }''')
            page.mouse.move(point['x']-2, point['y'])
            playwright.expect(chart.locator('.hoverlayer')).to_contain_text('Total gain:')
            assert metrics.all_text_contents() == expected_summary
            page.mouse.move(0, 0)
            history_sizes = metrics.evaluate_all('es=>es.map(e=>({width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height}))')
            dialog.get_by_role('radio', name='Price', exact=True).click(); settle(page)
            playwright.expect(metrics).to_have_count(3)
            dialog.locator('.js-plotly-plot').wait_for(); settle(page)
            assert metrics.all_text_contents() == expected_summary
            price_sizes = metrics.evaluate_all('es=>es.map(e=>({width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height}))')
            for a, b in zip(history_sizes, price_sizes):
                assert a['width'] == pytest.approx(b['width'], abs=2)
                assert a['height'] == pytest.approx(b['height'], abs=2)
            assert (tmp_path/'calculations.txt').read_text().count('calculation') == 1
            dialog.get_by_role('radio', name='Holdings', exact=True).click()
            page.wait_for_timeout(200)
            # Returning to cached holdings must remove or hide the old price chart.
            # Plotly can purge its data before React removes the chart element.
            assert dialog.locator('.js-plotly-plot').evaluate_all('es=>es.every(e=>e.data?.[0]?.name!=="Price" || getComputedStyle(e).visibility==="hidden")')
            settle(page)
            assert dialog.evaluate('e=>e===window.originalDialog')
            assert (tmp_path/'calculations.txt').read_text().count('calculation') == 1
            assert metrics.all_text_contents() == expected_summary
            assert not dialog.get_by_text('Required inputs', exact=True).count()
            assert not dialog.get_by_text('History inputs', exact=True).count()
            dialog.get_by_role('button', name='Review transactions', exact=True).click(); settle(page)
            assert dialog.get_by_role('button', name='Apply and confirm history', exact=True).count() == 1
            assert not dialog.get_by_role('checkbox').count()
            assert not page.get_by_test_id('stException').count()
            # A long review remains scrollable without growing the outer window.
            assert dialog.evaluate('e=>{const s=e.lastElementChild; return s.scrollHeight>=s.clientHeight && getComputedStyle(s).overflowY==="auto";}')
            if viewport['height'] == 720:
                assert dialog.evaluate('e=>e.lastElementChild.scrollHeight>e.lastElementChild.clientHeight')
            dialog.get_by_role('button', name='Apply and confirm history', exact=True).scroll_into_view_if_needed()
            bounds = page.evaluate('cancelAnimationFrame(window.dialogFrame); window.dialogBounds')
            assert max(b['height'] for b in bounds)-min(b['height'] for b in bounds) <= 1
            assert max(b['top'] for b in bounds)-min(b['top'] for b in bounds) <= 1
            assert all(b['top'] >= 0 and b['bottom'] <= viewport['height'] for b in bounds), bounds
            # Evidence arriving while Price is selected updates the shared row
            # without a chart switch or a whole-dialog remount.
            from dataclasses import replace
            from portfolio_app.history_observations import load_observations, CoverageReview, HistoryScope
            from portfolio_app.security_history import history_fingerprint
            dialog.get_by_role('radio', name='History', exact=True).click(); settle(page)
            dialog.get_by_role('radio', name='Price', exact=True).click(); settle(page)
            annualized = metrics.filter(has_text='Annualized return').get_by_test_id('stMetricValue')
            playwright.expect(annualized).to_have_text('—')
            store, revision = load_observations(tmp_path)
            review = CoverageReview('synthetic-review', HistoryScope(('position-0',)),
                '2024-01-01', date.today().isoformat(), history_fingerprint(journal))
            save_observations(tmp_path, replace(store, reviews=(review,)), revision)
            playwright.expect(annualized).not_to_have_text('—', timeout=10000)
            assert dialog.evaluate('e=>e===window.originalDialog')
            page.wait_for_timeout(2300)
            assert (tmp_path/'calculations.txt').read_text().count('calculation') == 2
            dialog.get_by_role('button', name='Close', exact=True).first.click()
            playwright.expect(dialog).to_have_count(0)
            page.wait_for_timeout(2300)
            playwright.expect(dialog).to_have_count(0)
            browser.close()
    finally:
        process.terminate(); process.wait(timeout=10)
