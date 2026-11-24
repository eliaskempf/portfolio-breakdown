"""Analytics fits the refactored navigation; all screenshots use invented data."""
import pytest
from test_ux_browser import ux_page, playwright
from browser_support import settle


@pytest.mark.parametrize('ux_page', ['count_app_runs'], indirect=True)
def test_analytics_views_dialog_and_mobile_layout(ux_page):
    page, directory = ux_page
    full_name = 'Invented Global International Investment'
    holdings = directory / 'holdings.csv'
    holdings.write_text(holdings.read_text().replace('Invented Global', full_name))
    page.reload()
    page.get_by_role('tab', name='Overview', exact=True).wait_for()
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
    chart = page.locator('.js-plotly-plot').filter(has=page.locator('.heatmaplayer'))
    playwright.expect(chart).to_be_visible()
    assert chart.evaluate('el => el.layout.xaxis.ticktext') == [full_name[:27] + '…', 'Invented Regional', 'Invented Satellite']
    settle(page)
    chart.scroll_into_view_if_needed()
    cell = chart.evaluate("""el => {
        const layout = el._fullLayout;
        return {x: layout._size.l + layout.xaxis.c2p(1), y: layout._size.t + layout.yaxis.c2p(0)};
    }""")
    chart.hover(position=cell)
    lines = chart.locator('.hoverlayer .hovertext tspan.line')
    playwright.expect(lines).to_have_count(3)
    assert lines.all_text_contents()[:2] == [full_name, 'Invented Regional']
    assert lines.nth(2).text_content().startswith('Correlation: ')
    page.mouse.move(0, 0)
    assert chart.bounding_box()['height'] >= 420
    assert chart.bounding_box()['y'] < page.get_by_text('Holdings risk', exact=True).bounding_box()['y']
    settle(page)
    before = (directory / 'full-runs.log').read_text()
    page.get_by_text('Expand', exact=True).click()
    playwright.expect(page.get_by_role('switch', name='Expand', exact=True)).to_be_checked()
    playwright.expect(chart).to_be_visible()
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
    page.wait_for_function("document.querySelector('.st-key-risk_correlations .js-plotly-plot')?.getBoundingClientRect().height >= 640")
    assert chart.bounding_box()['height'] >= 640
    page.get_by_role('switch', name='Expand', exact=True).press('Space')
    playwright.expect(page.get_by_role('switch', name='Expand', exact=True)).not_to_be_checked()
    page.wait_for_function("document.querySelector('.st-key-risk_correlations .js-plotly-plot')?.getBoundingClientRect().height === 420")
    page.get_by_role('button', name='Columns', exact=True).click()
    page.get_by_role('button', name='Remove Beta', exact=True).click()
    page.keyboard.press('Escape')
    settle(page)
    headers = page.locator('[data-testid=stDataFrame]:visible canvas table [role=columnheader]')
    assert 'Volatility (%)' in headers.all_text_contents() and 'Beta' not in headers.all_text_contents()
    assert (directory / 'full-runs.log').read_text() == before
    page.get_by_role('button', name='Main menu', exact=True).click()
    page.get_by_test_id('stMainMenuItem-theme-Dark').click()
    page.keyboard.press('Escape')
    chart.scroll_into_view_if_needed()
    page.screenshot(path=str(directory / 'analytics-correlations-dark.png'), full_page=True)
    page.get_by_role('button', name='Main menu', exact=True).click()
    page.get_by_test_id('stMainMenuItem-theme-Light').click()
    page.keyboard.press('Escape')
    page.get_by_role('tab', name='Positions', exact=True).click()
    page.get_by_role('radio', name='Valuation', exact=True).click()
    table = page.get_by_role('table', name='Positions', exact=True)
    playwright.expect(table.get_by_role('button', name='Trailing P/E', exact=True)).to_be_visible()
    assert table.locator('thead th').count() == 6  # Identity, value, three metrics, edit.
    page.get_by_role('searchbox', name='Filter positions').fill('Regional')
    playwright.expect(table.locator('tbody tr')).to_have_count(1)
    playwright.expect(table.locator('tbody tr').first).to_contain_text('Invented Regional')
    table.locator('tbody tr').first.click()
    dialog = page.get_by_role('dialog')
    # The selector arrives before the initial History body. Wait for that render
    # to finish so the next click does not target a control being replaced.
    playwright.expect(dialog.locator('[data-position-view-ready="History"]')).to_have_count(1)
    settle(page)
    metrics = dialog.get_by_role('radio', name='Key metrics', exact=True)
    metrics.click()
    playwright.expect(metrics).to_be_checked()
    playwright.expect(dialog.locator('[data-position-view-ready="Key metrics"]')).to_have_count(1)
    settle(page)
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


@pytest.mark.parametrize('ux_page', ['pending_fundamentals'], indirect=True)
def test_fundamental_refresh_updates_open_position_dialog_without_rebuilding_parent(ux_page):
    page, directory = ux_page
    page.get_by_role('tab', name='Positions', exact=True).click()
    page.get_by_role('radio', name='Valuation', exact=True).click()
    table = page.get_by_role('table', name='Positions', exact=True)
    page.get_by_role('searchbox', name='Filter positions').fill('Regional')
    table.locator('tbody tr').first.click()
    dialog = page.get_by_role('dialog')
    dialog.locator('[data-position-view-ready="History"]').wait_for(state='attached')
    dialog.get_by_role('radio', name='Key metrics', exact=True).click()
    metric = dialog.get_by_test_id('stMetric').filter(has_text='P/E (trailing)').get_by_test_id('stMetricValue')
    playwright.expect(metric).to_have_text('10.00')
    assert (directory / 'requested-SYNTH-B').exists()
    before = (directory / 'full-runs.log').read_text()
    (directory / 'release-SYNTH-B').touch()
    page.wait_for_timeout(800)
    playwright.expect(metric).to_have_text('10.00')
    for ticker in ('SYNTH-A', 'SYNTH-C'):
        (directory / ('release-' + ticker)).touch()
    playwright.expect(metric).to_have_text('20.00', timeout=10000)
    assert (directory / 'full-runs.log').read_text() == before
    assert len(list(directory.glob('requested-SYNTH-*'))) == 3
    dialog.get_by_role('button', name='Close', exact=True).last.click()
    playwright.expect(dialog).to_have_count(0)
    playwright.expect(page.get_by_role('searchbox', name='Filter positions')).to_have_value('Regional')
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


@pytest.mark.parametrize('ux_page', ['company_details'], indirect=True)
def test_company_detail_prepares_once_on_open_and_reads_external_changes(ux_page):
    page, directory = ux_page
    page.get_by_role('radio', name='Analytics', exact=True).click()
    label = page.get_by_text('Underlying company exposure', exact=True)
    label.wait_for()
    settle(page)
    section = page.get_by_test_id('stExpander').filter(has=label)
    analysis = directory / 'company-runs.log'
    full_runs = directory / 'full-runs.log'
    before = full_runs.read_text()
    assert not analysis.exists()
    playwright.expect(section.get_by_test_id('stDataFrame')).to_have_count(0)

    for _ in range(2):
        label.click()
        row = section.locator('canvas tr').filter(has_text='Invented Satellite')
        playwright.expect(row.locator('[role=gridcell]:text-is("80")').first).to_be_attached()
        label.click()
        playwright.expect(section.get_by_test_id('stDataFrame')).to_have_count(0)
    assert analysis.read_text().splitlines() == ['analysis']
    assert full_runs.read_text() == before

    holdings = directory / 'holdings.csv'
    holdings.write_text(holdings.read_text().replace('Invented Satellite,SYNTH-C,1', 'Revised synthetic company,SYNTH-C,2'))
    label.click()
    revised = section.locator('canvas tr').filter(has_text='Revised synthetic company')
    playwright.expect(revised.locator('[role=gridcell]:text-is("160")').first).to_be_attached()
    assert analysis.read_text().splitlines() == ['analysis', 'analysis']
    assert full_runs.read_text() != before
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
