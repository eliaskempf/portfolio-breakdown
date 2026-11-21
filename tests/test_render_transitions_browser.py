"""Observe intermediate DOM states in unrelated production rendering paths."""
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

from browser_runtime import browser_session, start_server
from browser_support import settle
from portfolio_app.ledger.account_journal import save_journal
from test_history_download import setup

playwright = pytest.importorskip('playwright.sync_api')
CASES = {
    'checklist': ('[data-testid="stCaptionContainer"]', 'Select a position to review its history.'),
    'trade': ('button', 'Save sale'),
    'search': ('[data-testid="stCaptionContainer"]', 'Live suggestions appear as you type.'),
    'market': ('[data-testid="stAlert"]', 'Invented history status'),
    'etf': ('[data-testid="stCaptionContainer"]', 'Invented snapshot notice'),
}


@pytest.mark.parametrize('kind', CASES)
def test_dynamic_sections_do_not_duplicate_following_content(tmp_path, kind):
    journal, _ = setup(tmp_path)
    save_journal(tmp_path, journal, None)
    script = tmp_path/'preview.py'
    script.write_text('''
from pathlib import Path
import streamlit as st, time
from portfolio_app.ledger.account_journal import load_journal
from portfolio_app.portfolio.positions import read_snapshot
root=Path(__file__).parent
expanded=st.checkbox('Expanded state',value=True)
snapshot=read_snapshot(root/'holdings.csv')
journal,revision=load_journal(root)
if KIND == 'checklist':
    from portfolio_app.performance.personal_history_refresh import RefreshStatus
    import portfolio_app.performance.personal_history_refresh as refresh_module
    from portfolio_app.interface.views.history.history_status_ui import render_history_status_list
    class FakeRefresh:
        def request(self,*a,**k): return RefreshStatus(pending=expanded)
        def pending(self,*a): return expanded
    refresh_module.refresher=FakeRefresh()
    render_history_status_list(root/'holdings.csv',snapshot,snapshot.holdings)
elif KIND == 'trade':
    from portfolio_app.interface.views.positions.trade_entry_ui import render_trade
    st.session_state['synthetic_trade_quantity']='1' if expanded else ''
    st.session_state['synthetic_trade_price']='20'
    render_trade(root,snapshot,journal,revision,'position-0','synthetic_',kind='sell')
elif KIND == 'search':
    import portfolio_app.interface.shared.instrument_ui as search
    from portfolio_app.interface.shared.search_widget import SEARCH_KEY
    class FakeSearch:
        def __init__(self,*a): self.last_error='Invented search failure' if expanded else ''
        def search(self,*a): return []
    search.InstrumentSearch=FakeSearch
    # The result widget is independent; exercise the production retry/status layout.
    search.render_search_box=lambda *a: None
    st.session_state[SEARCH_KEY]={'query':'invented'}
    st.session_state['position_edit_search_cache']={}
    search.render_instrument_search('synthetic_',snapshot.holdings,root/'.cache')
elif KIND == 'market':
    from portfolio_app.market.history import HistoryResult
    from portfolio_app.interface.views.history.market_history_ui import render_market_history
    class FakeHistory:
        def get(self,*a,**k):
            return HistoryResult(('2024-01-02','2024-01-03') if expanded else (),
                (10.,11.) if expanded else (), 'EUR','stale',note='Invented history status')
        def pending(self,*a): return False
    render_market_history('INVENTED',root,key='synthetic_',service=FakeHistory(),manual=True)
else:
    import portfolio_app.interface.views.funds.etf_refresh_ui as funds
    funds.coordinator.running=lambda *a: expanded
    funds.refresh_revision=lambda *a: 0
    funds.refresh_allowed=lambda: False
    funds.refresh_notices=lambda *a,**k: (['Invented snapshot notice'],())
    funds.render_refresh_status(root,[],0)
# Make the real streamed intermediate states observable before stale cleanup.
time.sleep(.5)
st.html('<span hidden data-render-complete="'+str(expanded)+'"></span>')
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
            selector, text = CASES[kind]
            target=page.locator(selector).filter(has_text=text)
            playwright.expect(target).to_have_count(1)
            settle(page)
            for expanded in (False, True):
                page.evaluate('''({selector,text}) => {
                    window.transitionCounts=[];
                    const sample=()=>window.transitionCounts.push([...document.querySelectorAll(selector)]
                        .filter(e=>e.textContent.includes(text) && e.getClientRects().length
                            && getComputedStyle(e).visibility!=='hidden').length);
                    window.transitionObserver=new MutationObserver(sample);
                    window.transitionObserver.observe(document.body,{subtree:true,childList:true,attributes:true,characterData:true});sample();
                }''', {'selector': selector, 'text': text})
                page.get_by_role('checkbox', name='Expanded state', exact=True).press('Space')
                page.locator(f'[data-render-complete="{expanded}"]').wait_for(state='attached')
                settle(page)
                counts=page.evaluate('() => {window.transitionObserver.disconnect();return window.transitionCounts;}')
                assert counts and max(counts)==1, f'{kind}: duplicate content during transition: {max(counts)}'
                playwright.expect(target).to_have_count(1)
                playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
            browser.close()
    finally:
        process.terminate(); process.wait(timeout=10)
