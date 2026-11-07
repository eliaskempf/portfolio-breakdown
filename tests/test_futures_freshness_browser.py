"""Real browser checks of normal-app futures freshness using invented data only."""
from browser_runtime import browser_session, start_server
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest
from portfolio_app.ledger import account_journal as ledger
from portfolio_app.derivatives.automatic_settlements import apply_archived_settlements
from portfolio_app.derivatives.derivative_market import make_contract
from portfolio_app.derivatives.derivative_types import PriceObservation
from portfolio_app.derivatives.futures_clock import business_date
from portfolio_app.derivatives.futures_quotes import record_result
from portfolio_app.derivatives.settlement_archive import SettlementArchive
from test_futures_investments import grouped
from browser_support import StreamlitReruns, settle

NOW = datetime(2026, 1, 7, 12, tzinfo=timezone.utc)

playwright = pytest.importorskip('playwright.sync_api')


def preview_workspace(root):
    root.mkdir(exist_ok=True)
    _, journal, _ = grouped(root)
    contract = make_contract('FMWN','2027-03-19')
    journal = replace(journal, contracts=(contract,), positions=(replace(journal.positions[0],contract_id=contract.id),))
    ledger.save_journal(root,journal,None)
    now = NOW
    old = now-timedelta(days=2)
    mark = PriceObservation(contract.id,business_date(old).isoformat(),1020,kind='mark',source='Invented delayed quote',
                            observed_at=now.isoformat(),quote_at=old.isoformat(),delay_minutes=15)
    record_result(root,contract,mark,'Invented temporary outage',attempted_at=now,now=now)
    return journal,contract


def preview_script(root):
    return f'''
from pathlib import Path
from types import SimpleNamespace
from portfolio_app.interface.ui import render_app
from portfolio_app.market.prices import PriceService, StaticProvider
from portfolio_app.market.history import HistoryResult
from portfolio_app.derivatives.futures_quotes import collector as quotes
from portfolio_app.derivatives.settlement_collection import collector as settlements
from portfolio_app.funds.etf_refresh import coordinator
import portfolio_app.interface.views.history.market_history_ui as charts
quotes.schedule = lambda *a, **kw: False
settlements.schedule = lambda *a, **kw: False
coordinator.schedule = lambda *a, **kw: False
charts.history_for = lambda _: SimpleNamespace(get=lambda *a, **kw: HistoryResult(
    ('2026-01-02','2026-01-05','2026-01-06'), (100., 105., 98.), 'EUR', 'cached',
    '2026-01-07', 'Invented market context'), pending=lambda *a: False)
root = Path({str(root)!r})
import portfolio_app.derivatives.futures_valuation as valuation
from datetime import datetime
valuation.utc_now = lambda: datetime.fromisoformat((root/'clock.txt').read_text())
from portfolio_app.workspace.atomic_file import staged_text
from portfolio_app.workspace.locking import write_lock
count = root/'app-renders.txt'
# Fast full reruns can overlap before either reaches render_app. Keep the
# diagnostic integer readable by polling tests and serialize every increment.
with write_lock(root/'.app-renders.lock'):
    next_count = str(int(count.read_text())+1 if count.exists() else 1)
    with staged_text(count, lambda handle: handle.write(next_count)) as staged:
        staged.replace(count)
render_app(root, demo=False, ignore_selection=True, price_service=PriceService(StaticProvider(root/'demo_prices.json')))
'''


@pytest.fixture(autouse=True)
def chart_finish_delivery(request, monkeypatch):
    """Omit a Chart finish from the driver observer, keeping real app rendering."""
    if not getattr(request, 'param', False):
        yield
        return
    from streamlit.proto.BackMsg_pb2 import BackMsg
    from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
    sent, received = StreamlitReruns._sent, StreamlitReruns._received
    counts = {}
    state = dict(scope=None, omit=False, omitted=False)
    def _sent(self, payload):
        message = BackMsg.FromString(payload)
        if message.WhichOneof('type') == 'rerun_script':
            rerun = message.rerun_script
            if rerun.fragment_id and not rerun.is_auto_rerun:
                counts[rerun.fragment_id] = counts.get(rerun.fragment_id, 0) + 1
                # Settlements -> Contract history -> Chart in the same dialog.
                if counts[rerun.fragment_id] == 3:
                    state['scope'] = rerun.fragment_id
        sent(self, payload)
    def _received(self, payload):
        message = ForwardMsg.FromString(payload)
        kind = message.WhichOneof('type')
        if kind == 'new_session':
            state['omit'] = bool(state['scope']
                                 and state['scope'] in message.new_session.fragment_ids_this_run
                                 and not state['omitted'])
        elif (kind == 'script_finished' and state['omit']
              and message.script_finished in (ForwardMsg.FINISHED_FRAGMENT_RUN_SUCCESSFULLY,
                                               ForwardMsg.FINISHED_EARLY_FOR_RERUN)):
            state.update(omit=False, omitted=True)
            return
        received(self, payload)
    monkeypatch.setattr(StreamlitReruns, '_sent', _sent)
    monkeypatch.setattr(StreamlitReruns, '_received', _received)
    yield
    assert state['omitted'], 'Missing-finish regression did not exercise its fault'


@pytest.fixture
def futures_page(tmp_path):
    root = tmp_path/'synthetic'
    journal,contract = preview_workspace(root)
    script = tmp_path/'preview.py'
    script.write_text(preview_script(root))
    (root/'clock.txt').write_text(NOW.isoformat())
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    process = start_server([sys.executable,'-m','streamlit','run',str(script),
        '--server.address=127.0.0.1',f'--server.port={port}','--server.headless=true',
        '--server.fileWatcherType=none','--browser.gatherUsageStats=false'],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            assert process.poll() is None
            try:
                urlopen(f'http://127.0.0.1:{port}/_stcore/health',timeout=1);break
            except OSError:time.sleep(.1)
        with browser_session() as runner:
            browser=runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'),args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1100})
            page.set_default_timeout(20000)
            page.reruns = StreamlitReruns(page, settle=settle)
            page.goto(f'http://127.0.0.1:{port}')
            # Document load can precede the websocket and first app render.
            page.locator('tr[data-position-id="investment:synthetic"]').wait_for()
            page.reruns.wait()
            yield page,root,journal,contract
            browser.close()
    finally:
        process.terminate();process.wait(timeout=10)


@pytest.mark.parametrize('chart_finish_delivery', [False, True], indirect=True)
def test_stale_fresh_failure_settlement_and_popup_navigation(futures_page):
    page,root,journal,contract=futures_page
    row=page.locator('tr[data-position-id="investment:synthetic"]')
    row.wait_for();settle(page)
    playwright.expect(row.locator('summary')).to_contain_text('Stale')
    assert row.locator('td.positive,td.negative').count()==0
    row.locator('summary').focus();page.keyboard.press('Enter')
    playwright.expect(row.locator('details')).to_have_attribute('open','')
    playwright.expect(row).to_contain_text('Invented temporary outage')
    playwright.expect(page.get_by_role('dialog')).to_have_count(0)
    page.reruns.click(page,page.get_by_role('tab',name='Positions',exact=True))
    row=page.locator('tr[data-position-id="investment:synthetic"]')
    playwright.expect(row.locator('summary')).to_contain_text('Stale')
    row.locator('td').first.click()
    dialog=page.get_by_role('dialog');dialog.wait_for();settle(page)
    playwright.expect(dialog.locator('.js-plotly-plot')).to_have_count(1)
    playwright.expect(dialog.get_by_text('Net P&L',exact=True)).to_be_visible()
    assert dialog.locator('[class*="st-key-gain_positive_"]').count()==0
    for view in ['Settlements','Contract history','Chart']:
        dialog.get_by_text(view,exact=True).first.click();settle(page)
        if view == 'Settlements':
            playwright.expect(dialog.get_by_role('button', name='Retrieve settlements')).to_be_visible()
        elif view == 'Contract history':
            playwright.expect(dialog.get_by_text('Contract accounting and price sources', exact=True)).to_be_visible()
        else:
            playwright.expect(dialog.locator('.js-plotly-plot')).to_have_count(1)
    dialog.get_by_role('button',name='Close',exact=True).filter(has_text='Close').click();settle(page)
    playwright.expect(dialog).to_have_count(0)
    # This synthetic workspace without a quote still displays a dated
    # execution fallback, never an unqualified positive current valuation.
    saved_quotes = root/'invented-saved-quotes.json'
    (root/'futures-quotes.json').rename(saved_quotes)
    page.reruns.click_full_rerun(page,page.get_by_role('button',name='Refresh prices'))
    row=page.locator('tr[data-position-id="investment:synthetic"]')
    playwright.expect(row.locator('summary')).to_contain_text('Recorded')
    assert row.locator('td.positive,td.negative').count()==0
    saved_quotes.rename(root/'futures-quotes.json')
    now=NOW;stamp=now-timedelta(minutes=15)
    fresh=PriceObservation(contract.id,business_date(stamp).isoformat(),990,kind='mark',
        source='Invented delayed quote',observed_at=now.isoformat(),quote_at=stamp.isoformat(),delay_minutes=15)
    record_result(root,contract,fresh,'',attempted_at=now,now=now)
    page.reruns.click(page,page.get_by_role('button',name='Refresh prices'))
    row=page.locator('tr[data-position-id="investment:synthetic"]')
    playwright.expect(row.locator('summary')).to_have_count(0)
    assert row.locator('td.negative').count()==1
    assert ledger.load_journal(root)[0]==journal
    before_value=row.locator('td').nth(3).inner_text()
    day=business_date(now)-timedelta(days=1)
    while day.weekday()>4:day-=timedelta(days=1)
    SettlementArchive(root).record(contract,PriceObservation(contract.id,day.isoformat(),1005,source='Invented daily settlement'))
    assert apply_archived_settlements(root, today=NOW.date())==1
    assert apply_archived_settlements(root, today=NOW.date())==0
    page.reruns.click(page,page.get_by_role('button',name='Refresh prices'))
    assert row.locator('td').nth(3).inner_text()==before_value
    playwright.expect(row.locator('summary')).to_have_count(0)
    record_result(root,contract,None,'Invented refresh failure',attempted_at=NOW,now=NOW)
    page.reruns.click(page,page.get_by_role('button',name='Refresh prices'))
    playwright.expect(row.locator('summary')).to_have_count(0)
    assert row.locator('td[title]').evaluate_all("cells => cells.some(cell => cell.title.includes('Invented refresh failure'))")
    page.reruns.click(page,page.get_by_role('tab',name='Futures',exact=True))
    page.get_by_text('Marks & FX',exact=True).click();settle(page)
    page.get_by_role('textbox',name='Manual mark price',exact=True).fill('995')
    page.get_by_role('textbox',name='Manual mark price',exact=True).press('Enter');settle(page)
    playwright.expect(page.get_by_role('button',name='Save mark only',exact=True)).to_be_visible()
    page.reruns.click(page,page.get_by_role('tab',name='Overview',exact=True))
    page.set_viewport_size({'width':750,'height':1000});settle(page)
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


@pytest.mark.parametrize('last_trade', [False, True])
def test_closed_market_latest_session_has_compact_accessible_details(futures_page, last_trade):
    page, root, journal, contract = futures_page
    row = page.locator('tr[data-position-id="investment:synthetic"]')
    row.wait_for(); settle(page)
    # Polling reads this clock concurrently; never expose a truncated file.
    (root/'clock.next').write_text('2026-01-10T12:00:00+00:00')
    (root/'clock.next').replace(root/'clock.txt')
    SettlementArchive(root).record(contract, PriceObservation(contract.id, '2026-01-09', 1020,
        source='Invented settlement'))
    title = 'Settlement · 09 Jan · Market closed'
    if last_trade:
        now = datetime.fromisoformat('2026-01-10T12:00:00+00:00')
        record_result(root, contract, PriceObservation(contract.id, '2026-01-09', 1025,
            kind='mark', source='Invented last trade', quote_at='2026-01-09T19:00:00+00:00'),
            '', attempted_at=now, now=now)
        title = 'Market closed · Latest available quote'
    page.reruns.click(page, page.get_by_role('button', name='Refresh prices'))
    indicator = row.get_by_label('Valuation details', exact=True)
    playwright.expect(indicator).to_have_text('ⓘ')
    playwright.expect(row.locator('td').first.get_by_label('Valuation details')).to_have_count(1)
    playwright.expect(row.locator('td.number details')).to_have_count(0)
    playwright.expect(indicator).to_have_attribute('title', title)
    assert row.locator('td.positive').count() >= 1
    assert 'Settlement' not in row.inner_text()
    assert 'Stale' not in row.inner_text()
    indicator.focus(); page.keyboard.press('Enter')
    playwright.expect(row.locator('details')).to_have_attribute('open', '')
    playwright.expect(row).to_contain_text(title)
    playwright.expect(page.get_by_role('dialog')).to_have_count(0)
    page.reruns.click(page, page.get_by_role('tab', name='Positions', exact=True))
    row = page.locator('tr[data-position-id="investment:synthetic"]')
    playwright.expect(row.get_by_label('Valuation details', exact=True)).to_be_visible()
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
