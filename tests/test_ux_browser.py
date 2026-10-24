"""Full app regressions with invented positions and offline prices only."""
import json
import os
import re
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest
import yaml

from browser_support import select_tab

playwright = pytest.importorskip('playwright.sync_api')


@pytest.fixture
def ux_page(tmp_path):
    (tmp_path / 'holdings.csv').write_text(
        'position_key,id,name,ticker,shares,acquisition_price,acquisition_currency,account,bucket_id,within_bucket_target\n'
        'fund-a,a,Invented Global UCITS ETF Acc,SYNTH-A,2,80,EUR,First,core,0.6\n'
        'fund-b,b,Invented Regional UCITS ETF Acc,SYNTH-B,1,60,EUR,Second,core,0.4\n'
        'stock-c,c,Invented Satellite,SYNTH-C,1,50,EUR,First,active,1\n')
    (tmp_path / 'allocation.yaml').write_text(yaml.safe_dump({'version': 2, 'buckets': [
        dict(id='core', name='ETF core', target=.7), dict(id='active', name='Satellites', target=.3)]}))
    (tmp_path / 'demo_prices.json').write_text(json.dumps({'prices': {
        ticker: dict(price=price, currency='EUR', observed_at='2026-09-01T12:00:00+00:00')
        for ticker, price in [('SYNTH-A', 100), ('SYNTH-B', 50), ('SYNTH-C', 80)]}, 'fx': {}}))
    app = tmp_path / 'app.py'
    app.write_text('from pathlib import Path\nfrom portfolio_app.ui import render_app\n'
                   'render_app(Path(__file__).parent, demo=True)\n')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    process = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run', str(app),
        '--server.address=127.0.0.1', f'--server.port={port}', '--server.headless=true',
        '--server.fileWatcherType=none', '--browser.gatherUsageStats=false', '--client.toolbarMode=viewer'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=1); break
            except OSError:
                time.sleep(.1)
        with playwright.sync_playwright() as runner:
            browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1440, 'height': 1000}, locale='en-US')
            page.set_default_timeout(10000)
            page.goto(f'http://127.0.0.1:{port}')
            page.locator('.js-plotly-plot').first.wait_for()
            yield page, tmp_path
            browser.close()
    finally:
        process.terminate(); process.wait(timeout=10)


def click_slice(page, label):
    chart = page.locator('.js-plotly-plot').first
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
    page.wait_for_function("!!document.querySelector('.js-plotly-plot')?._ev?._events?.plotly_sunburstclick")
    text = chart.locator('text.slicetext').filter(has_text=re.compile(r'\s*'.join(map(re.escape, label.split()))))
    # Plotly's SVG labels delegate pointer events to the slice underneath.
    # Wait for layout, retrying only when a rerun replaces the element before
    # a click has been dispatched.
    deadline = time.monotonic() + 10
    while True:
        try:
            text.scroll_into_view_if_needed()
            bounds = text.bounding_box()
            if bounds is not None:
                break
        except playwright.Error as error:
            if 'not attached to the DOM' not in str(error):
                raise
        if time.monotonic() >= deadline:
            raise AssertionError(f'Chart label did not settle: {label}')
    page.mouse.click(bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2)


def open_settings(page):
    # Metric text arrives before Streamlit finishes replacing the header. Opening
    # its popover during that rerun can lose the click when the header is replaced.
    playwright.expect(page.get_by_test_id('stApp')).to_have_attribute('data-test-script-state', 'notRunning')
    # A metric rerun can retain the already-open popover. Opening settings must
    # not toggle that visible panel closed before checking its controls.
    panel = page.get_by_test_id('stPopoverBody')
    if not panel.is_visible():
        page.get_by_role('button', name='tune Portfolio settings', exact=True).click()
    playwright.expect(panel).to_be_visible()


def assert_gain_color(locator, *, positive):
    color = locator.evaluate('el => getComputedStyle(el).color')
    channels = [int(value) for value in re.findall(r'\d+', color)[:3]]
    assert (channels[1] > channels[0]) if positive else (channels[0] > channels[1])


def test_category_scope_performance_and_tab_roundtrip(ux_page):
    page, directory = ux_page
    value_card = page.locator('.st-key-overview_value').get_by_test_id('stMetric')
    playwright.expect(value_card).to_contain_text('€330.00')
    playwright.expect(value_card.get_by_test_id('stMetricDelta')).to_contain_text('+€60.00')
    assert_gain_color(value_card.get_by_test_id('stMetricDelta'), positive=True)
    playwright.expect(page.locator('.st-key-strategic_crumb_')).to_have_count(0)
    value_card.get_by_role('button', name='Show gain as percentage').click()
    playwright.expect(value_card.get_by_test_id('stMetricDelta')).to_have_text('—')
    open_settings(page)
    playwright.expect(page.get_by_role('radio', name='%', exact=True)).to_be_checked()
    page.keyboard.press('Escape')
    # The metric text can arrive before the rerun finishes attaching controls.
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
    value_card.get_by_role('button', name='Show gain as amount').press('Enter')
    playwright.expect(value_card.get_by_test_id('stMetricDelta')).to_contain_text('+€60.00')
    open_settings(page)
    playwright.expect(page.get_by_role('radio', name='€', exact=True)).to_be_checked()
    page.keyboard.press('Escape')
    value_box = value_card.get_by_test_id('stMetricValue').bounding_box()
    gain_box = value_card.get_by_test_id('stMetricDelta').bounding_box()
    assert gain_box['x'] >= value_box['x'] + value_box['width']
    assert abs(gain_box['y'] + gain_box['height']/2 - value_box['y'] - value_box['height']/2) < 12
    allocation = page.locator('.st-key-overview_allocation')
    chart_box = allocation.get_by_test_id('stPlotlyChart').bounding_box()
    summary_box = allocation.locator('.st-key-overview_allocation_summary').bounding_box()
    assert summary_box['x'] >= chart_box['x'] + chart_box['width']
    control = allocation.locator('.st-key-strategic_allocation_measure')
    control_box = control.bounding_box() if control.count() else summary_box
    assert abs(control_box['x'] - summary_box['x']) < 2
    assert abs(control_box['y'] - chart_box['y']) < 4
    page.get_by_role('tab', name='Positions', exact=True).click()
    playwright.expect(page.get_by_role('table', name='Positions', exact=True)).to_be_visible()
    page.get_by_role('tab', name='Overview', exact=True).click()
    playwright.expect(page.get_by_role('combobox', name='Category', exact=True)).to_be_visible()
    initial_colors = page.locator('.js-plotly-plot').first.evaluate('el => Object.fromEntries(el.data[0].ids.map((id,i) => [id,el.data[0].marker.colors[i]]))')
    click_slice(page, 'ETF core')
    page.wait_for_function("document.querySelector('.js-plotly-plot')?.data?.[0]?.labels?.[0] === 'ETF core'")
    chart = page.locator('.js-plotly-plot').first
    assert chart.evaluate('el => el._fullData[0].customdata.slice(1).map(r => r[0]).sort()') == [.2, .8]
    colors = chart.evaluate('el => el.data[0].marker.colors.slice(1)')
    assert len(set(colors)) == 2
    selected_colors = chart.evaluate('el => Object.fromEntries(el.data[0].ids.map((id,i) => [id,el.data[0].marker.colors[i]]))')
    assert all(initial_colors[node] == color for node, color in selected_colors.items())
    playwright.expect(page.get_by_role('combobox', name='Category', exact=True)).to_have_value('ETF core')
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
    value_card.get_by_role('button', name='Show gain as percentage').press('Space')
    playwright.expect(value_card.get_by_test_id('stMetricDelta')).to_have_text('—')
    playwright.expect(page.get_by_role('combobox', name='Category', exact=True)).to_have_value('ETF core')
    open_settings(page)
    page.get_by_role('radio', name='€', exact=True).click()
    page.keyboard.press('Escape')
    playwright.expect(value_card.get_by_role('button', name='Show gain as percentage')).to_contain_text('+€30.00')
    click_slice(page, 'ETF core')  # Center returns to the parent rather than zooming independently.
    # Streamlit can retain both Category controls while replacing the scoped
    # layout. Wait atomically for the new chart, value and unique control before
    # asking Playwright to apply a strict single-element assertion.
    page.wait_for_function("""() => {
        const categories = document.querySelectorAll('[role=combobox][aria-label="Category"]');
        return document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'
            && categories.length === 1 && categories[0].value === 'Portfolio'
            && document.querySelector('.js-plotly-plot')?.data?.[0]?.labels?.[0] === 'Portfolio';
    }""")
    playwright.expect(page.get_by_role('combobox', name='Category', exact=True)).to_have_value('Portfolio')
    click_slice(page, 'Portfolio')  # Root center is a safe no-op.
    playwright.expect(page.get_by_role('combobox', name='Category', exact=True)).to_have_value('Portfolio')
    click_slice(page, 'ETF core')
    playwright.expect(page.get_by_role('combobox', name='Category', exact=True)).to_have_value('ETF core')
    # Category is rendered before the view switch and chart. Wait for the
    # scoped rerun to finish so Performance is not clicked during replacement.
    page.wait_for_function("""() => {
        const categories = document.querySelectorAll('[role=combobox][aria-label="Category"]');
        return document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'
            && categories.length === 1 && categories[0].value === 'ETF core'
            && document.querySelector('.js-plotly-plot')?.data?.[0]?.labels?.[0] === 'ETF core';
    }""")
    page.get_by_role('radio', name='Performance', exact=True).click()
    playwright.expect(page.get_by_text('Category comparison', exact=True)).to_be_visible()
    playwright.expect(page.get_by_role('table')).to_have_count(0)
    assert page.locator('.js-plotly-plot').first.evaluate('el => el.data[0].type') == 'bar'
    playwright.expect(page.get_by_role('radio', name='Gain', exact=True)).to_be_checked()
    assert chart.evaluate('el => el.data[0].customdata[0].slice(1,3)') == ['-€10.00', 'Unavailable']
    page.wait_for_function("document.querySelector('.js-plotly-plot')?.layout.xaxis.title.text === 'Gain'")
    assert chart.evaluate('el => Array.from(el._fullData[0].x)') == [-10, 40]
    page.get_by_role('radio', name='Return (%)', exact=True).click()
    playwright.expect(page.get_by_text('TWR unavailable · Choose Gain for supported monetary results.',exact=True)).to_be_visible()
    page.screenshot(path=str(directory / 'performance.png'))
    page.get_by_role('radio', name='Allocation', exact=True).click()
    page.get_by_role('button', name='Back', exact=True).click()
    page.wait_for_function("document.querySelector('.js-plotly-plot')?.data?.[0]?.labels?.[0] === 'Portfolio'")
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
    page.screenshot(path=str(directory / 'overview.png'))
    page.set_viewport_size({'width': 700, 'height': 1000})
    playwright.expect(page.get_by_test_id('stSidebar')).to_have_count(0)
    playwright.expect(allocation.get_by_role('table', name='Allocation', exact=True)).to_be_visible()
    page.wait_for_function("({chart, table}) => table.getBoundingClientRect().top >= chart.getBoundingClientRect().bottom",
                           arg={'chart': allocation.get_by_test_id('stPlotlyChart').element_handle(),
                                'table': allocation.get_by_role('table', name='Allocation', exact=True).element_handle()})
    page.screenshot(path=str(directory / 'overview-narrow.png'))
    assert page.get_by_test_id('stException').count() == 0


def test_position_details_edit_cancel_and_preserved_filter(ux_page):
    page, directory = ux_page
    page.get_by_role('tab', name='Positions', exact=True).click()
    table = page.get_by_role('table', name='Positions', exact=True)
    playwright.expect(table.get_by_role('columnheader', name='Account', exact=True)).to_have_count(0)
    page.get_by_role('searchbox', name='Filter positions').fill('Regional')
    table.locator('tbody tr').first.click()
    dialog = page.get_by_role('dialog')
    playwright.expect(dialog.get_by_role('radio', name='History', exact=True)).to_be_checked()
    playwright.expect(dialog.get_by_text('Synthetic demo price history', exact=True)).to_be_visible()
    value = dialog.get_by_test_id('stMetric').filter(has=page.get_by_text('Position value', exact=True))
    playwright.expect(value).to_contain_text('-€10.00')
    assert_gain_color(value.get_by_test_id('stMetricDelta'), positive=False)
    annualized = dialog.get_by_test_id('stMetric').filter(has=page.get_by_text('Annualized return', exact=True))
    playwright.expect(annualized.get_by_test_id('stMetricValue')).to_have_text('—')
    dialog.get_by_role('radio', name='Details', exact=True).click()
    playwright.expect(dialog.get_by_test_id('stDataFrame').first).to_be_visible()
    page.screenshot(path=str(directory / 'details.png'))
    dialog.get_by_role('button', name='Edit position').click()
    quantity = dialog.get_by_role('textbox', name='Quantity held (total)', exact=True)
    playwright.expect(quantity).to_have_value('1')
    quantity.fill('3'); quantity.press('Tab')
    dialog.get_by_role('button', name='Save position', exact=True).click()
    playwright.expect(dialog).to_have_count(0)
    playwright.expect(page.get_by_role('searchbox', name='Filter positions')).to_have_value('Regional')
    playwright.expect(table.locator('tbody tr')).to_have_count(1)
    # Save closes the dialog before the list receives its new revision.
    playwright.expect(table.locator('tbody tr td').nth(2)).to_have_text('3')
    playwright.expect(page.get_by_test_id('stApp')).to_have_attribute('data-test-script-state', 'notRunning')
    table.get_by_role('button', name='Edit Invented Regional · Second', exact=True).click()
    quantity = page.get_by_role('dialog').get_by_role('textbox', name='Quantity held (total)', exact=True)
    playwright.expect(quantity).to_have_value('3')
    quantity.fill('99'); quantity.press('Tab')
    page.get_by_role('dialog').get_by_role('button', name='Cancel', exact=True).click()
    playwright.expect(page.get_by_role('dialog')).to_have_count(0)
    from portfolio_app.positions import read_snapshot
    assert read_snapshot(directory / 'holdings.csv').holdings.shares.tolist() == [2., 3., 1.]
    page.set_viewport_size({'width': 700, 'height': 900})
    playwright.expect(page.get_by_test_id('stSidebar')).to_have_count(0)
    page.screenshot(path=str(directory / 'positions-narrow.png'))


def test_overview_list_matches_positions_and_opens_sorted_filtered_rows(ux_page):
    page, directory = ux_page
    click_slice(page, 'ETF core')
    playwright.expect(page.get_by_role('combobox', name='Category', exact=True)).to_have_value('ETF core')
    table = page.get_by_role('table', name='Positions', exact=True)
    # The category control updates before Streamlit replaces the portfolio list.
    # Wait for the category-specific list before sorting or typing into it.
    playwright.expect(table.get_by_role('columnheader', name='% of ETF core', exact=True)).to_have_count(1)
    playwright.expect(table.locator('tbody tr')).to_have_count(2)
    playwright.expect(page.get_by_test_id('stApp')).to_have_attribute('data-test-script-state', 'notRunning')
    playwright.expect(table.get_by_role('columnheader', name='Return (%)')).to_have_count(1)
    playwright.expect(table.get_by_role('columnheader', name='Gain')).to_have_count(1)
    playwright.expect(table.get_by_role('checkbox')).to_have_count(0)
    playwright.expect(table.get_by_role('radio')).to_have_count(0)
    table.get_by_role('button', name='Return (%)', exact=True).click()
    playwright.expect(table.locator('tbody tr').first).to_contain_text('Invented Regional')
    page.get_by_role('searchbox', name='Filter positions').fill('Regional')
    row = table.locator('tbody tr')
    playwright.expect(row).to_have_count(1)
    playwright.expect(row).to_contain_text('20.00')  # Category allocation, not portfolio allocation.
    playwright.expect(row.locator('td.negative')).to_have_count(2)
    row.press('Enter')
    dialog = page.get_by_role('dialog')
    playwright.expect(dialog).to_contain_text('Invented Regional')
    dialog.get_by_role('button', name='Close', exact=True).filter(has_text='Close').click()
    playwright.expect(dialog).to_have_count(0)
    row.get_by_role('button', name='Edit Invented Regional · Second', exact=True).click()
    quantity = dialog.get_by_role('textbox', name='Quantity held (total)', exact=True)
    playwright.expect(quantity).to_have_value('1')
    quantity.fill('2')
    quantity.press('Tab')
    dialog.get_by_role('button', name='Save position', exact=True).click()
    playwright.expect(dialog).to_have_count(0)
    playwright.expect(page.get_by_role('searchbox', name='Filter positions')).to_have_value('Regional')
    playwright.expect(row).to_contain_text('33.33')
    # Opening the same position again needs no checkbox deselection.
    row.click()
    playwright.expect(dialog).to_contain_text('Invented Regional')
    dialog.get_by_role('button', name='Close', exact=True).filter(has_text='Close').click()
    playwright.expect(dialog).to_have_count(0)
    # Closing the dialog starts an app rerun; finish it before switching lists.
    select_tab(page, 'Positions')
    playwright.expect(page.get_by_role('searchbox', name='Filter positions')).to_have_value('')
    select_tab(page, 'Overview')
    playwright.expect(page.get_by_role('searchbox', name='Filter positions')).to_have_value('Regional')
    table.scroll_into_view_if_needed()
    page.wait_for_function("table => table.getBoundingClientRect().width > 300",
                           arg=page.get_by_role('table', name='Allocation', exact=True).element_handle())
    page.screenshot(path=str(directory / 'overview-positions.png'))
    assert page.get_by_test_id('stException').count() == 0


def test_dismissed_draft_resumes_and_stale_edit_cannot_overwrite(ux_page):
    page, directory = ux_page
    page.get_by_role('tab', name='Positions', exact=True).click()
    table = page.get_by_role('table', name='Positions', exact=True)
    table.get_by_role('button', name='Edit Invented Global · First', exact=True).click()
    dialog = page.get_by_role('dialog')
    quantity = dialog.get_by_role('textbox', name='Quantity held (total)', exact=True)
    quantity.fill('7'); quantity.press('Tab')
    playwright.expect(quantity).to_have_value('7')
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
    page.keyboard.press('Escape')
    playwright.expect(dialog).to_have_count(0)
    page.screenshot(path=str(directory / 'dismissed.png'))
    page.get_by_role('button', name='Resume unsaved edit', exact=True).click()
    quantity = page.get_by_role('dialog').get_by_role('textbox', name='Quantity held (total)', exact=True)
    playwright.expect(quantity).to_have_value('7')
    path = directory / 'holdings.csv'
    changed = path.read_text().replace('SYNTH-A,2,80', 'SYNTH-A,4,80')
    path.write_text(changed)
    page.get_by_role('dialog').get_by_role('button', name='Save position', exact=True).click()
    playwright.expect(page.get_by_text('Holdings changed while this form was open.', exact=False)).to_be_visible()
    assert path.read_text() == changed
    page.get_by_role('button', name='Reload position form', exact=True).click()
    quantity = page.get_by_role('dialog').get_by_role('textbox', name='Quantity held (total)', exact=True)
    playwright.expect(quantity).to_have_value('4')
    # Reload replaces the warning's Cancel button after the new fields arrive.
    playwright.expect(page.get_by_role('button', name='Reload position form', exact=True)).to_have_count(0)
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
    page.get_by_role('dialog').get_by_role('button', name='Cancel', exact=True).click()
    playwright.expect(page.get_by_role('dialog')).to_have_count(0)


def test_nested_categories_refresh_holding_click_and_dark_mode(ux_page):
    page, directory = ux_page
    path = directory / 'allocation.yaml'
    config = yaml.safe_load(path.read_text())
    config['buckets'][0]['parent'] = 'long'
    config['buckets'][0]['target'] = 1.
    config['buckets'].append(dict(id='long', name='Long term', target=.7))
    path.write_text(yaml.safe_dump(config))
    page.reload()
    page.get_by_role('combobox', name='Category', exact=True).wait_for()
    click_slice(page, 'Long term')
    playwright.expect(page.get_by_role('combobox', name='Category', exact=True)).to_have_value('Long term')
    click_slice(page, 'ETF core')
    playwright.expect(page.get_by_role('combobox', name='Category', exact=True)).to_have_value('Long term › ETF core')
    click_slice(page, 'Invented Global')
    playwright.expect(page.get_by_role('dialog')).to_be_visible()
    page.get_by_role('dialog').get_by_role('button', name='Close', exact=True).filter(has_text='Close').click()
    playwright.expect(page.get_by_role('dialog')).to_have_count(0)
    click_slice(page, 'ETF core')
    playwright.expect(page.get_by_role('combobox', name='Category', exact=True)).to_have_value('Long term')
    page.get_by_role('button', name='Main menu', exact=True).click()
    page.get_by_test_id('stMainMenuItem-theme-Dark').click()
    page.keyboard.press('Escape')
    page.screenshot(path=str(directory / 'overview-dark.png'))
    assert page.get_by_test_id('stException').count() == 0


def test_tiny_labels_reappear_when_scoped_and_losses_are_red(ux_page):
    page, directory = ux_page
    path = directory / 'demo_prices.json'
    prices = json.loads(path.read_text())
    prices['prices']['SYNTH-C']['price'] = 1
    path.write_text(json.dumps(prices))
    page.reload()
    page.wait_for_function("document.querySelector('.js-plotly-plot')?.data?.[0].labels.includes('Invented Satellite')")
    chart = page.locator('.js-plotly-plot').first
    assert chart.evaluate("el => el.data[0].text[el.data[0].labels.indexOf('Invented Satellite')]") == ''
    assert chart.evaluate("el => el._fullData[0].values[el.data[0].labels.indexOf('Invented Satellite')]") == 1
    # Choose the tiny category with the dropdown; its own view has a 100% holding.
    page.get_by_role('combobox', name='Category', exact=True).click()
    page.get_by_role('option', name='Satellites', exact=True).click()
    page.wait_for_function("document.querySelector('.js-plotly-plot')?.data?.[0]?.labels?.[0] === 'Satellites'")
    assert chart.evaluate("el => el.data[0].text[el.data[0].labels.indexOf('Invented Satellite')]")
    card = page.locator('.st-key-overview_value').get_by_test_id('stMetric')
    playwright.expect(card.get_by_test_id('stMetricDelta')).to_contain_text('-€49.00')
    assert_gain_color(card.get_by_test_id('stMetricDelta'), positive=False)
    open_settings(page)
    page.get_by_role('radio', name='%', exact=True).click()
    page.keyboard.press('Escape')
    playwright.expect(card.get_by_test_id('stMetricDelta')).to_have_text('—')


def test_transaction_review_defaults_to_first_purchase_and_applies_once(ux_page):
    page, directory = ux_page
    from portfolio_app.account_journal import load_journal
    from portfolio_app.security_history import effective_holdings
    from portfolio_app.positions import read_snapshot
    select_tab(page, 'Positions')
    page.get_by_role('table', name='Positions', exact=True).locator('tr[data-position-id="fund-a"]').click()
    dialog = page.get_by_role('dialog')
    dialog.get_by_text('Transactions', exact=True).click()
    dialog.get_by_role('button', name='Add buy').click()
    dialog.get_by_role('textbox', name='Quantity', exact=True).fill('2')
    dialog.get_by_role('textbox', name='Unit price', exact=True).fill('80')
    dialog.get_by_role('textbox', name='Fees', exact=True).fill('0')
    assert not dialog.get_by_text('Charges complete (blank means no charge)', exact=True).count()
    dialog.get_by_role('button', name='Save transaction', exact=True).click()
    dialog.get_by_role('button', name='Review against portfolio', exact=True).click()
    apply = dialog.get_by_role('button', name='Apply history to portfolio', exact=True)
    playwright.expect(apply).to_be_enabled()
    playwright.expect(dialog.get_by_text('Shares and acquisition cost match.', exact=True)).to_be_visible()
    assert not dialog.get_by_role('checkbox').count()
    assert not dialog.get_by_role('combobox', name='Included transactions', exact=True).is_visible()
    assert not load_journal(directory)[0].securities[0].active
    apply.click()
    dialog.get_by_role('button', name='Review against portfolio', exact=True).wait_for()
    journal, _ = load_journal(directory)
    row = effective_holdings(read_snapshot(directory/'holdings.csv').holdings, journal.securities).set_index('position_id').loc['fund-a']
    assert row.shares == 2 and row.acquisition_price == 80
    dialog.get_by_role('table', name='Transactions', exact=True).get_by_role('button', name=re.compile('^Edit ')).first.click()
    expect_price = dialog.get_by_role('textbox', name='Unit price', exact=True)
    playwright.expect(expect_price).to_have_value('80')
    expect_price.fill('81.125')
    dialog.get_by_role('button', name='Save changes', exact=True).click()
    dialog.get_by_role('button', name='Review against portfolio', exact=True).wait_for()
    journal, _ = load_journal(directory)
    assert len(journal.securities[0].records) == 3  # Original buy, internal boundary and retained correction.
    current = effective_holdings(read_snapshot(directory/'holdings.csv').holdings, journal.securities).set_index('position_id').loc['fund-a']
    assert current.acquisition_price == 80
    dialog.get_by_role('button', name='Review against portfolio', exact=True).click()
    dialog.get_by_text('Review options', exact=True).click()
    # Only the latest transaction belongs in the picker, not its retained audit
    # version or the internal zero-share boundary.
    playwright.expect(dialog.get_by_test_id('stMultiSelect').get_by_role('button', name=re.compile('^Remove '))).to_have_count(1)
    dialog.get_by_role('button', name='Apply history to portfolio', exact=True).click()
    dialog.get_by_role('button', name='Review against portfolio', exact=True).wait_for()
    journal, _ = load_journal(directory)
    current = effective_holdings(read_snapshot(directory/'holdings.csv').holdings, journal.securities).set_index('position_id').loc['fund-a']
    assert current.shares == 2 and current.acquisition_price == 81.125
    assert not page.get_by_test_id('stException').count()


def test_personal_history_is_visible_with_periods_ending_today(ux_page):
    from dataclasses import replace
    from datetime import date, timedelta
    from decimal import Decimal as D
    from portfolio_app.account_journal import Journal, save_journal
    from portfolio_app.history_commands import book_for
    from portfolio_app.history_observations import HistoryScope, Observation, ObservationStore, CoverageReview, save_observations
    from portfolio_app.positions import read_snapshot
    from portfolio_app.security_history import SecurityEvent, history_fingerprint
    page,directory=ux_page
    today=date.today();start=today-timedelta(days=365)
    rows=read_snapshot(directory/'holdings.csv').holdings
    books=[];prices=[]
    for row in rows.itertuples():
        event=SecurityEvent(row.position_id+'-open','opening',start.isoformat(),quantity=D(str(row.shares)),amount=D(str(row.shares*row.acquisition_price)),currency='EUR')
        book=replace(book_for(Journal(),rows.set_index('position_id',drop=False).loc[row.position_id]),records=(event,),active_ids=(event.id,))
        books.append(book)
        for offset in range(366):
            if row.position_id == 'fund-a' and offset == 350:
                continue  # Explicit missing synthetic price; no hover carry-forward.
            price=D(20) if row.position_id == 'fund-a' and offset == 349 else D(80)+D(offset)/20
            prices.append(Observation(row.position_id+str(offset),'price',row.position_id,(start+timedelta(days=offset)).isoformat(),price,'EUR'))
    journal=Journal(version=2,securities=tuple(books));save_journal(directory,journal,None)
    scopes=[HistoryScope(tuple(b.position_id for b in books)),*[HistoryScope((b.position_id,)) for b in books]]
    reviews=tuple(CoverageReview(str(i),scope,start.isoformat(),today.isoformat(),history_fingerprint(journal)) for i,scope in enumerate(scopes))
    save_observations(directory,ObservationStore(tuple(prices),reviews),None)
    page.reload()
    page.get_by_text('Performance',exact=True).click()
    playwright.expect(page.get_by_text('Portfolio value',exact=True)).to_be_visible()
    metric=page.get_by_test_id('stMetric').filter(has_text='Annualized return')
    playwright.expect(metric).to_be_visible()
    assert not page.get_by_text('Show portfolio history',exact=True).count()
    assert not page.get_by_label('History through',exact=True).count()
    full_return=metric.get_by_test_id('stMetricValue').inner_text()
    page.get_by_text('1M',exact=True).click()
    from browser_support import settle
    from portfolio_app.history_view import period_start
    settle(page)
    playwright.expect(metric.get_by_test_id('stMetricValue')).to_have_text(full_return)
    chart=page.get_by_test_id('stPlotlyChart').first.locator('.js-plotly-plot')
    # The old chart ends today too; wait for the selected range before inspecting
    # Plotly data, which is briefly cleared during the replacement.
    page.wait_for_function('([first,last])=>{const p=document.querySelector(".js-plotly-plot");return p?.layout?.xaxis?.range?.[0]===first && p?.layout?.xaxis?.range?.[1]===last && p?.data?.[1]?.name==="Invested capital"}',
        arg=[period_start('1M',today,start).isoformat(),today.isoformat()])
    assert chart.evaluate('el=>el.layout.xaxis.range[1]')==today.isoformat()
    assert not page.get_by_role('button',name='Scope',exact=True).count()
    assert chart.evaluate('el=>el.data[1].name') == 'Invested capital'
    assert chart.evaluate('el=>el.data[1].line.dash') == 'dash'
    assert chart.evaluate('el=>el.data.every(t=>t.connectgaps)')
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
    chart.scroll_into_view_if_needed()
    history_value=page.get_by_test_id('stMetric').filter(has_text='Portfolio value')
    history_capital=page.get_by_test_id('stMetric').filter(has_text='Invested capital')
    assert page.get_by_test_id('stMetric').count() == 3
    assert not page.get_by_role('switch', name='Compare categories').count()
    assert not page.get_by_role('table').count()
    playwright.expect(page.get_by_text('Category comparison',exact=True)).to_be_visible()
    assert page.locator('.js-plotly-plot').nth(1).evaluate('el=>el.data[0].type') == 'bar'
    summary_gain=history_value.get_by_test_id('stMetricDelta').inner_text()
    assert '€' in summary_gain and 'TWR' not in summary_gain
    playwright.expect(history_value.get_by_test_id('stMetricDelta')).to_have_attribute('title','Gain · 1M · Click for TWR')
    summary_value=history_value.get_by_test_id('stMetricValue').inner_text()
    summary_capital=history_capital.get_by_test_id('stMetricValue').inner_text()
    summary_return=metric.get_by_test_id('stMetricValue').inner_text()
    pointer=chart.evaluate('el=>{const b=el.getBoundingClientRect(),l=el._fullLayout;return {x:b.x+l.xaxis._offset+l.xaxis._length*.5,y:b.y+l.yaxis._offset+10}}')
    page.mouse.move(pointer['x'],pointer['y'])
    playwright.expect(chart.locator('.hoverlayer')).to_contain_text('Invested capital:')
    playwright.expect(chart.locator('.hoverlayer')).to_contain_text('€')
    playwright.expect(chart.locator('.portfolio-hover-point')).to_have_count(2)
    assert chart.locator('.spikeline').count() >= 1
    playwright.expect(history_value.get_by_test_id('stMetricValue')).not_to_have_text(summary_value)
    playwright.expect(history_value.get_by_test_id('stMetricLabel')).to_contain_text(re.compile(r'Portfolio value · \d{2} \w+ \d{4}'))
    playwright.expect(history_capital.get_by_test_id('stMetricLabel')).to_contain_text(re.compile(r'Invested capital · \d{2} \w+ \d{4}'))
    playwright.expect(metric.get_by_test_id('stMetricValue')).to_have_text(summary_return)
    playwright.expect(history_value.get_by_test_id('stMetricDelta')).to_have_attribute('title',re.compile(r'Gain · All history.* · Click for TWR'))
    playwright.expect(history_value.get_by_test_id('stMetricValue')).to_contain_text('€')
    pointer=chart.evaluate('(el,stamp)=>{const b=el.getBoundingClientRect(),l=el._fullLayout;return {x:b.x+l.xaxis._offset+l.xaxis.d2p(stamp),y:b.y+l.yaxis._offset+10}}', (start+timedelta(days=349)).isoformat())
    page.mouse.move(pointer['x'],pointer['y'])
    playwright.expect(history_value.get_by_test_id('stMetricDelta')).to_contain_text('-€35.10')
    assert_gain_color(history_value.get_by_test_id('stMetricDelta'),positive=False)
    pointer=chart.evaluate('(el,stamp)=>{const b=el.getBoundingClientRect(),l=el._fullLayout;return {x:b.x+l.xaxis._offset+l.xaxis.d2p(stamp),y:b.y+l.yaxis._offset+10}}', (start+timedelta(days=350)).isoformat())
    page.mouse.move(pointer['x'],pointer['y'])
    playwright.expect(chart.locator('.hoverlayer')).to_contain_text(re.compile(r'Value: €[\d,.]+ \(\d{2} \w+ \d{4}\)'))
    playwright.expect(page.get_by_text('Some valuations missing · lines connect available values',exact=True)).to_be_visible()
    assert not page.get_by_text('1 date with a missing value · chart gap',exact=True).count()
    page.mouse.move(5,5)
    playwright.expect(history_value.get_by_test_id('stMetricValue')).to_have_text(summary_value)
    playwright.expect(history_capital.get_by_test_id('stMetricValue')).to_have_text(summary_capital)
    playwright.expect(history_value.get_by_test_id('stMetricLabel')).to_have_text('Portfolio value')
    playwright.expect(history_value.get_by_test_id('stMetricDelta')).to_have_text(summary_gain)
    assert_gain_color(history_value.get_by_test_id('stMetricDelta'),positive=True)
    # One selected gain unit, with the period only in its tooltip. The same unit
    # survives hover, period changes and the roundtrip through Allocation.
    badge=history_value.get_by_test_id('stMetricDelta')
    badge.click()
    playwright.expect(badge).to_have_text(re.compile(r'[↑↓] \d[\d,.]*%'))
    playwright.expect(history_value.get_by_test_id('stMetricDelta')).to_have_attribute('title','TWR · 1M · Click for amount')
    period_twr=badge.inner_text()
    pointer=chart.evaluate('(el,stamp)=>{const b=el.getBoundingClientRect(),l=el._fullLayout;return {x:b.x+l.xaxis._offset+l.xaxis.d2p(stamp),y:b.y+l.yaxis._offset+10}}', (start+timedelta(days=349)).isoformat())
    page.mouse.move(pointer['x'],pointer['y'])
    playwright.expect(history_value.get_by_test_id('stMetricDelta')).to_have_attribute('title',re.compile(r'TWR · All history to .* · Click for amount'))
    playwright.expect(badge).to_have_text('↓ 26.59%')  # 234.90 / 320.00 - 1; snapshot cost is irrelevant to TWR.
    assert_gain_color(badge,positive=False)
    playwright.expect(metric.get_by_test_id('stMetricValue')).to_have_text(full_return)
    page.mouse.move(5,5)
    playwright.expect(badge).to_have_text(period_twr)
    playwright.expect(history_value.get_by_test_id('stMetricDelta')).to_have_attribute('title','TWR · 1M · Click for amount')
    page.get_by_role('radio',name='Allocation',exact=True).click()
    allocation_delta=page.locator('.st-key-overview_value').get_by_test_id('stMetricDelta')
    playwright.expect(allocation_delta).to_have_text(re.compile(r'[↑↓] \d[\d,.]*%'))
    page.get_by_role('radio',name='Performance',exact=True).click()
    playwright.expect(badge).to_have_text(period_twr)
    badge.press('Enter')
    playwright.expect(badge).to_have_text(summary_gain)
    page.get_by_text('All',exact=True).click()
    playwright.expect(history_value.get_by_test_id('stMetricDelta')).to_have_attribute('title',re.compile(r'Gain · All history.* · Click for TWR'))
    playwright.expect(history_value.get_by_test_id('stMetricDelta')).to_contain_text('+€60.00')
    playwright.expect(metric.get_by_test_id('stMetricValue')).to_have_text(full_return)
    history_color=history_value.get_by_test_id('stMetricDelta').evaluate('el=>getComputedStyle(el).color')
    page.get_by_role('radio',name='Allocation',exact=True).click()
    allocation_delta=page.locator('.st-key-overview_value').get_by_test_id('stMetricDelta')
    playwright.expect(allocation_delta).to_be_visible()
    playwright.expect(allocation_delta).to_contain_text('+€60.00')
    assert allocation_delta.evaluate('el=>getComputedStyle(el).color') == history_color
    page.get_by_role('radio',name='Performance',exact=True).click()
    page.get_by_label('Category',exact=True).click()
    page.get_by_role('option',name='ETF core',exact=True).click()
    playwright.expect(page.get_by_text('Category value',exact=True)).to_be_visible()
    page.wait_for_function('document.querySelector(".js-plotly-plot")?.data?.[0]?.y?.at(-1)===294.75')
    select_tab(page,'Positions')
    page.get_by_role('table',name='Positions',exact=True).locator('tr[data-position-id="fund-a"]').click()
    dialog=page.get_by_role('dialog')
    dialog.get_by_test_id('stPlotlyChart').wait_for()
    playwright.expect(dialog.get_by_test_id('stMetric').filter(has_text='Annualized return')).to_be_visible()
    assert not dialog.get_by_text('Personal performance',exact=True).count()
    assert not dialog.get_by_label('History from',exact=True).count()
    dialog.get_by_text('6M',exact=True).click()
    dialog.get_by_test_id('stPlotlyChart').wait_for()
    page.wait_for_function('(stamp)=>document.querySelector("[role=dialog] .js-plotly-plot")?.layout?.xaxis?.range?.[0]===stamp',arg=period_start('6M',today,start).isoformat())
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
    position_headlines=dialog.get_by_test_id('stMetric').all_text_contents()
    curve=dialog.locator('.js-plotly-plot')
    curve.scroll_into_view_if_needed()
    pointer=curve.evaluate('el=>{const b=el.getBoundingClientRect(),l=el._fullLayout;return {x:b.x+l.xaxis._offset+l.xaxis._length*.4,y:b.y+l.yaxis._offset+10}}')
    page.mouse.move(pointer['x'],pointer['y'])
    playwright.expect(curve.locator('.hoverlayer')).to_contain_text('Value:')
    playwright.expect(curve.locator('.portfolio-hover-point')).to_have_count(2)
    playwright.expect(curve.locator('.hoverlayer')).to_contain_text('Total gain:')
    playwright.expect(curve.locator('.hoverlayer')).to_contain_text('TWR:')
    assert dialog.get_by_test_id('stMetric').all_text_contents() == position_headlines
    page.mouse.move(5,5)
    dialog.get_by_role('radio', name='Price', exact=True).click()
    assert dialog.get_by_test_id('stMetric').all_text_contents() == position_headlines
    assert not page.get_by_test_id('stException').count()
