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
        '--server.fileWatcherType=none', '--browser.gatherUsageStats=false'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=1); break
            except OSError:
                time.sleep(.1)
        with playwright.sync_playwright() as runner:
            browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
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


def assert_gain_color(locator, *, positive):
    color = locator.evaluate('el => getComputedStyle(el).color')
    channels = [int(value) for value in re.findall(r'\d+', color)[:3]]
    assert (channels[1] > channels[0]) if positive else (channels[0] > channels[1])


def test_category_scope_performance_and_tab_roundtrip(ux_page):
    page, directory = ux_page
    value_card = page.get_by_test_id('stMetric').filter(has=page.get_by_text('Current value', exact=True))
    playwright.expect(value_card).to_contain_text('€330.00')
    playwright.expect(value_card.get_by_test_id('stMetricDelta')).to_contain_text('+€60.00')
    assert_gain_color(value_card.get_by_test_id('stMetricDelta'), positive=True)
    playwright.expect(page.locator('.st-key-strategic_crumb_')).to_have_count(0)
    value_card.get_by_role('button', name='Show gain as percentage').click()
    playwright.expect(value_card.get_by_test_id('stMetricDelta')).to_contain_text('+22.22%')
    playwright.expect(page.get_by_role('radio', name='%', exact=True)).to_be_checked()
    value_card.get_by_role('button', name='Show gain in euros').press('Enter')
    playwright.expect(value_card.get_by_test_id('stMetricDelta')).to_contain_text('+€60.00')
    playwright.expect(page.get_by_role('radio', name='€', exact=True)).to_be_checked()
    value_box = value_card.get_by_test_id('stMetricValue').bounding_box()
    gain_box = value_card.get_by_test_id('stMetricDelta').bounding_box()
    assert gain_box['x'] >= value_box['x'] + value_box['width']
    assert abs(gain_box['y'] + gain_box['height']/2 - value_box['y'] - value_box['height']/2) < 12
    allocation = page.locator('.st-key-overview_allocation')
    chart_box = allocation.get_by_test_id('stPlotlyChart').bounding_box()
    summary_box = allocation.locator('.st-key-overview_allocation_summary').bounding_box()
    assert summary_box['x'] >= chart_box['x'] + chart_box['width']
    offset = summary_box['y'] + summary_box['height']/2 - chart_box['y'] - chart_box['height']/2
    assert -40 < offset < -8
    page.get_by_role('tab', name='Positions', exact=True).click()
    page.get_by_role('tab', name='Overview', exact=True).click()
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
    value_card.get_by_role('button', name='Show gain as percentage').press('Space')
    playwright.expect(value_card.get_by_test_id('stMetricDelta')).to_contain_text('+13.64%')
    playwright.expect(page.get_by_role('combobox', name='Category', exact=True)).to_have_value('ETF core')
    page.get_by_role('radio', name='€', exact=True).click()
    playwright.expect(value_card.get_by_role('button', name='Show gain as percentage')).to_contain_text('+€30.00')
    click_slice(page, 'ETF core')  # Center returns to the parent rather than zooming independently.
    playwright.expect(page.get_by_role('combobox', name='Category', exact=True)).to_have_value('Portfolio')
    click_slice(page, 'Portfolio')  # Root center is a safe no-op.
    playwright.expect(page.get_by_role('combobox', name='Category', exact=True)).to_have_value('Portfolio')
    click_slice(page, 'ETF core')
    playwright.expect(page.get_by_role('combobox', name='Category', exact=True)).to_have_value('ETF core')
    page.get_by_role('radio', name='Performance', exact=True).click()
    playwright.expect(page.get_by_text('Cost (EUR)', exact=True)).to_have_count(1)
    assert page.locator('.js-plotly-plot').first.evaluate('el => el.data[0].type') == 'bar'
    playwright.expect(page.get_by_role('radio', name='Return (%)', exact=True)).to_be_checked()
    assert chart.evaluate('el => Array.from(el._fullData[0].x)') == pytest.approx([-100 / 6, 25])
    assert chart.evaluate('el => el.data[0].customdata[0].slice(1,3)') == ['-10.00 EUR', '-16.67%']
    page.get_by_role('radio', name='Gain (EUR)', exact=True).click()
    page.wait_for_function("document.querySelector('.js-plotly-plot')?.layout.xaxis.title.text === 'Gain (EUR)'")
    assert chart.evaluate('el => Array.from(el._fullData[0].x)') == [-10, 40]
    page.get_by_role('radio', name='Return (%)', exact=True).click()
    page.wait_for_function("document.querySelector('.js-plotly-plot')?.layout.xaxis.title.text === 'Return (%)'")
    page.screenshot(path=str(directory / 'performance.png'))
    page.get_by_role('radio', name='Allocation', exact=True).click()
    page.get_by_role('button', name='Back', exact=True).click()
    page.wait_for_function("document.querySelector('.js-plotly-plot')?.data?.[0]?.labels?.[0] === 'Portfolio'")
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
    page.screenshot(path=str(directory / 'overview.png'))
    page.set_viewport_size({'width': 700, 'height': 1000})
    collapse = page.get_by_test_id('stSidebarCollapseButton').get_by_role('button')
    if collapse.is_visible():
        collapse.click()
    page.wait_for_function("document.querySelector('[data-testid=stSidebar]')?.getBoundingClientRect().right <= 0")
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
    playwright.expect(dialog.get_by_text('Market-price history', exact=True)).to_be_visible()
    playwright.expect(dialog.get_by_text('Synthetic demo price history', exact=True)).to_be_visible()
    gain = dialog.get_by_test_id('stMetric').filter(has=page.get_by_text('Unrealized gain', exact=True))
    returns = dialog.get_by_test_id('stMetric').filter(has=page.get_by_text('Return on cost', exact=True))
    playwright.expect(gain).to_contain_text('-10.00 EUR')
    playwright.expect(returns).to_contain_text('-16.67%')
    assert_gain_color(gain.get_by_test_id('stMetricValue'), positive=False)
    assert_gain_color(returns.get_by_test_id('stMetricValue'), positive=False)
    page.screenshot(path=str(directory / 'details.png'))
    dialog.get_by_role('button', name='Edit position').click()
    quantity = dialog.get_by_role('spinbutton', name='Quantity held (total)', exact=True)
    playwright.expect(quantity).to_have_value('1.0000000000')
    quantity.fill('3'); quantity.press('Tab')
    dialog.get_by_role('button', name='Save position', exact=True).click()
    playwright.expect(dialog).to_have_count(0)
    playwright.expect(page.get_by_role('searchbox', name='Filter positions')).to_have_value('Regional')
    playwright.expect(table.locator('tbody tr')).to_have_count(1)
    table.get_by_role('button', name='Edit Invented Regional · Second', exact=True).click()
    quantity = page.get_by_role('dialog').get_by_role('spinbutton', name='Quantity held (total)', exact=True)
    playwright.expect(quantity).to_have_value('3.0000000000')
    quantity.fill('99'); quantity.press('Tab')
    page.get_by_role('dialog').get_by_role('button', name='Cancel', exact=True).click()
    playwright.expect(page.get_by_role('dialog')).to_have_count(0)
    from portfolio_app.positions import read_snapshot
    assert read_snapshot(directory / 'holdings.csv').holdings.shares.tolist() == [2., 3., 1.]
    page.set_viewport_size({'width': 700, 'height': 900})
    collapse = page.get_by_test_id('stSidebarCollapseButton').get_by_role('button')
    if collapse.is_visible():
        collapse.click()
    page.screenshot(path=str(directory / 'positions-narrow.png'))


def test_overview_list_matches_positions_and_opens_sorted_filtered_rows(ux_page):
    page, directory = ux_page
    click_slice(page, 'ETF core')
    playwright.expect(page.get_by_role('combobox', name='Category', exact=True)).to_have_value('ETF core')
    table = page.get_by_role('table', name='Positions', exact=True)
    playwright.expect(table.get_by_role('columnheader', name='Return (%)')).to_have_count(1)
    playwright.expect(table.get_by_role('columnheader', name='Gain (EUR)')).to_have_count(1)
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
    quantity = dialog.get_by_role('spinbutton', name='Quantity held (total)', exact=True)
    playwright.expect(quantity).to_have_value('1.0000000000')
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
    page.get_by_role('tab', name='Positions', exact=True).click()
    playwright.expect(page.get_by_role('searchbox', name='Filter positions')).to_have_value('')
    page.get_by_role('tab', name='Overview', exact=True).click()
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
    quantity = dialog.get_by_role('spinbutton', name='Quantity held (total)', exact=True)
    quantity.fill('7'); quantity.press('Tab')
    playwright.expect(quantity).to_have_value('7.0000000000')
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
    page.keyboard.press('Escape')
    playwright.expect(dialog).to_have_count(0)
    page.screenshot(path=str(directory / 'dismissed.png'))
    page.get_by_role('button', name='Resume unsaved edit', exact=True).click()
    quantity = page.get_by_role('dialog').get_by_role('spinbutton', name='Quantity held (total)', exact=True)
    playwright.expect(quantity).to_have_value('7.0000000000')
    path = directory / 'holdings.csv'
    changed = path.read_text().replace('SYNTH-A,2,80', 'SYNTH-A,4,80')
    path.write_text(changed)
    page.get_by_role('dialog').get_by_role('button', name='Save position', exact=True).click()
    playwright.expect(page.get_by_text('Holdings changed while this form was open.', exact=False)).to_be_visible()
    assert path.read_text() == changed
    page.get_by_role('button', name='Reload position form', exact=True).click()
    quantity = page.get_by_role('dialog').get_by_role('spinbutton', name='Quantity held (total)', exact=True)
    playwright.expect(quantity).to_have_value('4.0000000000')
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
    card = page.get_by_test_id('stMetric').filter(has=page.get_by_text('Current value', exact=True))
    playwright.expect(card.get_by_test_id('stMetricDelta')).to_contain_text('-€49.00')
    assert_gain_color(card.get_by_test_id('stMetricDelta'), positive=False)
    page.get_by_role('radio', name='%', exact=True).click()
    playwright.expect(card.get_by_test_id('stMetricDelta')).to_contain_text('-98.00%')
    assert_gain_color(card.get_by_test_id('stMetricDelta'), positive=False)
