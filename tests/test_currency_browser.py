"""Reporting-currency settings and menu round trips in the real release UI."""
import json
import pytest
from test_ux_browser import ux_page

playwright = pytest.importorskip('playwright.sync_api')


def test_currency_review_estimates_and_all_main_tabs(ux_page):
    page, directory = ux_page
    quotes = json.loads((directory / 'demo_prices.json').read_text())
    quotes['fx'] = {c: dict(price=r, currency='EUR', observed_at='2026-09-01T12:00:00+00:00') for c,r in [('USD', .8), ('GBP', 1.2)]}
    (directory / 'demo_prices.json').write_text(json.dumps(quotes))
    original = (directory / 'holdings.csv').read_bytes()
    page.get_by_role('button', name='tune Settings', exact=True).click()
    settings = page.get_by_test_id('stPopoverBody')
    settings.get_by_role('combobox', name='Portfolio currency').click()
    page.get_by_role('option', name='USD', exact=True).click()
    settings.get_by_role('button', name='Review currency change', exact=True).click()
    dialog = page.get_by_role('dialog', name='Change portfolio currency')
    playwright.expect(dialog).to_contain_text('3 positions have incomplete costs')
    dialog.get_by_role('button', name='Cancel currency change').click()
    playwright.expect(page.get_by_test_id('stMetric').filter(has=page.get_by_text('Current value', exact=True))).to_contain_text('€330.00')
    assert (directory / 'holdings.csv').read_bytes() == original
    if not settings.is_visible():
        page.get_by_role('button', name='tune Settings', exact=True).click()
    settings.get_by_role('combobox', name='Portfolio currency').click()
    page.get_by_role('option', name='USD', exact=True).click()
    settings.get_by_role('button', name='Review currency change', exact=True).click()
    dialog.get_by_role('button', name='Apply currency change').click()
    playwright.expect(page.get_by_test_id('stMetric').filter(has=page.get_by_text('Current value', exact=True))).to_contain_text('$412.50')
    playwright.expect(page.get_by_role('columnheader', name='Value (USD)', exact=False).first).to_be_visible()
    for tab in ['Positions', 'Exposure', 'Rebalance', 'Overview']:
        page.get_by_role('tab', name=tab, exact=True).click()
        page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
        playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
    assert (directory / 'holdings.csv').read_bytes() == original
    page.reload()
    playwright.expect(page.get_by_test_id('stMetric').filter(has=page.get_by_text('Current value', exact=True))).to_contain_text('$412.50')
