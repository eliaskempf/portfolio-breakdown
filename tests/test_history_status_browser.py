"""History checklist navigation with invented holdings and offline evidence."""
from browser_runtime import browser_session, start_server
from datetime import date
from decimal import Decimal

from browser_support import select_tab, settle
from test_ux_browser import ux_page, playwright  # noqa: F401


def open_history(page):
    select_tab(page, 'Positions')
    page.get_by_role('radiogroup', name='Position view', exact=True).get_by_role('radio', name='History', exact=True).click()
    table = page.get_by_role('table', name='History status', exact=True)
    playwright.expect(table).to_be_visible()
    return table


def test_missing_history_list_opens_empty_transactions(ux_page):
    page, _ = ux_page
    table = open_history(page)
    playwright.expect(table.locator('tbody tr')).to_have_count(3)
    playwright.expect(table.get_by_text('No history', exact=True)).to_have_count(3)
    table.locator('tr[data-position-id="fund-a"] td').first.click()
    dialog = page.get_by_role('dialog')
    playwright.expect(dialog.get_by_role('radio', name='Transactions', exact=True)).to_be_checked()
    playwright.expect(dialog.get_by_role('button', name='Add buy', exact=False)).to_be_visible()
    assert page.get_by_test_id('stException').count() == 0


def test_history_filter_and_review_navigation(ux_page):
    from portfolio_app.ledger.account_journal import Journal, save_journal, load_journal
    from portfolio_app.performance.history_observations import CoverageReview, HistoryScope, Observation, ObservationStore, save_observations
    from portfolio_app.ledger.security_history import SecurityBook, SecurityEvent, history_fingerprint
    page, root = ux_page
    start, end = '2024-01-01', date.today().isoformat()
    books = tuple(SecurityBook(identity, instrument, (str(shares), str(cost), 'EUR'),
        records=(SecurityEvent(identity+'-opening', 'opening', start, quantity=shares, amount=shares*cost, currency='EUR'),),
        active_ids=(identity+'-opening',)) for identity, instrument, shares, cost in [('fund-a', 'a', 2, 80), ('fund-b', 'b', 1, 60)])
    journal = Journal(version=2, securities=books)
    save_journal(root, journal, None)
    observations = tuple(Observation(identity+stamp, 'price', identity, stamp, Decimal(price), 'EUR')
                         for identity, price in [('fund-a', 100), ('fund-b', 70)] for stamp in (start, end))
    store = ObservationStore(observations, (CoverageReview('review', HistoryScope(('fund-a',)), start, end, history_fingerprint(journal)),))
    save_observations(root, store, None)
    page.reload(); page.locator('.js-plotly-plot').first.wait_for()
    table = open_history(page)
    playwright.expect(table.locator('tr[data-position-id="fund-a"]')).to_have_count(0)
    playwright.expect(table.locator('tr[data-position-id="fund-b"]')).to_have_count(0)
    checkbox = page.get_by_role('checkbox', name='Only show positions needing attention')
    checkbox.press('Space')
    playwright.expect(table.locator('tr[data-position-id="fund-a"]')).to_contain_text('Applied')
    table.locator('tr[data-position-id="fund-b"] td').first.click()
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('radio', name='All', exact=True).click(); settle(page)
    playwright.expect(dialog.locator('.js-plotly-plot')).to_be_visible()
    playwright.expect(dialog.get_by_role('button', name='Confirm complete history', exact=True)).to_have_count(0)
    assert load_journal(root)[0] == journal  # Navigation never confirms or applies data.
    assert page.get_by_test_id('stException').count() == 0


def test_checklist_loads_prices_without_opening_each_position(tmp_path):
    """One scoped background request, with invented prices and FX only."""
    import os
    import socket
    import subprocess
    import sys
    import time
    from urllib.request import urlopen
    from streamlit.proto.BackMsg_pb2 import BackMsg
    from test_history_download import setup
    from portfolio_app.ledger.account_journal import save_journal, load_journal
    from portfolio_app.performance.history_observations import ObservationStore, CoverageReview, save_observations, load_observations
    from portfolio_app.ledger.security_history import history_fingerprint
    from browser_support import settle
    journal,scope=setup(tmp_path)
    save_journal(tmp_path,journal,None)
    save_observations(tmp_path,ObservationStore(reviews=(CoverageReview('review',scope,'2024-01-01',
        date.today().isoformat(),history_fingerprint(journal)),)),None)
    script=tmp_path/'checklist.py'
    script.write_text('''
from pathlib import Path
from decimal import Decimal
import time
from portfolio_app.portfolio.positions import read_snapshot
import portfolio_app.interface.views.history.history_status_ui as checklist
from portfolio_app.performance.history_observations import Observation
import portfolio_app.performance.personal_market as market
root=Path(__file__).parent
with (root/'page-renders.txt').open('a') as log: print('page', file=log)
if not hasattr(market,'_invented_checklist'):
    render_list = checklist._render_history_status_list
    def counted(*args, **kwargs):
        with (root/'renders.txt').open('a') as log: print('render', file=log)
        return render_list(*args, **kwargs)
    checklist._render_history_status_list = counted
    def prices(ticker,identity,start,end):
        for _ in range(300):
            if (root/'release').exists(): break
            time.sleep(.1)
        else: raise TimeoutError('Synthetic price request was not released')
        with (root/'requests.txt').open('a') as f: f.write('request\\n')
        return (Observation('price','price',identity,end,Decimal(12),'EUR',source='Invented feed'),)
    market.fetch_security_prices=prices
    market.fetch_fx=lambda source,target,start,end: (Observation('fx','fx',source+'/'+target,'2024-01-02',Decimal(1),source,target=target,source='Invented FX'),)
    market._invented_checklist=True
snapshot=read_snapshot(root/'holdings.csv')
checklist.render_history_status_list(root/'holdings.csv',snapshot,snapshot.holdings)
''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    process=start_server([sys.executable,'-m','streamlit','run',str(script),'--server.address=127.0.0.1',
        f'--server.port={port}','--server.headless=true','--server.fileWatcherType=none','--browser.gatherUsageStats=false'],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            assert process.poll() is None
            try:
                urlopen(f'http://127.0.0.1:{port}/_stcore/health',timeout=1);break
            except OSError: time.sleep(.1)
        with browser_session() as runner:
            browser=runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'),args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1000})
            polls = []
            def record(payload):
                message = BackMsg.FromString(payload)
                if message.WhichOneof('type') == 'rerun_script' and message.rerun_script.is_auto_rerun:
                    polls.append(message.rerun_script.fragment_id)
            page.on('websocket', lambda sock: sock.on('framesent', record) if '/_stcore/stream' in sock.url else None)
            page.goto(f'http://127.0.0.1:{port}')
            checkbox=page.get_by_role('checkbox',name='Only show positions needing attention')
            playwright.expect(checkbox).to_be_visible()
            if checkbox.is_checked():
                checkbox.press('Space')
            table=page.get_by_role('table',name='History status',exact=True)
            settle(page)
            renders = tmp_path/'renders.txt'
            pending = renders.read_text()
            pending_polls = len(polls)
            page.wait_for_timeout(4300)  # Two scheduled status ticks while the provider is pending.
            after_pending = renders.read_text()
            assert len(polls) > pending_polls
            (tmp_path/'release').touch()
            playwright.expect(table.get_by_text('Available',exact=True)).to_have_count(3,timeout=20000)
            settle(page)
            finished = renders.read_text()
            finished_polls = len(polls)
            page.wait_for_timeout(4300)
            assert len(polls) == finished_polls
            assert after_pending == pending and renders.read_text() == finished
            assert len(finished.splitlines()) == len(pending.splitlines()) + 1
            assert (tmp_path/'page-renders.txt').read_text().splitlines() == ['page']
            playwright.expect(table.get_by_text('Applied',exact=True)).to_be_visible()
            playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
            assert load_journal(tmp_path)[0]==journal
            assert len(load_observations(tmp_path)[0].reviews)==1
            assert (tmp_path/'requests.txt').read_text().count('request')==1
            page.reload();settle(page)
            checkbox=page.get_by_role('checkbox',name='Only show positions needing attention')
            if checkbox.is_checked():
                checkbox.press('Space')
            playwright.expect(table.get_by_text('Available',exact=True)).to_have_count(3)
            assert (tmp_path/'requests.txt').read_text().count('request')==1
            browser.close()
    finally:
        process.terminate();process.wait(timeout=10)
