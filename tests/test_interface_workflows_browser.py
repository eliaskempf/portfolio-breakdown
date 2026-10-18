"""Approved interface workflows, using only temporary invented workspaces."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest
from portfolio_app import account_journal as ledger
from portfolio_app.generalized_studies_ui import save_study_settings
from portfolio_app.leverage_settings import load_leverage_settings
from test_actual_portfolio import invented
from test_generalized_studies_ui import config
from test_leveraged_funds import invented_workspace
from browser_support import settle
from test_futures_freshness_browser import futures_page  # noqa: F401

playwright = pytest.importorskip('playwright.sync_api')


@pytest.fixture
def workflow_page(tmp_path):
    accounts = tmp_path / 'accounts'; accounts.mkdir()
    _, journal, _ = invented(accounts)
    ledger.save_journal(accounts, journal, None)
    funds = tmp_path / 'funds'; funds.mkdir()
    invented_workspace(funds)
    studies = tmp_path / 'studies'; studies.mkdir()
    save_study_settings(studies, 'manual:EUR', config(), None)
    script = tmp_path / 'app.py'
    script.write_text(f'''
import sys
sys.path.insert(0, {str(Path(__file__).parent)!r})
from pathlib import Path
import streamlit as st
from portfolio_app.account_journal import load_journal
from portfolio_app.account_activity_ui import render_activity
from portfolio_app.portfolio_exposure_ui import render_leverage_editor
from portfolio_app.positions import read_snapshot
from portfolio_app.generalized_studies_ui import render_generalized_studies
from portfolio_app.stock_ui import render_stock_exposure
from test_stock_exposure import sources, funds
root=Path({str(tmp_path)!r})
st.set_page_config(layout='wide')
view=st.radio('Synthetic test view', ['Journal', 'Liabilities & funds', 'Studies', 'Companies'], horizontal=True)
if view == 'Journal':
    journal,revision=load_journal(root/'accounts')
    render_activity(root/'accounts', journal, revision, key='accounts_journal_')
elif view == 'Liabilities & funds':
    render_leverage_editor(root/'funds', read_snapshot(root/'funds'/'holdings.csv').holdings)
elif view == 'Studies':
    render_generalized_studies(root/'studies')
else:
    render_stock_exposure(sources(), funds(), root)
''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    with (tmp_path/'server.log').open('w') as log:
        process = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run', str(script),
            '--server.address=127.0.0.1', f'--server.port={port}', '--server.headless=true',
            '--server.fileWatcherType=none', '--browser.gatherUsageStats=false'], stdout=log, stderr=log)
        try:
            for _ in range(100):
                assert process.poll() is None
                try:
                    with urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=1):
                        break
                except OSError:
                    time.sleep(.1)
            with playwright.sync_playwright() as runner:
                browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                page.set_default_timeout(15000)
                page.goto(f'http://127.0.0.1:{port}')
                settle(page)
                yield page, tmp_path
                browser.close()
        finally:
            process.terminate(); process.wait(timeout=10)


def select(page, label, name):
    open_select(page, label)
    page.get_by_role('option', name=name, exact=True).click()
    settle(page)


def open_select(page, label):
    control = page.get_by_role('combobox', name=label, exact=True)
    trigger = control.locator('xpath=..').get_by_role('button', name='Open', exact=True)
    control.scroll_into_view_if_needed()
    settle(page)  # Native menus close on scrolling; finish scrolling before opening.
    (trigger if trigger.count() else control).click()
    try:
        playwright.expect(control).to_have_attribute('aria-expanded', 'true', timeout=500)
    except AssertionError:
        # After scrolling a native combobox can consume the first click to focus.
        # Open from the now-focused control; never force clicks through overlays.
        control.click()


def fill(page, label, value):
    field = page.get_by_role('textbox', name=label, exact=True)
    field.fill(value); field.press('Enter'); settle(page)


def test_journal_review_cancel_save_and_stale_revision(workflow_page):
    page, root = workflow_page
    original = (root/'accounts'/ledger.FILE).read_bytes()
    page.get_by_text('Recorded transactions', exact=True).click(); settle(page)
    select(page, 'Transaction details', '2026-01-02 · Fee · Invented account · SYNTH 2027-03-19 · 8.00 EUR')
    select(page, 'Entry action', 'Correct entry')
    playwright.expect(page.get_by_role('textbox', name='Corrected amount', exact=True)).to_have_value('8.00')
    fill(page, 'Corrected amount', '3')
    page.get_by_role('button', name='Review correction', exact=True).click(); settle(page)
    playwright.expect(page.get_by_role('button', name='Save correction', exact=True)).to_be_visible()
    assert (root/'accounts'/ledger.FILE).read_bytes() == original
    page.get_by_role('button', name='Cancel correction', exact=True).click(); settle(page)
    playwright.expect(page.get_by_role('button', name='Save correction', exact=True)).to_have_count(0)
    select(page, 'Entry action', 'Correct entry')
    fill(page, 'Corrected amount', '4')
    page.get_by_role('button', name='Review correction', exact=True).click(); settle(page)
    current, revision = ledger.load_journal(root/'accounts')
    external = ledger.append_event(current, ledger.Event('external', '2026-01-06', 'fee', account_id='a', currency='EUR', amount=1))
    ledger.save_journal(root/'accounts', external, revision)
    page.get_by_role('button', name='Save correction', exact=True).click(); settle(page)
    assert ledger.load_journal(root/'accounts')[0] == external
    playwright.expect(page.get_by_role('button', name='Save correction', exact=True)).to_have_count(0)
    page.get_by_role('button', name='Review correction', exact=True).click(); settle(page)
    page.get_by_role('button', name='Save correction', exact=True).click(); settle(page)
    saved, _ = ledger.load_journal(root/'accounts')
    assert saved.events[:-1] == external.events and saved.events[-1].replaces == 'fee'
    assert ledger.replay(saved).cash['a', 'EUR'] == 19995
    playwright.expect(page.get_by_text('Replacement:', exact=False)).to_be_visible()
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


def test_named_liability_and_company_controls(workflow_page):
    page, root = workflow_page
    page.get_by_text('Liabilities & funds', exact=True).click(); settle(page)
    page.get_by_text('Recorded liabilities & leveraged funds', exact=True).click(); settle(page)
    page.get_by_role('textbox', name='Liability name', exact=True).fill('Invented reserve loan')
    page.get_by_role('textbox', name='Account or lender', exact=True).fill('Invented lender')
    page.get_by_role('button', name='Save liability', exact=True).click(); settle(page)
    item = load_leverage_settings(root/'funds')[0].liabilities[0]
    assert item.balance_eur is None
    select(page, 'Liability', 'Invented reserve loan · Invented lender')
    page.get_by_role('textbox', name='Outstanding balance (EUR)', exact=True).fill('0')
    page.get_by_role('button', name='Save liability', exact=True).click(); settle(page)
    assert load_leverage_settings(root/'funds')[0].liabilities[0].balance_eur == 0
    playwright.expect(page.get_by_role('combobox', name='ETF position', exact=True)).to_have_value('Invented fund · Synthetic')
    page.get_by_role('textbox', name='Signed factor', exact=True).fill('-2')
    page.get_by_role('textbox', name='Source or method', exact=True).fill('Invented documented factor')
    page.get_by_role('button', name='Save fund factor', exact=True).click(); settle(page)
    settings = load_leverage_settings(root/'funds')[0]
    assert settings.liabilities[0].id == item.id and settings.fund_factors[0].factor == -2
    page.get_by_text('Companies', exact=True).click(); settle(page)
    playwright.expect(page.get_by_role('checkbox', name='Show stock-only company exposure')).to_have_count(0)
    playwright.expect(page.get_by_text('Selected stock universe:', exact=False)).to_be_visible()
    select(page, 'Exclude source buckets from stock exposure', 'active')
    playwright.expect(page.get_by_text('Selected stock universe:', exact=False)).to_contain_text('200.00')
    page.set_viewport_size({'width': 390, 'height': 844}); settle(page)
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


def test_study_results_precede_assumptions_and_inputs_stay_explicit(workflow_page):
    page, root = workflow_page
    before = (root/'accounts'/ledger.FILE).read_bytes()
    page.get_by_text('Studies', exact=True).click(); settle(page)
    page.get_by_role('button', name='Run study', exact=True).click(); settle(page)
    chart = page.locator('.js-plotly-plot')
    playwright.expect(chart).to_have_count(1)
    assumptions = page.get_by_text('Cash, collateral and currency', exact=True)
    assert chart.bounding_box()['y'] < assumptions.bounding_box()['y']
    playwright.expect(page.get_by_role('textbox', name='Product identifier', exact=True)).to_have_value('SYNTHETIC')
    assumptions.click(); settle(page)
    playwright.expect(page.get_by_role('spinbutton', name='Allowed settlement-currency debit', exact=True)).to_be_visible()
    field = page.get_by_role('spinbutton', name='Signed scenario contracts', exact=True)
    field.fill('2'); field.press('Enter'); settle(page)
    playwright.expect(page.get_by_text('Inputs changed. Run the study to refresh the results.', exact=True)).to_be_visible()
    playwright.expect(chart).to_have_count(0)
    assert (root/'accounts'/ledger.FILE).read_bytes() == before


def test_journal_selection_and_draft_survive_main_app_navigation(futures_page):
    from browser_support import select_tab
    page, root, journal, _ = futures_page
    select_tab(page, 'Futures')
    page.get_by_text('Recorded transactions', exact=True).click(); settle(page)
    open_select(page, 'Transaction details')
    page.get_by_role('option').filter(has_text=' · Trade · ').click(); settle(page)
    open_select(page, 'Transaction details')
    page.get_by_role('option').filter(has_text=' · Fee · ').click(); settle(page)
    select(page, 'Entry action', 'Correct entry')
    fill(page, 'Corrected amount', '3')
    select_tab(page, 'Exposure')
    select_tab(page, 'Futures')
    page.get_by_text('Recorded transactions', exact=True).click(); settle(page)
    playwright.expect(page.get_by_role('textbox', name='Corrected amount', exact=True)).to_have_value('3')
    page.get_by_role('button', name='Review correction', exact=True).click(); settle(page)
    playwright.expect(page.get_by_text('Accounting effects', exact=True)).to_be_visible()
    page.get_by_role('button', name='Cancel correction', exact=True).click(); settle(page)
    select(page, 'Entry action', 'Correct entry')
    playwright.expect(page.get_by_role('textbox', name='Corrected amount', exact=True)).to_have_value('8.00')
    assert ledger.load_journal(root)[0] == journal
