"""Exposure workflows against synthetic portfolios; no live provider requests."""
import json
import yaml
import pytest

from test_ux_browser import ux_page as ux_page, playwright


@pytest.fixture
def exposure_page(ux_page):
    page, directory = ux_page
    funds = directory / 'etfs'
    funds.mkdir()
    (funds / 'invented.csv').write_text('constituent_id,name,ticker,isin,weight\nc,Invented Satellite,SYNTH-C,,0.5\n')
    (funds / 'invented.yaml').write_text(yaml.safe_dump(dict(
        fund_id='invented', name='Invented Global UCITS ETF', isin='ZZ9999999999', tickers=['SYNTH-A'],
        as_of='2026-09-01', source='https://example.invalid', holdings_file='invented.csv')))
    (directory / 'classifications.yaml').write_text('c:\n  classifications:\n    labels: [[Theme, Child]]\n')
    page.reload()
    page.get_by_role('tab', name='Exposure', exact=True).click()
    page.get_by_role('table', name='Exposure assets', exact=True).wait_for()
    return page, directory


def test_assets_first_filters_breakdown_and_source_dialog(exposure_page):
    page, directory = exposure_page
    results = page.locator('.st-key-exposure_results')
    playwright.expect(results.locator('.js-plotly-plot')).to_have_count(0)
    playwright.expect(page.get_by_text('ETF refresh & snapshots', exact=True)).not_to_be_visible()
    table = results.get_by_role('table', name='Exposure assets', exact=True)
    assert table.bounding_box()['y'] < 650
    page.screenshot(path=str(directory / 'exposure.png'))
    table.get_by_role('row').filter(has_text='Invented Satellite').click()
    dialog = page.get_by_role('dialog')
    playwright.expect(dialog.get_by_role('heading', name='Invented Satellite', exact=True)).to_be_visible()
    playwright.expect(dialog.get_by_text('Total exposure', exact=True)).to_be_visible()
    page.screenshot(path=str(directory / 'exposure-detail.png'))
    dialog.get_by_role('button', name='Close exposure details').click()
    playwright.expect(dialog).to_have_count(0)
    page.get_by_role('textbox', name='Search exposure').fill('Regional')
    page.get_by_role('textbox', name='Search exposure').press('Enter')
    playwright.expect(page.get_by_text('1 matching assets', exact=False)).to_be_visible()
    page.get_by_role('button', name='Filters', exact=True).click()
    page.get_by_role('button', name='Clear filters', exact=True).click()
    page.keyboard.press('Escape')
    playwright.expect(page.get_by_role('textbox', name='Search exposure')).to_have_value('')
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
    page.get_by_role('combobox', name='Source scope').click()
    page.get_by_role('option', name='ETF core', exact=True).click()
    playwright.expect(page.locator('.st-key-exposure_summary')).to_contain_text('€250.00')
    page.get_by_text('Break down ETFs', exact=True).click()
    playwright.expect(page.get_by_role('switch', name='Break down ETFs', exact=True)).not_to_be_checked()
    playwright.expect(results.locator('.js-plotly-plot')).to_have_count(0)
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


def test_themes_include_unclassified_and_chart_navigation_updates_table(exposure_page):
    page, directory = exposure_page
    page.get_by_role('radio', name='Themes & sectors', exact=True).click()
    results = page.locator('.st-key-exposure_results')
    chart = results.locator('.js-plotly-plot')
    chart.wait_for()
    assert 'Unclassified' in chart.evaluate('el => el.data[0].labels')
    label = chart.locator('text.slicetext').filter(has_text='Theme').first
    label.scroll_into_view_if_needed()
    box = label.bounding_box()
    page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
    playwright.expect(page.get_by_role('combobox', name='Detail view', exact=True)).to_have_value('Theme')
    assert chart.evaluate('el => el.data[0].labels[0]') == 'Theme'
    label = chart.locator('text.slicetext').filter(has_text='Theme').first
    label.scroll_into_view_if_needed()
    box = label.bounding_box()
    page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
    playwright.expect(page.get_by_role('combobox', name='Detail view', exact=True)).to_have_value('All selected labels')
    page.get_by_role('combobox', name='Group by', exact=True).click()
    page.get_by_role('option', name='Labels', exact=True).click()
    results.locator('[class*="st-key-exposure_theme_chart_"] .js-plotly-plot').wait_for()
    label = chart.locator('text.slicetext').filter(has_text='Theme').first
    label.scroll_into_view_if_needed()
    box = label.bounding_box()
    page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
    playwright.expect(page.get_by_role('combobox', name='Hierarchy root', exact=True)).to_have_value('Theme')
    page.screenshot(path=str(directory / 'exposure-themes.png'))
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


def test_exposure_lists_expand_without_breakdown_and_bound_long_breakdowns(exposure_page):
    page, directory = exposure_page
    with (directory / 'holdings.csv').open('a') as output:
        for index in range(40):
            output.write(f'extra-{index},extra-{index},Invented Asset {index},SYNTH-EXTRA-{index},1,50,EUR,First,active,0\n')
    prices = json.loads((directory / 'demo_prices.json').read_text())
    prices['prices'].update({f'SYNTH-EXTRA-{index}': dict(price=80, currency='EUR', observed_at='2026-09-01T12:00:00+00:00')
                             for index in range(40)})
    (directory / 'demo_prices.json').write_text(json.dumps(prices))
    page.reload()
    page.get_by_role('tab', name='Exposure', exact=True).click()
    table = page.get_by_role('table', name='Exposure assets', exact=True)
    playwright.expect(table.locator('tbody tr')).to_have_count(43)
    assert table.evaluate('el => el.parentElement.scrollHeight > el.parentElement.clientHeight')
    assert table.evaluate('el => el.parentElement.clientHeight') <= 620
    playwright.expect(table.get_by_role('checkbox')).to_have_count(0)
    playwright.expect(table.get_by_role('radio')).to_have_count(0)
    page.get_by_text('Break down ETFs', exact=True).click()
    playwright.expect(page.get_by_role('switch', name='Break down ETFs')).not_to_be_checked()
    playwright.expect(table.locator('..')).to_have_css('max-height', 'none')
    assert table.evaluate('el => el.parentElement.clientHeight') > 620
    assert table.evaluate('el => Math.abs(el.parentElement.scrollHeight - el.parentElement.clientHeight)') <= 1
    table.locator('tbody tr').last.scroll_into_view_if_needed()
    playwright.expect(table.locator('tbody tr').last).to_be_in_viewport()
    assert page.get_by_test_id('stMain').evaluate('el => el.scrollTop') > 500
    page.set_viewport_size({'width': 700, 'height': 1000})
    assert table.evaluate('el => Math.abs(el.parentElement.scrollHeight - el.parentElement.clientHeight)') <= 1
    page.screenshot(path=str(directory / 'exposure-long-narrow.png'))


def test_sources_show_direct_and_each_etf_with_asset_relative_percentages(exposure_page):
    page, directory = exposure_page
    (directory / 'etfs/regional.csv').write_text('constituent_id,name,ticker,isin,weight\nc,Invented Satellite,SYNTH-C,,0.5\n')
    (directory / 'etfs/regional.yaml').write_text(yaml.safe_dump(dict(
        fund_id='regional', name='Invented Regional ETF', isin='ZZ8888888888', tickers=['SYNTH-B'],
        as_of='2026-09-01', source='https://example.invalid', holdings_file='regional.csv')))
    page.reload()
    page.get_by_role('tab', name='Exposure', exact=True).click()
    table = page.get_by_role('table', name='Exposure assets', exact=True)
    row = table.get_by_role('row').filter(has_text='Invented Satellite')
    playwright.expect(row).to_contain_text('3 positions')
    playwright.expect(table.get_by_role('columnheader', name='Direct (EUR)', exact=True)).to_have_count(0)
    row.press('Enter')
    dialog = page.get_by_role('dialog')
    sources = dialog.get_by_role('table', name='Exposure sources', exact=True)
    playwright.expect(sources.locator('tbody tr')).to_have_count(3)
    for name, amount, percent in [('Invented Global', '100.00', '48.78'),
                                  ('Invented Satellite', '80.00', '39.02'),
                                  ('Invented Regional', '25.00', '12.20')]:
        source = sources.get_by_role('row').filter(has_text=name)
        playwright.expect(source).to_contain_text(amount)
        playwright.expect(source).to_contain_text(percent)
    page.screenshot(path=str(directory / 'exposure-source-contributions.png'))
    dialog.get_by_text('ETF breakdown: Invented Global', exact=True).click()
    playwright.expect(dialog.get_by_role('table', name='Invented Global holdings', exact=True)).to_be_visible()
    dialog.get_by_role('button', name='Close exposure details').click()
    row.click()  # The same asset can reopen without deselecting a control.
    playwright.expect(sources).to_be_visible()
