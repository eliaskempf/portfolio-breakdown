"""Quote corrections retain disclosed provenance in a synthetic running view."""
from dataclasses import replace
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

from browser_support import settle
from portfolio_app.account_journal import save_journal
from portfolio_app.history_observations import load_observations, save_observations
from test_history_data_ui import fixture

playwright = pytest.importorskip('playwright.sync_api')


def test_quote_provenance_correction_and_saved_details_in_browser(tmp_path):
    journal, _, store, revision = fixture(tmp_path)
    store = replace(store, observations=tuple(replace(o, source='Invented closing feed', listing='SYNTH',
        calendar='XNYS', exchange_timezone='America/New_York') if o.id == 'p1' else o for o in store.observations))
    save_observations(tmp_path, store, revision)
    save_journal(tmp_path, journal, None)
    before = (tmp_path/'holdings.csv').read_bytes()
    script = tmp_path/'preview.py'
    script.write_text('''
from pathlib import Path
from types import SimpleNamespace
from portfolio_app.account_journal import load_journal
from portfolio_app.history_observations import load_observations, HistoryScope
from portfolio_app.history_data_ui import render_history_data
root=Path(__file__).parent
journal,_=load_journal(root)
store,revision=load_observations(root)
result=SimpleNamespace(value_reasons=(),flow_reasons=(),capital_reasons=(),money_weighted_reasons=(),time_weighted_reasons=())
view=SimpleNamespace(result=result,all_result=result,missing=(),all_start='2024-01-01',all_end='2024-01-01',today='2024-01-01')
render_history_data(root,journal,HistoryScope(('position-0',)),store,revision,view,'EUR','browser_')
''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    process = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run', str(script),
        '--server.address=127.0.0.1', f'--server.port={port}', '--server.headless=true',
        '--server.fileWatcherType=none', '--browser.gatherUsageStats=false'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            assert process.poll() is None
            try:
                with urlopen(url+'/_stcore/health', timeout=1):
                    break
            except OSError:
                time.sleep(.1)
        else:
            pytest.fail('Synthetic history preview did not start')
        with playwright.sync_playwright() as runner:
            browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            page.goto(url); settle(page)
            page.get_by_text('Correct market data', exact=True).click(); settle(page)
            page.get_by_text('Quote provenance', exact=True).click(); settle(page)
            for label, value in [('Source','Invented closing feed'), ('Quote listing','SYNTH'),
                                 ('Exchange calendar','XNYS'), ('Exchange timezone','America/New_York')]:
                playwright.expect(page.get_by_role('textbox', name=label, exact=True)).to_have_value(value)
            amount = page.get_by_role('textbox', name='Price (USD)', exact=True)
            amount.fill('10.25'); amount.press('Tab'); settle(page)
            page.get_by_role('button', name='Replace saved value', exact=True).click(); settle(page)
            page.get_by_text('Saved market data', exact=True).click(); settle(page)
            saved = load_observations(tmp_path)[0].observations[-1]
            assert saved.replaces == 'p1' and str(saved.amount) == '10.25'
            assert saved.source == 'manual: Invented closing feed'
            assert saved.listing == 'SYNTH' and saved.calendar == 'XNYS'
            assert saved.exchange_timezone == 'America/New_York'
            assert (tmp_path/'holdings.csv').read_bytes() == before
            table = page.get_by_test_id('stDataFrame')
            playwright.expect(table).to_be_visible()
            assert not page.get_by_test_id('stException').count()
            page.reload(); settle(page)
            page.get_by_text('Correct market data', exact=True).click(); settle(page)
            playwright.expect(page.get_by_role('button', name='Replace saved value', exact=True)).to_be_visible()
            browser.close()
        print(f'Quote correction browser passed: {url}')
    finally:
        process.terminate()
        process.wait(timeout=10)
