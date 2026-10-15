"""Real browser checks of normal-app futures freshness using invented data only."""
from dataclasses import replace
from datetime import timedelta
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest
from portfolio_app import account_journal as ledger
from portfolio_app.automatic_settlements import apply_archived_settlements
from portfolio_app.derivative_market import make_contract
from portfolio_app.derivative_types import PriceObservation
from portfolio_app.futures_clock import business_date, utc_now
from portfolio_app.futures_quotes import record_result
from portfolio_app.settlement_archive import SettlementArchive
from test_futures_investments import grouped
from browser_support import settle, select_tab

playwright = pytest.importorskip('playwright.sync_api')


def preview_workspace(root):
    root.mkdir(exist_ok=True)
    _, journal, _ = grouped(root)
    contract = make_contract('FMWN','2027-03-19')
    journal = replace(journal, contracts=(contract,), positions=(replace(journal.positions[0],contract_id=contract.id),))
    ledger.save_journal(root,journal,None)
    now = utc_now()
    old = now-timedelta(days=2)
    mark = PriceObservation(contract.id,business_date(old).isoformat(),1020,kind='mark',source='Invented delayed quote',
                            observed_at=now.isoformat(),quote_at=old.isoformat(),delay_minutes=15)
    record_result(root,contract,mark,'Invented temporary outage',attempted_at=now,now=now)
    return journal,contract


def preview_script(root):
    return f'''
from pathlib import Path
from types import SimpleNamespace
from portfolio_app.ui import render_app
from portfolio_app.prices import PriceService, StaticProvider
from portfolio_app.history import HistoryResult
from portfolio_app.futures_quotes import collector as quotes
from portfolio_app.settlement_collection import collector as settlements
from portfolio_app.etf_refresh import coordinator
import portfolio_app.market_history_ui as charts
quotes.schedule = lambda *a, **kw: False
settlements.schedule = lambda *a, **kw: False
coordinator.schedule = lambda *a, **kw: False
charts.history_for = lambda _: SimpleNamespace(get=lambda *a, **kw: HistoryResult(
    ('2026-01-02','2026-01-05','2026-01-06'), (100., 105., 98.), 'EUR', 'cached',
    '2026-01-07', 'Invented market context'), pending=lambda *a: False)
root = Path({str(root)!r})
render_app(root, demo=False, ignore_selection=True, price_service=PriceService(StaticProvider(root/'demo_prices.json')))
'''


@pytest.fixture
def futures_page(tmp_path):
    root = tmp_path/'synthetic'
    journal,contract = preview_workspace(root)
    script = tmp_path/'preview.py'
    script.write_text(preview_script(root))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    process = subprocess.Popen([sys.executable,'-m','streamlit','run',str(script),
        '--server.address=127.0.0.1',f'--server.port={port}','--server.headless=true',
        '--server.fileWatcherType=none','--browser.gatherUsageStats=false'],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            assert process.poll() is None
            try:
                urlopen(f'http://127.0.0.1:{port}/_stcore/health',timeout=1);break
            except OSError:time.sleep(.1)
        with playwright.sync_playwright() as runner:
            browser=runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'),args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1100})
            page.set_default_timeout(20000)
            page.goto(f'http://127.0.0.1:{port}')
            yield page,root,journal,contract
            browser.close()
    finally:
        process.terminate();process.wait(timeout=10)


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
    select_tab(page,'Positions')
    row=page.locator('tr[data-position-id="investment:synthetic"]')
    playwright.expect(row.locator('summary')).to_contain_text('Stale')
    row.locator('td').first.click()
    dialog=page.get_by_role('dialog');dialog.wait_for();settle(page)
    playwright.expect(dialog.locator('.js-plotly-plot')).to_have_count(1)
    playwright.expect(dialog.get_by_text('Net P&L',exact=True)).to_be_visible()
    assert dialog.locator('[class*="st-key-gain_positive_"]').count()==0
    for view in ['Settlements','Contract history','Chart']:
        dialog.get_by_text(view,exact=True).first.click();settle(page)
    dialog.get_by_role('button',name='Close',exact=True).filter(has_text='Close').click();settle(page)
    playwright.expect(dialog).to_have_count(0)
    # This synthetic workspace without a quote still displays a dated
    # execution fallback, never an unqualified positive current valuation.
    saved_quotes = root/'invented-saved-quotes.json'
    (root/'futures-quotes.json').rename(saved_quotes)
    page.get_by_role('button',name='Refresh prices').click();settle(page)
    row=page.locator('tr[data-position-id="investment:synthetic"]')
    playwright.expect(row.locator('summary')).to_contain_text('Recorded')
    assert row.locator('td.positive,td.negative').count()==0
    saved_quotes.rename(root/'futures-quotes.json')
    now=utc_now();stamp=now-timedelta(minutes=15)
    fresh=PriceObservation(contract.id,business_date(stamp).isoformat(),990,kind='mark',
        source='Invented delayed quote',observed_at=now.isoformat(),quote_at=stamp.isoformat(),delay_minutes=15)
    record_result(root,contract,fresh,'',attempted_at=now,now=now)
    page.get_by_role('button',name='Refresh prices').click();settle(page)
    row=page.locator('tr[data-position-id="investment:synthetic"]')
    playwright.expect(row.locator('summary')).to_contain_text('Delayed')
    assert row.locator('td.negative').count()==1
    assert ledger.load_journal(root)[0]==journal
    before_value=row.locator('td').nth(3).inner_text()
    day=business_date(now)-timedelta(days=1)
    while day.weekday()>4:day-=timedelta(days=1)
    SettlementArchive(root).record(contract,PriceObservation(contract.id,day.isoformat(),1005,source='Invented daily settlement'))
    assert apply_archived_settlements(root)==1
    assert apply_archived_settlements(root)==0
    page.get_by_role('button',name='Refresh prices').click();settle(page)
    assert row.locator('td').nth(3).inner_text()==before_value
    playwright.expect(row.locator('summary')).to_contain_text('Delayed')
    record_result(root,contract,None,'Invented refresh failure',attempted_at=utc_now(),now=utc_now())
    page.get_by_role('button',name='Refresh prices').click();settle(page)
    row.locator('summary').click()
    playwright.expect(row).to_contain_text('Invented refresh failure')
    select_tab(page,'Futures')
    page.get_by_text('Marks & FX',exact=True).click();settle(page)
    page.get_by_role('textbox',name='Manual mark price',exact=True).fill('995')
    page.get_by_role('textbox',name='Manual mark price',exact=True).press('Enter');settle(page)
    playwright.expect(page.get_by_role('button',name='Save mark only',exact=True)).to_be_visible()
    select_tab(page,'Overview')
    page.set_viewport_size({'width':750,'height':1000});settle(page)
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
