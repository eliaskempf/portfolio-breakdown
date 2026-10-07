"""Real app navigation using invented bond/rate funds and offline prices."""
from datetime import date
from dataclasses import replace
import os
import re
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pandas as pd
import pytest

from portfolio_app.etf import FundSnapshot
from portfolio_app.etf_sources import install_snapshot, publish_snapshot
from test_etf_discovery import Provider, FUND, invented_isin

playwright = pytest.importorskip('playwright.sync_api')


@pytest.fixture
def fund_page(tmp_path):
    full = install_snapshot(tmp_path / 'etfs', FUND, fetch=Provider())
    partial = replace(full, fund_id='partial', name='Invented partial bonds', isin=invented_isin(444),
                      manifest_path=tmp_path / 'etfs/partial.yaml')
    partial_frame = full.constituents.iloc[:1].copy()
    partial_frame['name'] = 'Invented partial bond'
    partial_frame['constituent_id'] = 'partial:bond'
    partial_frame['isin'] = invented_isin(445)
    publish_snapshot(partial, partial_frame, full.as_of, notes='Partial breakdown: invented top holdings.')
    rate_isin = invented_isin(333)
    frame = pd.DataFrame([dict(constituent_id='overnight:invented', name='Invented overnight exposure', isin='', ticker='',
        weight=1., instrument_type='overnight_rate', exposure_kind='non_equity', market_currency='EUR')])
    basket = pd.DataFrame([dict(constituent_id='basket:a', name='Invented substitute bond', isin='', ticker='', weight=.9, instrument_type='bond'),
                           dict(constituent_id='basket:b', name='Invented substitute cash', isin='', ticker='', weight=.1, instrument_type='cash')])
    rate = FundSnapshot('rate', 'Invented overnight fund', rate_isin, (), date.min, 'https://example.invalid',
        pd.DataFrame(columns=['isin', 'constituent_id']), tmp_path / 'etfs/rate.yaml',
        asset_class='money_market', replication='synthetic', breakdown_basis='economic')
    publish_snapshot(rate, frame, date(2026, 1, 2), basket=basket)
    (tmp_path / 'holdings.csv').write_text('id,name,isin,shares,instrument_type,manual_price,manual_price_currency,manual_price_date,quantity_unit\n'
        f'fund,Invented fund,{FUND},1,etf,100,EUR,2026-01-02,units\n'
        f'partial,Invented partial bonds,{partial.isin},1,etf,100,EUR,2026-01-02,units\n'
        f'rate,Invented overnight fund,{rate_isin},1,etf,100,EUR,2026-01-02,units\n')
    app = tmp_path / 'app.py'
    app.write_text('''from pathlib import Path
from portfolio_app.ui import render_app
from portfolio_app.prices import PriceService, UnavailableProvider
render_app(Path(__file__).parent, demo=True, price_service=PriceService(UnavailableProvider()))
''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run', str(app), '--server.address=127.0.0.1',
        f'--server.port={port}', '--server.headless=true', '--server.fileWatcherType=none', '--browser.gatherUsageStats=false'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=tmp_path)
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
            page = browser.new_page(viewport={'width': 1440, 'height': 1100})
            page.goto(f'http://127.0.0.1:{port}')
            page.get_by_role('tab', name='Exposure', exact=True).click()
            page.get_by_role('table', name='Exposure assets', exact=True).wait_for()
            yield page
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


def detail(page, asset, fund):
    table = page.get_by_role('table', name='Exposure assets', exact=True)
    table.get_by_text(asset, exact=True).click()
    page.get_by_role('button', name=f'Details for {asset}', exact=True).click()
    dialog = page.get_by_role('dialog')
    dialog.get_by_text(f'ETF breakdown: {fund}', exact=True).click()
    return dialog


def test_bond_summary_defaults_charts_and_security_details(fund_page):
    page = fund_page
    dialog = detail(page, 'Invented issuer bond one', 'Invented fund')
    def choose_summary(name):
        control = dialog.get_by_role('combobox', name='Summarize by')
        # Keep the selector away from the dialog's bottom edge before opening
        # its popup. Scrolling to an off-screen option can close that popup.
        page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
        page.wait_for_function("""() => !document.querySelector('[data-testid=stDialog]')
            .getAnimations({subtree: true}).some(animation => animation.playState === 'running'
                && animation.effect.getTiming().iterations !== Infinity)""")
        control.evaluate("el => el.scrollIntoView({block: 'center', inline: 'nearest', behavior: 'instant'})")
        control.evaluate('el => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        control.click()
        page.get_by_role('option', name=name, exact=True).click()
    dialog.get_by_role('combobox', name='Summarize by').wait_for()
    playwright.expect(dialog.locator('.js-plotly-plot')).to_have_count(1)
    playwright.expect(dialog.locator('.js-plotly-plot')).to_be_visible()
    choose_summary('Maturity')
    # The caption below is also present for Issuer. Wait for the new chart,
    # otherwise the previous selection's rerun can close the next dropdown.
    page.wait_for_function("""[...document.querySelectorAll('[role=dialog] .js-plotly-plot')]
        .some(chart => chart.layout?.yaxis?.title?.text === 'Maturity')""")
    playwright.expect(dialog.get_by_text('Calculated from holdings dated 2026-01-02; missing metadata remains Unknown.')).to_be_visible()
    choose_summary('Credit quality')
    playwright.expect(dialog.get_by_text('Provider aggregate', exact=False)).to_be_visible()
    dialog.get_by_role('radio', name='Holdings', exact=True).click()
    dialog.get_by_role('table', name='Invented fund holdings', exact=True).wait_for()
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


def test_overnight_economic_view_and_separate_basket(fund_page):
    page = fund_page
    dialog = detail(page, 'Invented overnight exposure', 'Invented overnight fund')
    playwright.expect(dialog.get_by_text('100% economic representation', exact=False)).to_be_visible()
    playwright.expect(dialog.get_by_text('Invented overnight exposure. This represents', exact=False)).to_be_visible()
    playwright.expect(dialog.get_by_text('Solactive', exact=False)).to_have_count(0)
    dialog.get_by_role('radio', name='Holdings', exact=True).click()
    dialog.get_by_text('Actual substitute basket · excluded from portfolio exposure', exact=True).click()
    playwright.expect(dialog.get_by_text('Net basket coverage', exact=False)).to_be_visible()
    # The caption arrives before the holdings component and the final Close
    # control. Do not click the previous fragment's button during its replacement.
    playwright.expect(dialog.get_by_role('table', name='Invented overnight fund holdings', exact=True)).to_be_visible()
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
    # The expanded basket can still animate/scroll after the server becomes idle.
    page.wait_for_function("""() => !document.querySelector('[data-testid=stDialog]')
        .getAnimations({subtree: true}).some(animation => animation.playState === 'running'
            && animation.effect.getTiming().iterations !== Infinity)""")
    close = dialog.get_by_role('button', name='Close exposure details')
    close.evaluate("el => el.scrollIntoView({block: 'center', inline: 'nearest', behavior: 'instant'})")
    close.evaluate('el => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    close.click()
    playwright.expect(dialog).to_have_count(0)
    page.get_by_role('button', name='Data & settings', exact=True).click()
    page.get_by_text('ETF refresh & snapshots', exact=True).click()
    playwright.expect(page.get_by_text('Set up a breakdown', exact=True)).to_be_visible()


def test_saved_breakdown_accessible_from_settings_without_live_listing(fund_page):
    page = fund_page
    page.get_by_role('button', name='Data & settings', exact=True).click()
    page.get_by_text('ETF refresh & snapshots', exact=True).click()
    page.get_by_text('ETF breakdown: Invented fund', exact=True).click()
    page.get_by_role('combobox', name='Summarize by').wait_for()
    page.get_by_role('radio', name='Holdings', exact=True).click()
    playwright.expect(page.get_by_text('Fund composition is available independently of position pricing.')).to_be_visible()
    page.get_by_role('table', name='Invented fund holdings', exact=True).wait_for()
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


def test_partial_bond_summary_shows_coverage_other_and_holdings(fund_page):
    page = fund_page
    dialog = detail(page, 'Invented partial bond', 'Invented partial bonds')
    playwright.expect(dialog.get_by_text('Partial holdings summary · 60.00% covered', exact=False)).to_be_visible()
    playwright.expect(dialog.locator('.js-plotly-plot')).to_be_visible()
    page.wait_for_function("""[...document.querySelectorAll('[role=dialog] .js-plotly-plot')]
        .some(chart => chart.data?.some(trace => trace.y?.includes('Other')))""")
    dialog.get_by_role('radio', name='Holdings', exact=True).click()
    table = dialog.get_by_role('table', name='Invented partial bonds holdings', exact=True)
    playwright.expect(table.get_by_text('Invented partial bonds / Other', exact=True)).to_be_visible()
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


@pytest.mark.parametrize('isin,ticker,exchange', [
    ('DE0005933931', 'EXS1.DE', 'Xetra'),
    ('LU1190417599', 'CSH2.PA', 'Euronext Paris'),
])
def test_isin_search_fills_verified_listing_identity(fund_page, isin, ticker, exchange):
    page = fund_page
    page.get_by_role('tab', name='Positions', exact=True).click()
    page.get_by_role('button', name=re.compile(r'Add position$')).click()
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('searchbox', name='Find an investment', exact=True).fill(isin)
    # Search is debounced; wait for this result before inspecting its chooser.
    selection = dialog.get_by_role('button', name=f'Select {ticker} on {exchange}', exact=True, include_hidden=True)
    playwright.expect(selection).to_be_attached()
    chooser = dialog.locator('summary').filter(has_text='Choose listing')
    if chooser.count():
        chooser.first.click()
    selection.click()
    playwright.expect(dialog.get_by_role('textbox', name='Ticker', exact=True)).to_have_value(ticker)
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
    dialog.get_by_text('More details', exact=True).click()
    playwright.expect(dialog.get_by_role('textbox', name='ISIN (optional)', exact=True)).to_have_value(isin)
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
