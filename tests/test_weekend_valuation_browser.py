"""Weekend quote provenance and Monday TWR, with invented saved evidence only."""
import os
import socket
import subprocess
import sys
import time
from dataclasses import replace
from urllib.request import urlopen

import pytest

from browser_support import settle
from portfolio_app.account_journal import save_journal
from portfolio_app.history_observations import save_observations
from test_weekend_valuation_fx import example

playwright = pytest.importorskip('playwright.sync_api')


@pytest.mark.parametrize('venue,timezone', [('NYQ', 'America/New_York'), ('STU', 'Europe/Berlin')])
def test_friday_fx_provenance_and_monday_return_render(tmp_path, venue, timezone):
    journal, scope, store = example()
    from portfolio_app.history_valuation import listing_calendar
    calendar, zone = listing_calendar({'exchangeName': venue, 'exchangeTimezoneName': timezone})
    store = replace(store, observations=tuple(replace(o, calendar=calendar, exchange_timezone=zone)
        if o.kind == 'price' else o for o in store.observations))
    save_journal(tmp_path, journal, None)
    save_observations(tmp_path, store, None)
    (tmp_path/'holdings.csv').write_text('position_key,id,name,shares,acquisition_price,acquisition_currency\n'
                                       'invented,SYNTH,Invented USD holding,2,65,EUR\n')
    script = tmp_path/'preview.py'
    script.write_text('''
from pathlib import Path
from datetime import date
import streamlit as st
from portfolio_app.account_journal import load_journal
from portfolio_app.history_observations import load_observations, HistoryScope
from portfolio_app.history_view import history_view
from portfolio_app.history_ui import render_history_results
root=Path(__file__).parent
journal,_=load_journal(root)
store,revision=load_observations(root)
scope=HistoryScope(('invented',))
choice=st.radio('Synthetic interval', ['Weekend','After Monday contribution'])
today=date(2024,1,7) if choice=='Weekend' else date(2024,1,9)
view=history_view(journal,scope,store,'All',today=today)
render_history_results(root,journal,scope,key='synthetic_',show_summary=False,
    prepared=dict(observations=store,revision=revision,view=view))
st.metric('Calculated daily TWR', 'Unavailable' if view.all_result.time_weighted is None else f'{100*view.all_result.time_weighted:.2f}%')
''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    process = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run', str(script),
        '--server.address=127.0.0.1', f'--server.port={port}', '--server.headless=true',
        '--server.fileWatcherType=none', '--browser.gatherUsageStats=false'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            assert process.poll() is None
            try:
                urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=1); break
            except OSError:
                time.sleep(.1)
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
            page = browser.new_page(viewport={'width':1280, 'height':900})
            page.goto(f'http://127.0.0.1:{port}')
            chart = page.locator('.js-plotly-plot'); chart.wait_for(); settle(page)
            texts = chart.evaluate('e=>e.data.find(t=>t.name==="Value").text')
            assert 'Quote as of: 2024-01-05' in texts[-1]
            assert 'FX as of: 2024-01-05' in texts[-1]
            playwright.expect(page.get_by_text('Prior close · FX as of', exact=False)).to_be_visible()
            page.get_by_role('radio', name='After Monday contribution', exact=True).press('Space'); settle(page)
            playwright.expect(page.get_by_test_id('stMetricValue')).to_have_text('24.62%')
            assert not page.get_by_test_id('stException').count()
            browser.close()
    finally:
        process.terminate(); process.wait(timeout=10)
