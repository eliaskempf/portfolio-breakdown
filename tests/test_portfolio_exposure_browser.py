"""Real browser signed charts, on invented data only."""
from dataclasses import asdict
import json
import re

from portfolio_app.futures import FuturePosition
from portfolio_app.futures_workspace import SANDBOX_FILE
from test_ux_browser import ux_page as ux_page, playwright
from browser_support import settle, select_tab


def test_sold_unassigned_asset_does_not_blank_market_exposure_gaps(ux_page):
    page, directory = ux_page
    holdings = directory/'holdings.csv'
    lines = holdings.read_text().splitlines()
    holdings.write_text(lines[0]+',instrument_type\n'+'\n'.join(line+',equity' for line in lines[1:])
        +'\nsold,sold,Invented sold asset,,0,,,Invented account,,,crypto\n')
    position = FuturePosition(entry_price=5., mark_price=5., bucket_id='core')
    (directory/SANDBOX_FILE).write_text(json.dumps(dict(version=2, simulation=True,
        instrument='FMWN', position=asdict(position))))
    page.reload(); settle(page)
    table = page.get_by_role('table', name='Market exposure', exact=True)
    playwright.expect(table).to_be_visible()
    playwright.expect(table.get_by_text('Unassigned', exact=True)).to_have_count(0)
    headers = table.get_by_role('columnheader').all_text_contents()
    for label in ('% of exposure', 'Gap (pp)'):
        index = next(i for i, text in enumerate(headers) if text.startswith(label))
        cells = table.locator('tbody tr').locator(f'td:nth-child({index+1})')
        assert all(text.strip() not in ('', '—') for text in cells.all_text_contents())
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


def test_market_basis_survives_real_browser_remount(ux_page):
    page, directory = ux_page
    position = FuturePosition(entry_price=5., mark_price=5., bucket_id='core')
    (directory / SANDBOX_FILE).write_text(json.dumps(dict(version=2, simulation=True,
        instrument='FMWN', position=asdict(position))))
    page.reload()
    select_tab(page, 'Exposure')
    page.get_by_role('button', name='Data & settings', exact=True).click()
    basis = page.get_by_role('radio', name='Equity only', exact=True)
    basis.click()
    playwright.expect(basis).to_be_checked()
    settle(page)
    playwright.expect(basis).to_be_checked()
    page.keyboard.press('Escape')
    select_tab(page, 'Positions')
    select_tab(page, 'Exposure')
    page.get_by_role('button', name='Data & settings', exact=True).click()
    playwright.expect(basis).to_be_checked()
    # A subsequent edit must use the restored value rather than overwriting it
    # with the browser's old default on its next request.
    page.keyboard.press('Escape')
    page.get_by_role('radio', name='Geography', exact=True).click()
    page.get_by_role('button', name='Data & settings', exact=True).click()
    settle(page)
    playwright.expect(basis).to_be_checked()


def test_normal_and_sandbox_exposure_controls(ux_page):  # noqa: F811
    page, directory = ux_page
    playwright.expect(page.get_by_role('radio', name='Capital allocation', exact=True)).to_have_count(0)
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
    # Mark the three deliberately invented positions explicitly as equities.
    holdings = directory / 'holdings.csv'
    lines = holdings.read_text().splitlines()
    holdings.write_text(lines[0] + ',instrument_type\n' + '\n'.join(line + ',equity' for line in lines[1:]) + '\n')
    position = FuturePosition(contracts=-1, entry_price=5., mark_price=5., bucket_id='core')
    (directory / SANDBOX_FILE).write_text(json.dumps(dict(version=2, simulation=True, instrument='FMWN', position=asdict(position))))
    page.reload()
    page.get_by_role('tab', name='Overview', exact=True).click()
    exposure_card = page.locator('.st-key-equity_exposure_metric')
    playwright.expect(exposure_card.get_by_test_id('stMetricValue')).to_contain_text('-0.52×')
    page.get_by_role('button', name='Show gross market exposure', exact=True).click()
    playwright.expect(exposure_card.get_by_test_id('stMetricValue')).to_contain_text('2.52×')
    playwright.expect(page.get_by_text('Portfolio leverage · net', exact=True)).to_have_count(0)
    # The gains toggle must coexist with the exposure toggle's DOM listeners.
    page.get_by_role('button', name='Show gain as percentage', exact=True).click()
    playwright.expect(page.get_by_role('button', name='Show gain as amount', exact=True)).to_be_visible()
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
    page.get_by_role('button', name='Show net market exposure', exact=True).press('Enter')
    playwright.expect(exposure_card.get_by_test_id('stMetricValue')).to_contain_text('-0.52×')
    page.get_by_role('button', name='Show gross market exposure', exact=True).press('Space')
    playwright.expect(exposure_card.get_by_test_id('stMetricValue')).to_contain_text('2.52×')
    if not page.get_by_role('radio', name='Market exposure', exact=True).is_checked():
        page.get_by_role('radio', name='Market exposure', exact=True).click()
    chart = page.locator('.st-key-strategic_exposure_chart .js-plotly-plot')
    playwright.expect(chart).to_be_visible()
    assert chart.evaluate('el => el.data[0].type') == 'bar'
    assert chart.evaluate('el => Array.from(el._fullData[0].x).some(x => x < 0)')
    assert chart.evaluate('el => el.layout.xaxis.zeroline')
    future_row = page.locator('tr[data-position-id="future:overlay"]')
    playwright.expect(future_row).to_be_visible()
    cells = future_row.locator('td')
    # Unfunded capital is unavailable; -500 is signed notional, shown above
    # in the exposure chart and below in contract details, never owned wealth.
    playwright.expect(cells.nth(1)).to_have_text('—')
    playwright.expect(cells.nth(2)).to_have_text('—')
    playwright.expect(cells.nth(3)).to_have_text('—')
    playwright.expect(future_row.locator('.secondary')).to_have_count(0)
    playwright.expect(future_row.get_by_role('button', name='Show contract details for FMWN future', exact=True)).to_have_attribute('aria-expanded', 'false')
    playwright.expect(page.get_by_role('table', name='Positions', exact=True).get_by_role('columnheader', name=re.compile(r'^Value \(EUR\)'))).to_have_count(1)
    future_row.click()
    page.get_by_role('dialog').get_by_text('Contract & collateral', exact=True).click()
    playwright.expect(page.get_by_role('dialog')).to_contain_text('Signed notional exposure')
    playwright.expect(page.get_by_role('dialog')).to_contain_text('€-500.00')
    page.get_by_role('button', name='Close', exact=True).click()
    playwright.expect(page.get_by_role('dialog')).to_have_count(0)
    page.wait_for_timeout(500)
    page.get_by_role('combobox', name='Category', exact=True).click()
    page.get_by_role('combobox', name='Category', exact=True).press('ArrowDown')
    page.get_by_role('option', name='ETF core', exact=True).click()
    playwright.expect(page.locator('.st-key-strategic_exposure_chart .js-plotly-plot')).to_be_visible()
    playwright.expect(page.get_by_text('Category leverage · gross', exact=True)).to_be_visible()
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
    playwright.expect(exposure_card.get_by_test_id('stMetricValue')).to_contain_text('3.00×')
    playwright.expect(page.get_by_test_id('stMetric').filter(has=page.get_by_text('Portfolio share', exact=True))).to_contain_text('75.8%')
    playwright.expect(future_row).to_be_visible()
    future_row.click()
    page.get_by_role('button', name='Open Futures analysis', exact=True).click()
    playwright.expect(page.get_by_role('tab', name='Futures', exact=True)).to_have_attribute('aria-selected', 'true')
    page.get_by_role('tab', name='Exposure', exact=True).click()
    playwright.expect(page.locator('.st-key-equity_exposure_metric')).to_have_count(0)
    for view in ['Assets', 'Themes & sectors', 'Geography']:
        if not page.get_by_role('radio', name=view, exact=True).is_checked():
            page.get_by_role('radio', name=view, exact=True).click()
        settle(page)
        if view == 'Assets':
            playwright.expect(page.get_by_role('table', name='Exposure assets', exact=True)).to_be_visible()
        else:
            playwright.expect(page.locator('.st-key-exposure_results .js-plotly-plot').first).to_be_visible()
        playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
    page.get_by_role('tab', name='Rebalance', exact=True).click()
    playwright.expect(page.get_by_text('Capital plan', exact=False)).to_be_visible()
    page.get_by_role('tab', name='Targets', exact=True).click()
    page.get_by_role('radio', name='Exposure targets', exact=True).click()
    playwright.expect(page.get_by_text('Exposure targets are unset.', exact=False)).to_be_visible()
    page.get_by_role('combobox', name='Exposure policy', exact=True).click()
    page.get_by_role('option', name='Keep funded allocation; overlay in one category', exact=True).click()
    multiple = page.get_by_role('spinbutton', name='Desired total exposure (× net equity)', exact=True)
    multiple.fill('1.2')
    destination = page.get_by_role('combobox', name='Overlay destination (overlay-only policy)', exact=True)
    page.wait_for_timeout(500)
    destination.click()
    destination.press('ArrowDown')
    destination.press('ArrowDown')
    destination.press('Enter')
    playwright.expect(destination).to_have_value('ETF core')
    page.get_by_role('button', name='Save exposure targets', exact=True).click()
    playwright.expect(page.get_by_text('Gap = desired', exact=False)).to_be_visible()
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
    # Change only synthetic persisted data to check above-100% chart rendering.
    position = FuturePosition(contracts=1, entry_price=5., mark_price=5., bucket_id='core')
    (directory / SANDBOX_FILE).write_text(json.dumps(dict(version=2, simulation=True, instrument='FMWN', position=asdict(position))))
    page.reload()
    page.get_by_role('tab', name='Overview', exact=True).click()
    if not page.get_by_role('radio', name='Market exposure', exact=True).is_checked():
        page.get_by_role('radio', name='Market exposure', exact=True).click()
    sunburst = page.locator('.st-key-overview_allocation .js-plotly-plot')
    playwright.expect(sunburst).to_be_visible()
    page.wait_for_function("document.querySelector('.st-key-overview_allocation .js-plotly-plot')?.data?.[0]?.type === 'sunburst'")
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


def test_funded_strategy_value_return_close_and_negative_chart(ux_page):
    page, directory = ux_page
    from test_futures_returns import write_synthetic_return_history
    write_synthetic_return_history(directory, '2026-08-05')
    position = FuturePosition(contracts=1, entry_price=5., mark_price=5.1, bucket_id='core',
                              reference_capital_eur=200., net_funding_eur=30., closed_pnl_eur=5., fees_eur=2.,
                              quote_date='2026-08-05', reference_price=5.1, contract_expiry='2026-12-18',
                              entry_date='2026-07-01', entry_reference_price=5., entry_price_source='https://example.test/entry')
    (directory / SANDBOX_FILE).write_text(json.dumps(dict(version=2, simulation=True, instrument='FMWN', position=asdict(position))))
    from portfolio_app.futures_mtm import refresh_settlements
    from test_futures_mtm import quote
    refresh_settlements(directory, position, fetch=lambda day, expiry: quote(day.isoformat(), 5. if day.isoformat() == position.entry_date else 5.1))
    page.reload()
    playwright.expect(page.get_by_role('tab', name='Overview', exact=True)).to_have_attribute('aria-selected', 'true')
    playwright.expect(page.locator('.st-key-overview_value')).to_contain_text('€573.00')
    future_row = page.locator('tr[data-position-id="future:overlay"]')
    playwright.expect(future_row.locator('td').nth(1)).to_contain_text('243.00')
    playwright.expect(future_row.locator('td').nth(3)).to_contain_text('6.50')
    playwright.expect(future_row.locator('.secondary')).to_have_count(0)
    details = page.locator('.contract-detail-row')
    playwright.expect(details).to_have_count(0)
    toggle = future_row.locator('[data-detail-toggle]')
    toggle.click()
    playwright.expect(toggle).to_have_attribute('aria-expanded', 'true')
    playwright.expect(page.get_by_role('dialog')).to_have_count(0)
    for text in ['Notional', '€510', 'Collateral', '€243', 'Dec 2026']:
        playwright.expect(details).to_contain_text(text)
    playwright.expect(details).not_to_contain_text('Initial margin')
    assert details.bounding_box()['height'] < 50
    assert toggle.evaluate('button => button.previousSibling.nodeType === Node.TEXT_NODE')
    # Keyboard toggling only changes the inline details, never opens a dialog.
    toggle.press('Enter')
    playwright.expect(details).to_have_count(0)
    toggle.press('Space')
    playwright.expect(details).to_be_visible()
    playwright.expect(page.get_by_role('dialog')).to_have_count(0)
    # Sort / filter retain the disclosure and leave ordinary rows untouched.
    page.get_by_role('button', name='Investment', exact=True).click()
    playwright.expect(details).to_be_visible()
    page.get_by_role('searchbox', name='Filter positions', exact=True).fill('FMWN')
    playwright.expect(future_row).to_be_visible()
    playwright.expect(details).to_be_visible()
    page.get_by_role('searchbox', name='Filter positions', exact=True).fill('')
    # Clicking anywhere else on the parent row opens its normal details screen.
    future_row.locator('td').nth(1).click()
    dialog = page.get_by_role('dialog')
    playwright.expect(dialog.locator('.st-key-future_position_index_chart .js-plotly-plot')).to_be_visible()
    playwright.expect(dialog).to_contain_text('MSCI World ETF proxy')
    playwright.expect(dialog.get_by_text('Reference collateral', exact=True)).not_to_be_visible()
    playwright.expect(dialog.get_by_text('Settlement P&L', exact=True)).not_to_be_visible()
    dialog.get_by_text('Daily MTM', exact=True).click()
    playwright.expect(dialog.get_by_text('Maximum drawdown', exact=True)).to_be_visible()
    playwright.expect(dialog.get_by_test_id('stMetric').filter(has=page.get_by_text('Settlement P&L', exact=True))).to_contain_text('€10.00')
    playwright.expect(dialog.get_by_test_id('stDataFrame')).to_be_visible()
    dialog.get_by_text('Contract & collateral', exact=True).click()
    for text in ['Contract & collateral', 'Reference collateral', '€200.00', 'Current collateral balance',
                 '€243.00', '2026-07-01', '2026-12-18', '2026-08-05', 'Eurex']:
        playwright.expect(page.get_by_role('dialog')).to_contain_text(text)
    playwright.expect(page.get_by_role('dialog')).to_contain_text('Return')
    settle(page)
    dialog.get_by_role('button', name='Open Futures analysis', exact=True).click()
    playwright.expect(dialog).to_have_count(0)
    playwright.expect(page.get_by_role('tab', name='Futures', exact=True)).to_have_attribute('aria-selected', 'true')
    settle(page)
    page.get_by_text('Strategy capital & lifetime gains', exact=True).click()
    page.get_by_role('button', name='Close saved contract and retain cash', exact=True).click()
    select_tab(page, 'Overview')
    playwright.expect(future_row).to_be_visible()
    playwright.expect(future_row.locator('td').nth(1)).to_contain_text('243.00')
    if toggle.get_attribute('aria-expanded') != 'true':
        toggle.click()
    playwright.expect(details).to_contain_text('0 · Closed')
    # A negative remaining balance must remain visible and avoid negative areas.
    from dataclasses import replace
    position = replace(position, closed_pnl_eur=-400.)
    (directory / SANDBOX_FILE).write_text(json.dumps(dict(version=2, simulation=True, instrument='FMWN', position=asdict(position))))
    page.reload()
    page.get_by_role('tab', name='Overview', exact=True).click()
    page.get_by_role('radio', name='Capital allocation', exact=True).click()
    playwright.expect(page.get_by_role('radio', name='Capital allocation', exact=True)).to_be_checked()
    chart = page.locator('.st-key-signed_capital_chart .js-plotly-plot')
    playwright.expect(chart).to_be_visible()
    assert chart.evaluate('el => el.data[0].type') == 'bar'
    playwright.expect(future_row.locator('td').nth(1)).to_contain_text('-162.00')
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


def test_future_signed_beta_and_core_contribution(ux_page):
    page, directory = ux_page
    betas = []
    for contracts in [1, -1]:
        position = FuturePosition(contracts=contracts, entry_price=5., mark_price=5.,
                                  bucket_id='core', reference_capital_eur=200.)
        (directory / SANDBOX_FILE).write_text(json.dumps(dict(version=2, simulation=True,
            instrument='FMWN', position=asdict(position))))
        page.reload()
        playwright.expect(page.get_by_role('tab', name='Overview', exact=True)).to_have_attribute('aria-selected', 'true')
        page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
        page.get_by_role('radio', name='Analytics', exact=True).click()
        page.get_by_role('button', name='Calculate risk').click()
        metric = page.get_by_test_id('stMetric').filter(has=page.get_by_text('Beta', exact=True))
        playwright.expect(metric).to_be_visible()
        betas.append(float(metric.get_by_test_id('stMetricValue').inner_text()))
        chart = page.locator('.js-plotly-plot').filter(has=page.locator('.heatmaplayer'))
        playwright.expect(chart).to_be_visible()
        assert 'FMWN future · index proxy' in chart.evaluate('el => el.layout.xaxis.ticktext')
        playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
    assert betas[0] > betas[1]
    category = page.get_by_role('combobox', name='Category', exact=True)
    category.click()
    category.press('ArrowDown')
    page.get_by_role('option', name='ETF core', exact=True).click()
    playwright.expect(page.get_by_text('Subset beta', exact=True)).to_be_visible()
    playwright.expect(page.get_by_text('Subset volatility (annualized)', exact=True)).to_be_visible()
    playwright.expect(page.get_by_text('Portfolio contribution', exact=False)).to_be_visible()
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


def test_linked_future_hatching_exposure_sunburst_and_company_shock(ux_page):
    page, directory = ux_page
    from test_future_proxy_exposure import write_proxy
    from portfolio_app.positions import read_snapshot
    holdings = directory / 'holdings.csv'
    lines = holdings.read_text().splitlines()
    holdings.write_text(lines[0] + ',instrument_type\n' + '\n'.join(line + (',etf' if 'SYNTH-A' in line else ',equity') for line in lines[1:]) + '\n')
    write_proxy(directory, fund_ticker='SYNTH-A', direct_ticker='SYNTH-C')
    frame = read_snapshot(holdings).holdings
    companion = frame.loc[frame.ticker.eq('SYNTH-A'), 'position_id'].item()
    future = FuturePosition(entry_price=5., mark_price=5., bucket_id='core', reference_capital_eur=200., proxy_position_id=companion)
    (directory / SANDBOX_FILE).write_text(json.dumps(dict(version=2, simulation=True, instrument='FMWN', position=asdict(future))))
    page.reload()
    row = page.locator('tr[data-position-id="future:overlay"]')
    playwright.expect(row).to_be_visible()
    playwright.expect(page.get_by_role('radio', name='Market exposure', exact=True)).to_be_checked()
    page.get_by_role('radio', name='Capital allocation', exact=True).click()
    settle(page)
    playwright.expect(row.locator('td').nth(1)).to_contain_text('200.00')
    assert row.evaluate('(el, companion) => el.previousElementSibling.dataset.positionId === companion', companion)
    chart = page.locator('.st-key-overview_allocation .js-plotly-plot')
    playwright.expect(chart).to_be_visible()
    settle(page)
    assert chart.evaluate('''(el, companion) => {
      const trace=el.data[0], ids=trace.ids.map(id=>JSON.parse(id)[2]);
      const a=ids.indexOf(companion), b=ids.indexOf('future:overlay');
      return b===a+1 && trace.marker.colors[a]===trace.marker.colors[b] && trace.marker.pattern.shape[b]==='/' && trace.marker.pattern.fillmode==='overlay'
        && trace.marker.pattern.bgcolor[b]===trace.marker.colors[a] && trace.marker.pattern.fgcolor[b]!==trace.marker.colors[a];
    }''', companion), chart.evaluate('el => ({ids:el.data[0].ids, marker:el.data[0].marker})')
    playwright.expect(page.locator('.position-swatch')).to_have_count(0)
    page.screenshot(path=str(directory / 'synthetic-companion-hatch.png'), full_page=True)
    # Adjacent after sorting; disclosure still does not open the dialog.
    page.get_by_role('button', name='Investment', exact=True).click()
    assert row.evaluate('(el, companion) => el.previousElementSibling.dataset.positionId === companion', companion)
    row.locator('[data-detail-toggle]').click()
    playwright.expect(page.get_by_role('dialog')).to_have_count(0)
    page.get_by_role('radio', name='Market exposure', exact=True).click()
    settle(page)
    playwright.expect(chart).to_be_visible()
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning' && !!document.querySelector('.st-key-overview_allocation .js-plotly-plot')?._fullData?.[0]")
    assert chart.evaluate('el => el.data[0].type') == 'sunburst'
    playwright.expect(row.locator('td').nth(1)).to_contain_text('200.00')
    playwright.expect(page.get_by_role('table', name='Positions', exact=True).get_by_role('columnheader', name=re.compile(r'^Value \(EUR\)'))).to_be_visible()
    assert chart.evaluate('''el => {
      const trace = el._fullData[0];
      return trace.values[trace.ids.findIndex(id => JSON.parse(id)[2] === 'future:overlay')];
    }''') == 500
    page.get_by_role('combobox', name='Category', exact=True).click()
    page.get_by_role('combobox', name='Category', exact=True).press('ArrowDown')
    page.get_by_role('option', name='ETF core', exact=True).click()
    playwright.expect(page.get_by_text('Category leverage · net', exact=True)).to_be_visible()
    playwright.expect(page.locator('.st-key-equity_exposure_metric')).to_have_count(1)
    playwright.expect(page.locator('.st-key-equity_exposure_metric')).to_contain_text('1.67×')
    page.get_by_role('tab', name='Exposure', exact=True).click()
    exposure = page.locator('.st-key-exposure_results .js-plotly-plot')
    playwright.expect(page.get_by_role('table', name='Exposure assets', exact=True)).to_be_visible()
    playwright.expect(exposure).to_have_count(0)
    page.get_by_text('Company move', exact=True).click()
    settle(page)
    company = page.get_by_role('combobox', name='Company', exact=True)
    company.click()
    company.fill('Invented Satellite')
    page.get_by_role('option', name='Invented Satellite', exact=True).click()
    playwright.expect(page.get_by_test_id('stMetric').filter(has=page.get_by_text('Estimated P&L', exact=True))).to_contain_text('€-18.00')
    page.screenshot(path=str(directory / 'synthetic-proxy-exposure.png'), full_page=True)
    for view in ['Themes & sectors', 'Geography']:
        page.get_by_role('radio', name=view, exact=True).click()
        settle(page)
        playwright.expect(exposure).to_be_visible()
        playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


def test_optional_policy_switch_and_constrained_exposure_plan(ux_page):
    page, directory = ux_page
    holdings = directory/'holdings.csv'
    lines = holdings.read_text().splitlines()
    holdings.write_text(lines[0] + ',instrument_type\n' + '\n'.join(line + ',equity' for line in lines[1:]) + '\n')
    original = holdings.read_bytes()
    capital = (directory/'allocation.yaml').read_bytes()
    page.reload()
    playwright.expect(page.get_by_role('radio', name='Market exposure', exact=True)).to_have_count(0)
    select_tab(page, 'Rebalance')
    select_tab(page, 'Targets')
    playwright.expect(page.get_by_role('radio', name='Exposure targets', exact=True)).to_have_count(0)
    page.get_by_text('Leverage & target policy', exact=True).click()
    page.get_by_text('Enable leverage', exact=True).click()
    playwright.expect(page.get_by_role('checkbox', name='Enable leverage', exact=True)).to_be_checked()
    page.get_by_role('button', name='Save active policy', exact=True).click()
    playwright.expect(page.get_by_role('radio', name='Exposure targets', exact=True)).to_be_visible()
    # Deliberately synthetic future and policy; the UI still activates the policy.
    future = FuturePosition(contracts=1, entry_price=5., mark_price=5., bucket_id='core', reference_capital_eur=100.)
    (directory/SANDBOX_FILE).write_text(json.dumps(dict(version=2, simulation=True, instrument='FMWN', position=asdict(future))))
    (directory/'exposure-targets.json').write_text(json.dumps(dict(version=2, multiple=2., policy='composition', composition={'core': .8, 'active': .2}, universe='market', weights='leaves')))
    page.reload()
    select_tab(page, 'Rebalance')
    select_tab(page, 'Plan')
    combo = page.get_by_role('combobox', name='Active planning basis', exact=True)
    combo.click()
    page.get_by_role('option', name='Market / equity exposure', exact=True).click()
    page.get_by_role('button', name='Save active policy', exact=True).click()
    permitted = page.get_by_role('combobox', name='Permitted instruments', exact=True)
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
    permitted.click()
    page.get_by_role('option', name='FMWN future', exact=True).click()
    # Let the multiselect's batched selection settle before leaving the popup.
    page.wait_for_timeout(1000)
    page.keyboard.press('Tab')
    page.get_by_role('spinbutton', name='Eligible existing future collateral (EUR)', exact=True).fill('100')
    page.get_by_role('spinbutton', name='Declared initial margin per contract (EUR)', exact=True).fill('20')
    page.get_by_role('button', name='Calculate exposure plan', exact=True).click()
    playwright.expect(page.get_by_text('Remaining target gaps:', exact=False)).to_be_visible()
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
    policy = (directory/'exposure-targets.json').read_bytes()
    select_tab(page, 'Overview')
    page.get_by_role('radio', name='Market exposure', exact=True).click()
    settle(page)
    page.get_by_role('radio', name='Capital allocation', exact=True).click()
    select_tab(page, 'Rebalance')
    playwright.expect(page.get_by_role('combobox', name='Active planning basis', exact=True)).to_have_value('Market / equity exposure')
    assert holdings.read_bytes() == original
    assert (directory/'allocation.yaml').read_bytes() == capital
    assert (directory/'exposure-targets.json').read_bytes() == policy
    combo = page.get_by_role('combobox', name='Active planning basis', exact=True)
    combo.click()
    page.get_by_role('option', name='Capital allocation', exact=True).click()
    page.get_by_role('button', name='Save active policy', exact=True).click()
    playwright.expect(page.get_by_text('Capital plan', exact=False)).to_be_visible()
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
