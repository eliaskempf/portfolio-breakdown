"""Rendered geography smoke test with invented, offline portfolio inputs."""
import json

import yaml

from test_ux_browser import ux_page as ux_page, playwright


def test_classification_and_geography_controls_render_and_drill(ux_page):
    page, directory = ux_page
    (directory / 'classifications.yaml').write_text(yaml.safe_dump({
        'a': {'classifications': {'geography': [['UK']]}},
        'c': {'classifications': {'labels': [['Invented theme']], 'sector': [['Invented sector']],
                                   'geography': [['Germany']]}},
        'gold': {'classifications': {'asset_class': [['Commodities', 'Gold']]}},
        'crypto': {'classifications': {'geography': [['Crypto']]}},
    }))
    with (directory / 'holdings.csv').open('a') as handle:
        handle.write('gold,gold,Invented Gold,SYN-GOLD,1,40,EUR,First,active,0\n'
                     'crypto,crypto,Invented Crypto,SYN-CRYPTO,1,40,EUR,First,active,0\n')
    path = directory / 'demo_prices.json'
    prices = json.loads(path.read_text())
    for symbol in ['SYN-GOLD', 'SYN-CRYPTO']:
        prices['prices'][symbol] = dict(price=50, currency='EUR', observed_at='2026-09-01T12:00:00+00:00')
    path.write_text(json.dumps(prices))
    page.reload()
    page.get_by_role('tab', name='Exposure', exact=True).click()
    page.get_by_role('combobox', name='Asset classifications', exact=True).click()
    page.get_by_role('option', name='Sector', exact=True).click()
    table = page.get_by_role('table', name='Exposure assets', exact=True)
    playwright.expect(table).to_contain_text('Invented sector')
    page.get_by_role('radio', name='Geography', exact=True).click()
    results = page.locator('.st-key-exposure_results')
    playwright.expect(results.locator('.js-plotly-plot')).to_have_count(1)
    playwright.expect(results.get_by_text('Country coverage:', exact=False)).to_be_visible()
    chart = results.locator('.js-plotly-plot')
    playwright.expect(chart).to_contain_text('Europe')
    playwright.expect(chart).to_contain_text('Gold')
    playwright.expect(chart).to_contain_text('Crypto')
    page.get_by_role('radio', name='Countries', exact=True).click()
    playwright.expect(chart).to_contain_text('Germany')
    playwright.expect(chart).to_contain_text('United Kingdom')
    page.screenshot(path=str(directory / 'geography.png'), full_page=True)
    page.get_by_role('combobox', name='Geography detail', exact=True).click()
    page.get_by_role('option', name='Europe › Germany', exact=True).click()
    playwright.expect(chart).to_contain_text('Invented Satellite')
    page.get_by_role('button', name='Back to geography overview', exact=True).click()
    playwright.expect(page.get_by_role('combobox', name='Geography detail', exact=True)).to_have_value('Entire selection')
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
