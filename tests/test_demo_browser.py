"""The shipped offline demo's headline exposure features, in a real browser."""
from test_intro_browser import intro_page as intro_page, intro_server as intro_server, playwright


def idle(page):
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")


def test_public_demo_breakdown_sectors_geography_and_rebalance(intro_page):
    page, url, _ = intro_page
    page.emulate_media(reduced_motion='reduce')
    page.goto(url)
    page.get_by_role('button', name='Explore demo', exact=True).click()
    playwright.expect(page.get_by_test_id('stMetric').filter(
        has=page.get_by_text('Current value', exact=True))).to_contain_text('93,184.35')
    page.get_by_role('tab', name='Exposure', exact=True).click()
    table = page.get_by_role('table', name='Exposure assets', exact=True)
    playwright.expect(table).to_contain_text('Nvidia')
    playwright.expect(page.get_by_role('combobox', name='Asset classifications', exact=True)).to_have_value('Sector')
    idle(page)
    table.get_by_text('Nvidia', exact=True).click()
    page.get_by_role('button', name='Details for Nvidia', exact=True).click()
    dialog = page.get_by_role('dialog')
    dialog.get_by_text('ETF breakdown:', exact=False).click()
    playwright.expect(dialog.get_by_role('table', name='Xtrackers MSCI World holdings', exact=True)).to_contain_text('Nvidia')
    dialog.get_by_role('radio', name='Summary', exact=True).click()
    playwright.expect(dialog.locator('.js-plotly-plot').first).to_be_visible()
    playwright.expect(dialog.get_by_text('invented', exact=False).first).to_be_visible()
    idle(page)
    dialog.get_by_role('button', name='Close exposure details', exact=True).click()
    playwright.expect(dialog).to_have_count(0)
    page.get_by_role('radio', name='Themes & sectors', exact=True).click()
    chart = page.locator('.st-key-exposure_results .js-plotly-plot').last
    playwright.expect(chart).to_contain_text('Technology')
    playwright.expect(chart).to_contain_text('Financials')
    page.get_by_role('radio', name='Geography', exact=True).click()
    playwright.expect(chart).to_contain_text('Europe')
    playwright.expect(chart).to_contain_text('Money market')
    page.get_by_role('radio', name='Countries', exact=True).click()
    playwright.expect(chart).to_contain_text('Germany')
    idle(page)
    page.get_by_role('combobox', name='Geography detail', exact=True).fill('Germany')
    page.get_by_role('option', name='Europe › Germany', exact=True).click()
    playwright.expect(chart).to_contain_text('Siemens')
    page.get_by_role('button', name='Back to geography overview', exact=True).click()
    playwright.expect(chart).to_contain_text('Germany')
    idle(page)
    page.get_by_text('Break down ETFs', exact=True).click()
    playwright.expect(chart).not_to_contain_text('Germany')
    playwright.expect(chart).to_contain_text('Money market')
    page.get_by_text('Break down ETFs', exact=True).click()
    playwright.expect(chart).to_contain_text('Germany')
    page.get_by_role('tab', name='Rebalance', exact=True).click()
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
