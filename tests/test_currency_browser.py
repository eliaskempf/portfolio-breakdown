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
    page.get_by_role('button', name='tune Portfolio settings', exact=True).click()
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
        page.get_by_role('button', name='tune Portfolio settings', exact=True).click()
    settings.get_by_role('combobox', name='Portfolio currency').click()
    page.get_by_role('option', name='USD', exact=True).click()
    settings.get_by_role('button', name='Review currency change', exact=True).click()
    dialog.get_by_role('button', name='Apply currency change').click()
    playwright.expect(page.get_by_test_id('stMetric').filter(has=page.get_by_text('Current value', exact=True))).to_contain_text('$412.50')

    playwright.expect(page.get_by_role('columnheader', name='Value (USD)', exact=False).first).to_be_visible()
    views = {
        'Positions': page.get_by_role('table', name='Positions', exact=True),
        'Exposure': page.get_by_role('table', name='Exposure assets', exact=True),
        'Rebalance': page.get_by_role('button', name='Calculate plan', exact=True),
        'Overview': page.get_by_role('radio', name='Allocation', exact=True),
    }
    for tab in ['Positions', 'Exposure', 'Rebalance', 'Overview']:
        navigation = page.get_by_role('tab', name=tab, exact=True)
        playwright.expect(navigation).to_have_count(1)
        navigation.click()
        # Idle can briefly describe the previous run. Wait for this view's
        # content before advancing through the next navigation interaction.
        playwright.expect(views[tab]).to_be_visible()
        page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
        playwright.expect(navigation).to_have_count(1)
        playwright.expect(navigation).to_have_attribute('aria-selected', 'true')
        playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
    assert (directory / 'holdings.csv').read_bytes() == original
    page.reload()
    playwright.expect(page.get_by_test_id('stMetric').filter(has=page.get_by_text('Current value', exact=True))).to_contain_text('$412.50')


@pytest.mark.parametrize('currency,symbol', [('USD', '$'), ('GBP', '£')])
def test_tour_returns_to_reporting_currency_and_plan(ux_page, currency, symbol):
    from portfolio_app.portfolio_settings import save_settings
    page, directory = ux_page
    quotes = json.loads((directory / 'demo_prices.json').read_text())
    quotes['fx'] = {c: dict(price=r, currency='EUR', observed_at='2026-09-01T12:00:00+00:00')
                    for c, r in [('USD', .8), ('GBP', 1.2)]}
    (directory / 'demo_prices.json').write_text(json.dumps(quotes))
    save_settings(directory, currency, None)
    saved = {name: (directory / name).read_bytes() for name in ('holdings.csv', 'portfolio.yaml')}
    page.reload()
    value = page.get_by_test_id('stMetric').filter(has=page.get_by_text('Current value', exact=True))
    playwright.expect(value).to_contain_text(symbol)
    page.get_by_role('tab', name='Rebalance', exact=True).click()
    amount = page.get_by_role('spinbutton', name='Contribution', exact=True)
    amount.fill('750')
    amount.press('Enter')
    page.get_by_role('button', name='Calculate plan', exact=True).click()
    playwright.expect(page.get_by_role('table', name='Suggested trades', exact=True)).to_be_visible()
    page.get_by_role('button', name='?', exact=True).click()
    page.get_by_role('button', name='Take the tour', exact=True).click()
    playwright.expect(page.locator('.portfolio-tour-overlay')).to_have_attribute('data-ready', 'true')
    playwright.expect(value).to_contain_text('€93,184.35')
    page.get_by_role('button', name='Skip tour', exact=True).click()
    playwright.expect(page.locator('.portfolio-tour-overlay')).to_have_count(0)
    playwright.expect(amount).to_have_value('750.00')
    playwright.expect(page.get_by_role('table', name='Suggested trades', exact=True)).to_be_visible()
    page.get_by_role('tab', name='Overview', exact=True).click()
    playwright.expect(value).to_contain_text(symbol)
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
    assert {name: (directory / name).read_bytes() for name in saved} == saved
