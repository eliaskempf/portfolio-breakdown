"""Exposure workflows against synthetic portfolios; no live provider requests."""
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
    page.locator('.st-key-exposure_results').get_by_test_id('stDataFrame').wait_for()
    return page, directory


def test_assets_first_filters_breakdown_and_source_dialog(exposure_page):
    page, directory = exposure_page
    results = page.locator('.st-key-exposure_results')
    playwright.expect(results.locator('.js-plotly-plot')).to_have_count(0)
    playwright.expect(page.get_by_text('ETF refresh & snapshots', exact=True)).not_to_be_visible()
    table = results.get_by_test_id('stDataFrame')
    assert table.bounding_box()['y'] < 650
    page.screenshot(path=str(directory / 'exposure.png'))
    canvas = table.locator('canvas').first
    box = canvas.bounding_box()
    page.mouse.click(box['x'] + 15, box['y'] + 50)
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
