"""Analytics fits the refactored navigation; all screenshots use invented data."""
from test_ux_browser import ux_page, playwright


def test_analytics_views_dialog_and_mobile_layout(ux_page):
    page, directory = ux_page
    assert page.get_by_role('tab').all_text_contents()[:4] == ['Overview', 'Exposure', 'Positions', 'Rebalance']
    page.get_by_role('radio', name='Analytics', exact=True).click()
    playwright.expect(page.get_by_text('Direct-stock P/E', exact=True)).to_be_visible()
    page.screenshot(path=str(directory / 'analytics-overview.png'), full_page=True)
    assert page.get_by_test_id('stException').count() == 0, page.get_by_test_id('stException').all_text_contents()
    playwright.expect(page.get_by_role('checkbox', name='Show portfolio analytics')).to_have_count(0)
    playwright.expect(page.get_by_role('button', name='Calculate risk')).to_be_visible()
    page.get_by_role('button', name='Calculate risk').click()
    playwright.expect(page.get_by_text('Annualized volatility', exact=True)).to_be_visible()
    assert page.get_by_text('Beta', exact=True).bounding_box()['y'] < page.get_by_text('Direct-stock P/E', exact=True).bounding_box()['y']
    page.get_by_text('Annualized volatility', exact=True).scroll_into_view_if_needed()
    page.screenshot(path=str(directory / 'analytics-risk.png'), full_page=True)
    page.get_by_text('Holding correlations', exact=True).click()
    chart = page.locator('.js-plotly-plot').filter(has=page.locator('.heatmaplayer'))
    playwright.expect(chart).to_be_visible()
    assert chart.evaluate('el => el.layout.xaxis.ticktext') == ['Invented Global', 'Invented Regional', 'Invented Satellite']
    assert chart.bounding_box()['height'] >= 640
    page.get_by_role('button', name='Main menu', exact=True).click()
    page.get_by_test_id('stMainMenuItem-theme-Dark').click()
    page.keyboard.press('Escape')
    chart.scroll_into_view_if_needed()
    page.screenshot(path=str(directory / 'analytics-correlations-dark.png'), full_page=True)
    page.get_by_role('button', name='Main menu', exact=True).click()
    page.get_by_test_id('stMainMenuItem-theme-Light').click()
    page.keyboard.press('Escape')
    page.get_by_text('Holding correlations', exact=True).click()
    page.get_by_role('tab', name='Positions', exact=True).click()
    page.get_by_role('radio', name='Valuation', exact=True).click()
    table = page.get_by_role('table', name='Positions', exact=True)
    playwright.expect(table.get_by_role('button', name='Trailing P/E', exact=True)).to_be_visible()
    assert table.locator('thead th').count() == 6  # Identity, value, three metrics, edit.
    page.get_by_role('searchbox', name='Filter positions').fill('Regional')
    table.locator('tbody tr').first.click()
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('radio', name='Key metrics', exact=True).click()
    playwright.expect(dialog.get_by_text('P/E (trailing)', exact=True).first).to_be_visible()
    page.screenshot(path=str(directory / 'analytics-position-detail.png'), full_page=True)
    dialog.get_by_role('button', name='Close', exact=True).last.click()
    playwright.expect(dialog).to_have_count(0)
    playwright.expect(page.get_by_role('searchbox', name='Filter positions')).to_have_value('Regional')
    playwright.expect(page.get_by_role('radio', name='Valuation', exact=True)).to_be_checked()
    page.screenshot(path=str(directory / 'analytics-positions.png'), full_page=True)
    playwright.expect(page.get_by_test_id('stSidebar')).to_have_count(0)
    page.set_viewport_size({'width': 700, 'height': 1000})
    page.get_by_role('tab', name='Overview', exact=True).click()
    playwright.expect(page.get_by_role('tab', name='Overview', exact=True)).to_have_attribute('aria-selected', 'true')
    playwright.expect(page.get_by_text('Direct-stock P/E', exact=True)).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    page.screenshot(path=str(directory / 'analytics-narrow.png'), full_page=True)
    assert page.get_by_test_id('stException').count() == 0
