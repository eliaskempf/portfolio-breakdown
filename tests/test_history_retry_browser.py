"""Failed downloads can retry locally without leaving the synthetic dialog."""
from browser_runtime import browser_session, start_server
from dataclasses import replace
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

from browser_support import settle
from portfolio_app.ledger.account_journal import load_journal, save_journal
from portfolio_app.performance.history_observations import load_observations, save_observations
from test_history_download import setup

playwright = pytest.importorskip('playwright.sync_api')


@pytest.mark.parametrize('kind', ['market', 'market-cached', 'market-fx', 'personal'])
def test_delayed_retry_rebuilds_owner_and_updates_chart_without_app_rerun(tmp_path, kind):
    journal, _ = setup(tmp_path)
    save_journal(tmp_path, journal, None)
    script = tmp_path/'preview.py'
    script.write_text('''
from pathlib import Path
from datetime import date, timedelta
from dataclasses import replace
import streamlit as st, time, threading
from portfolio_app.market.history import HistoryResult
from portfolio_app.performance.history_observations import Observation, HistoryScope, load_observations, save_observations
from portfolio_app.ledger.account_journal import load_journal
from portfolio_app.performance.personal_history_refresh import RefreshStatus
import portfolio_app.interface.views.history.market_history_ui as market
import portfolio_app.interface.views.history.history_ui as personal
import portfolio_app.performance.personal_history_refresh as refresh_module
root=Path(__file__).parent
def count(name):
    path=root/name
    path.write_text(str(int(path.read_text())+1 if path.exists() else 1))
count('app-renders.txt')
class FakeMarket:
    def __init__(self): self.requested=None
    def get(self, ticker, period, refresh=False, **kwargs):
        if refresh: self.requested=time.monotonic()
        if self.requested and not self.pending():
            return HistoryResult(('2024-01-01','2024-01-02'),(100.,110.),'EUR','cached','2024-01-03','Invented retry')
        if KIND == 'market-cached':
            return HistoryResult(('2024-01-01','2024-01-02'),(100.,110.),'EUR','stale','','Invented initial failure')
        return HistoryResult((),(),'EUR','unavailable','','Invented initial failure')
    def pending(self,*args):
        if KIND == 'market-cached': return self.requested is not None and not (root/'release').exists()
        return self.requested is not None and time.monotonic()-self.requested<.5
class FakePersonal:
    def __init__(self): self.requested=False; self.busy=False
    def request(self,*args,force=False,**kwargs):
        if force:
            self.requested=True; self.busy=True
            def complete():
                time.sleep(.5)
                store,revision=load_observations(root)
                dates=['2024-01-02',(date.today()-timedelta(days=1)).isoformat(),date.today().isoformat()]
                prices=tuple(Observation('price'+str(i),'price','position-0',stamp,10+i,'EUR') for i,stamp in enumerate(dates))
                save_observations(root,replace(store,observations=prices),revision)
                self.busy=False
            threading.Thread(target=complete,daemon=True).start()
        return RefreshStatus(pending=self.busy,errors=() if self.requested else ('Invented initial failure',))
    def pending(self,*args): return self.busy
if not hasattr(market,'_retry_fixture'):
    market._retry_fixture=FakeMarket()
    if KIND == 'market-fx':
        from portfolio_app.market.history import HistoryService
        from portfolio_app.market.market_data import BackgroundHistory, RequestCoordinator
        class Rates:
            def history(self,ticker,period):
                count(ticker)
                if ticker == 'USDEUR=X' and (root/ticker).read_text() == '1':
                    raise OSError('Invented first FX request failed')
                prices, currency = {'SYNTH': ((100.,110.),'USD'),
                    'USDEUR=X': ((.9,.8),'EUR'), 'GBPEUR=X': ((.5,.5),'EUR')}[ticker]
                return HistoryResult(('2024-01-01','2024-01-02'), prices, currency, 'fresh')
        market._retry_fixture=BackgroundHistory(HistoryService(Rates()),RequestCoordinator(),root)
    style = market.style_figure
    def counted_style(*args, **kwargs):
        count('figures.txt')
        return style(*args, **kwargs)
    market.style_figure = counted_style
    refresh_module.refresher=FakePersonal()
    def counted_load(root):
        count('observation-reads.txt')
        return load_observations(root)
    personal.load_observations=counted_load
st.session_state['position_edit_dialog']=True
if KIND == 'market-fx':
    st.session_state.reporting_currency='GBP'
@st.dialog('Invented history')
def dialog():
    count('dialog-renders.txt')
    if KIND.startswith('market'):
        market.render_market_history('SYNTH',root,key='retry_',service=market._retry_fixture)
    else:
        personal.render_history_results(root,load_journal(root)[0],HistoryScope(('position-0',)),
            key='retry_',automatic=True,show_summary=False)
dialog()
'''.replace('KIND', repr(kind)))
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
            button = page.get_by_role('button', name='Retry history' if kind.startswith('market') else 'Retry prices', exact=True)
            button.wait_for(); settle(page)
            assert page.locator('.js-plotly-plot').count() == (1 if kind == 'market-cached' else 0)
            button.click()
            if kind == 'market-cached':
                loading = page.get_by_text('Updating market-price history in the background…', exact=True)
                loading.wait_for()
                settle(page)
                plot = page.locator('.js-plotly-plot')
                plot.evaluate('''el => {
                    window.cachedPricePlot = el;
                    window.pricePlotFaded = false;
                    window.priceChanges = new MutationObserver(() => {
                        if (el.closest('[data-stale="true"]')) window.pricePlotFaded = true;
                    });
                    window.priceChanges.observe(document.body, {subtree: true, attributes: true});
                }''')
                figures = tmp_path/'figures.txt'
                before = figures.read_text()
                page.wait_for_timeout(4300)
                pending = figures.read_text()
                (tmp_path/'release').touch()
                loading.wait_for(state='hidden')
                playwright.expect(page.get_by_text('History last retrieved: 2024-01-03')).to_be_visible()
                page.wait_for_timeout(4300)
                assert pending == before and figures.read_text() == before
                assert plot.evaluate('el => el === window.cachedPricePlot && !window.pricePlotFaded')
                assert plot.evaluate('el => Array.from(el.data[0].y)') == [100, 110]
            playwright.expect(page.locator('.js-plotly-plot')).to_have_count(1, timeout=15000)
            if kind == 'market-fx':
                assert page.locator('.js-plotly-plot').evaluate('el => Array.from(el.data[0].y)') == [180, 176]
                assert [(tmp_path/symbol).read_text() for symbol in ('SYNTH', 'USDEUR=X', 'GBPEUR=X')] == ['1','2','1']
            settle(page)
            playwright.expect(page.get_by_role('dialog')).to_be_visible()
            playwright.expect(button).to_have_count(0)
            assert (tmp_path/'app-renders.txt').read_text() == '1'
            assert (tmp_path/'dialog-renders.txt').read_text() == '1'
            if kind == 'personal':
                before = (tmp_path/'observation-reads.txt').read_text()
                page.wait_for_timeout(2500); settle(page)
                assert (tmp_path/'observation-reads.txt').read_text() == before
                # A real observation revision still invalidates the local cached data.
                store, revision = load_observations(tmp_path)
                last = store.observations[-1]
                updated = replace(last, id='corrected', replaces=last.id, amount=25)
                save_observations(tmp_path, replace(store, observations=(*store.observations, updated)), revision)
                page.wait_for_function("() => document.querySelector('.js-plotly-plot')?.data.some(t => t.name === 'Value' && t.y.includes(50))")
                settle(page)
                assert (tmp_path/'app-renders.txt').read_text() == '1'
            assert load_journal(tmp_path)[0] == journal
            browser.close()
    finally:
        process.terminate(); process.wait(timeout=10)
