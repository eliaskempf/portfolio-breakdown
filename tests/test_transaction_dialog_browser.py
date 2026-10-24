"""Transaction workflows keep their dialog mounted using invented holdings only."""
import os
import json
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

from browser_support import settle
from portfolio_app.account_journal import load_journal, save_journal, Journal
from portfolio_app.security_history import SecurityBook, SecurityEvent
from decimal import Decimal
from test_ux_browser import ux_page  # noqa: F401

playwright = pytest.importorskip('playwright.sync_api')


@pytest.fixture
def transaction_page(tmp_path, request):
    scenario = getattr(request, 'param', 'normal')
    (tmp_path/'holdings.csv').write_text(
        'id,name,ticker,shares,acquisition_price,acquisition_currency\n'
        'invented,Invented holding,SYNTH,2,10,EUR\n')
    event = SecurityEvent('buy', 'buy', '2024-01-02', quantity=Decimal(2),
                          price=Decimal(10), currency='USD' if scenario == 'fx' else 'EUR', charges_known=scenario != 'fees')
    save_journal(tmp_path, Journal(version=2, securities=(SecurityBook(
        'position-0', 'invented', ('2', '10', 'EUR'), records=(event,)),)), None)
    (tmp_path/'demo_prices.json').write_text(json.dumps({'prices': {'SYNTH': {'price': 12, 'currency': 'EUR', 'observed_at': '2026-10-18T12:00:00+00:00'}}, 'fx': {}}))
    script = tmp_path/'preview.py'
    script.write_text('''
from pathlib import Path
import time
import streamlit as st
from portfolio_app.input_cache import load_inputs
from portfolio_app.application import prepare_application, select_price_service
from portfolio_app.position_ui import request_position, render_position_dialog
from portfolio_app.history import HistoryResult
from portfolio_app.presentation import apply_style
import portfolio_app.position_detail as detail
import portfolio_app.history_ui as history
from portfolio_app.personal_history_refresh import refresher, RefreshStatus
refresher.request=lambda *a,**k:RefreshStatus()
root=Path(__file__).parent
if not hasattr(history, '_test_preview'):
    history._test_preview=history.preview_activation
    def delayed(*a, **kw):
        time.sleep(.7)
        return history._test_preview(*a, **kw)
    history.preview_activation=delayed
class InventedHistory:
    def pending(self,*a):return False
    def get(self,*a,**kw):return HistoryResult(('2024-01-02','2025-01-02'),(10.,11.),'EUR','cached')
detail.history_for=lambda _:InventedHistory()
apply_style()
st.session_state.runs=st.session_state.get('runs',0)+1
st.html(f'<span id="page-runs">{st.session_state.runs}</span>')
time.sleep(.7)
st.button('Open position',on_click=request_position,args=('position-0',))
inputs=load_inputs(root)
prices=select_price_service(root,offline_demo=True)
valued=prepare_application(inputs,prices).valued
render_position_dialog(root/'holdings.csv',inputs.snapshot,{},demo=False,valued=valued,prices=prices)
''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    process = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run', str(script),
        '--server.address=127.0.0.1', f'--server.port={port}', '--server.headless=true',
        '--server.fileWatcherType=none', '--browser.gatherUsageStats=false'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=1); break
            except OSError:
                time.sleep(.1)
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
            page = browser.new_page(viewport={'width':1280, 'height':720})
            page.goto(f'http://127.0.0.1:{port}')
            page.get_by_role('button', name='Open position', exact=True).click()
            page.get_by_role('dialog').wait_for(); settle(page)
            yield page, tmp_path
            browser.close()
    finally:
        process.terminate(); process.wait(timeout=10)


def watch_dialog(page):
    page.get_by_role('dialog').evaluate('''e => {
        window.originalDialog=e;
        window.missingDialogFrames=0;
        const sample=()=>{
            const r=e.getBoundingClientRect();
            if(!e.isConnected || !r.height || getComputedStyle(e).visibility==='hidden')
                window.missingDialogFrames++;
            window.dialogFrame=requestAnimationFrame(sample);
        };
        sample();
    }''')


@pytest.mark.parametrize('action', ['save', 'review'])
def test_save_and_review_keep_dialog_visible(transaction_page, action):
    page, root = transaction_page
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('radio', name='Transactions', exact=True).click(); settle(page)
    original, revision = load_journal(root)
    if action == 'save':
        dialog.get_by_role('button', name='Add buy', exact=False).click(); settle(page)
        for name, value in [('Quantity', '1'), ('Unit price', '12'), ('Fees', '0')]:
            dialog.get_by_role('textbox', name=name, exact=True).fill(value)
            dialog.get_by_role('textbox', name=name, exact=True).press('Tab'); settle(page)
        button = dialog.get_by_role('button', name='Save transaction', exact=True)
    else:
        button = dialog.get_by_role('button', name='Review against portfolio', exact=True)
    runs = page.locator('#page-runs').inner_text()
    values = dialog.get_by_test_id('stMetricValue').all_text_contents()
    watch_dialog(page)
    button.click(); settle(page)
    assert page.evaluate('window.missingDialogFrames') == 0
    assert dialog.evaluate('e=>e===window.originalDialog')
    assert page.locator('#page-runs').inner_text() == runs
    assert dialog.get_by_test_id('stMetricValue').all_text_contents() == values
    current, current_revision = load_journal(root)
    if action == 'save':
        assert current.securities[0].records[:-1] == original.securities[0].records
        assert len(current.securities[0].records) == 2
        assert not current.securities[0].active
        playwright.expect(dialog.get_by_role('button', name='Review against portfolio', exact=True)).to_be_visible()
    else:
        assert current_revision == revision
        playwright.expect(dialog.get_by_role('button', name='Apply and confirm history', exact=True)).to_be_visible()
    page.evaluate('cancelAnimationFrame(window.dialogFrame)')


def test_first_history_preserves_tabs_selection_and_valuation(ux_page):
    from browser_support import select_tab
    page, root = ux_page
    select_tab(page, 'Positions')
    table = page.get_by_role('table', name='Positions', exact=True)
    table.locator('tr[data-position-id="fund-a"] td').first.click(); settle(page)
    dialog = page.get_by_role('dialog')
    tabs = dialog.get_by_role('radiogroup', name='Position detail view')
    before = tabs.get_by_role('radio').all_text_contents()
    playwright.expect(tabs.get_by_role('radio', name='History', exact=True)).to_be_checked()
    playwright.expect(dialog.get_by_role('radiogroup', name='History type')).to_have_count(0)
    dialog.locator('.js-plotly-plot').wait_for()
    dialog.get_by_role('radio', name='Transactions', exact=True).click(); settle(page)
    values = dialog.get_by_test_id('stMetricValue').all_text_contents()
    watch_dialog(page)
    dialog.get_by_role('button', name='Add buy', exact=False).click(); settle(page)
    for name, value in [('Quantity', '2'), ('Unit price', '80'), ('Fees', '0')]:
        field = dialog.get_by_role('textbox', name=name, exact=True)
        field.fill(value); field.press('Tab'); settle(page)
    dialog.get_by_role('button', name='Save transaction', exact=True).click(); settle(page)
    assert dialog.get_by_test_id('stMetricValue').all_text_contents() == values
    dialog.get_by_role('button', name='Review against portfolio', exact=True).click(); settle(page)
    assert page.evaluate('window.missingDialogFrames') == 0
    assert dialog.evaluate('e=>e===window.originalDialog')
    dialog.get_by_role('button', name='Apply and confirm history', exact=True).click(); settle(page)
    assert page.evaluate('window.missingDialogFrames') == 0
    assert dialog.evaluate('e=>e===window.originalDialog')
    page.evaluate('cancelAnimationFrame(window.dialogFrame)')
    applied = next(book for book in load_journal(root)[0].securities if book.position_id == 'fund-a')
    assert applied.reviews[-1].complete_from and applied.reviews[-1].complete_through
    assert tabs.get_by_role('radio').all_text_contents() == before
    playwright.expect(tabs.get_by_role('radio', name='Transactions', exact=True)).to_be_checked()
    dialog.get_by_role('button', name='Close', exact=True).last.click(); settle(page)
    assert not page.get_by_role('button', name='Confirm existing histories', exact=True).count()
    # Visiting another position resets the selection so each history-dependent
    # default is checked independently of the retained navigation preference.
    table.locator('tr[data-position-id="stock-c"] td').first.click(); settle(page)
    playwright.expect(dialog.get_by_role('radiogroup', name='History type')).to_have_count(0)
    dialog.get_by_role('button', name='Close', exact=True).last.click(); settle(page)
    table.locator('tr[data-position-id="fund-a"] td').first.click(); settle(page)
    playwright.expect(dialog.get_by_role('radio', name='History', exact=True)).to_be_checked()
    playwright.expect(dialog.get_by_role('radio', name='Holdings', exact=True)).to_be_checked()
    assert tabs.get_by_role('radio').all_text_contents() == ['History', 'Key metrics', 'Transactions', 'Details']
    assert load_journal(root)[0].securities[0].active
    dialog.get_by_role('radio', name='Price', exact=True).click(); settle(page)
    dialog.get_by_role('button', name='Close', exact=True).last.click(); settle(page)
    table.locator('tr[data-position-id="stock-c"] td').first.click(); settle(page)
    playwright.expect(dialog.get_by_role('radiogroup', name='History type')).to_have_count(0)
    dialog.get_by_role('button', name='Close', exact=True).last.click(); settle(page)
    table.locator('tr[data-position-id="fund-a"] td').first.click(); settle(page)
    playwright.expect(dialog.get_by_role('radio', name='Price', exact=True)).to_be_checked()


def test_history_selector_remembers_choice_and_keeps_empty_holdings_action_local(transaction_page):
    page, root = transaction_page
    original, revision = load_journal(root)
    dialog = page.get_by_role('dialog')
    watch_dialog(page)
    playwright.expect(dialog.get_by_role('radio', name='Price', exact=True)).to_be_checked()
    dialog.get_by_role('radio', name='Holdings', exact=True).click(); settle(page)
    playwright.expect(dialog.get_by_text('Add and review transactions to see your holdings history.', exact=True)).to_be_visible()
    playwright.expect(dialog.locator('.js-plotly-plot')).to_have_count(0)
    dialog.get_by_role('button', name='Add transactions', exact=True).click(); settle(page)
    playwright.expect(dialog.get_by_role('radio', name='Transactions', exact=True)).to_be_checked()
    dialog.get_by_role('radio', name='History', exact=True).click(); settle(page)
    playwright.expect(dialog.get_by_role('radio', name='Holdings', exact=True)).to_be_checked()
    dialog.get_by_role('radio', name='Price', exact=True).click(); settle(page)
    dialog.locator('.js-plotly-plot').wait_for()
    assert page.evaluate('window.missingDialogFrames') == 0
    assert dialog.evaluate('e=>e===window.originalDialog')
    page.evaluate('cancelAnimationFrame(window.dialogFrame)')
    dialog.get_by_role('button', name='Close', exact=True).last.click(); settle(page)
    page.get_by_role('button', name='Open position', exact=True).click(); settle(page)
    playwright.expect(dialog.get_by_role('radio', name='Price', exact=True)).to_be_checked()
    assert load_journal(root) == (original, revision)


@pytest.mark.parametrize('native', [True, False])
def test_clean_open_and_close_do_not_rebuild_portfolio(transaction_page, native):
    page, root = transaction_page
    before = load_journal(root)
    assert page.locator('#page-runs').inner_text() == '1'
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('button', name='Close', exact=True).nth(0 if native else -1).click()
    playwright.expect(dialog).to_have_count(0)
    settle(page)
    assert page.locator('#page-runs').inner_text() == '1'
    page.get_by_role('button', name='Open position', exact=True).click(); settle(page)
    playwright.expect(dialog).to_be_visible()
    assert page.locator('#page-runs').inner_text() == '1'
    assert load_journal(root) == before


@pytest.mark.parametrize('transaction_page', ['fees', 'fx'], indirect=True)
def test_review_repairs_and_apply_share_dialog_refresh_boundary(transaction_page):
    from portfolio_app.history_observations import load_observations
    page, root = transaction_page
    original, _ = load_journal(root)
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('radio', name='Transactions', exact=True).click(); settle(page)
    watch_dialog(page)
    dialog.get_by_role('button', name='Review against portfolio', exact=True).click(); settle(page)
    if original.securities[0].records[0].currency == 'USD':
        field, value, action = '2024-01-02 · EUR per 1 USD', '0.9', 'Save exchange rate'
    else:
        field, value, action = 'Fees (EUR)', '0', 'Save fees'
    dialog.get_by_role('textbox', name=field, exact=True).fill(value)
    dialog.get_by_role('textbox', name=field, exact=True).press('Tab'); settle(page)
    dialog.get_by_role('button', name=action, exact=True).click(); settle(page)
    dialog.get_by_role('button', name='Apply and confirm history', exact=True).click(); settle(page)
    assert load_journal(root)[0].securities[0].active
    assert load_journal(root)[0].securities[0].records[0] == original.securities[0].records[0]
    dialog.get_by_role('button', name='Review against portfolio', exact=True).click(); settle(page)
    assert not dialog.get_by_role('button', name='Confirm complete history', exact=True).count()
    from portfolio_app.history_observations import HistoryScope
    from portfolio_app.personal_performance import coverage_reasons
    saved = load_journal(root)[0]
    review = saved.securities[0].reviews[-1]
    assert review.complete_from and review.complete_through
    assert not coverage_reasons(saved, HistoryScope(('position-0',)), load_observations(root)[0],
                                review.complete_from, review.complete_through)
    assert page.locator('#page-runs').inner_text() == '1'
    assert page.evaluate('window.missingDialogFrames') == 0
    assert dialog.evaluate('e=>e===window.originalDialog')
    assert not page.get_by_test_id('stException').count()
    page.evaluate('cancelAnimationFrame(window.dialogFrame)')
    dialog.get_by_role('button', name='Close', exact=True).first.click(); settle(page)
    playwright.expect(dialog).to_have_count(0)
    assert page.locator('#page-runs').inner_text() == '2'
