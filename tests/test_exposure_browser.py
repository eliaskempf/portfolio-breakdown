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
    summary = page.locator('.st-key-exposure_summary')
    playwright.expect(summary).not_to_contain_text('source positions')
    playwright.expect(summary).not_to_contain_text('use a proxy')
    playwright.expect(summary).not_to_contain_text('ETF snapshot')
    value_card = summary.locator('.st-key-exposure_value')
    value_card.get_by_role('button', name='Show gain as percentage', exact=True).click()
    playwright.expect(value_card.get_by_test_id('stMetricDelta')).to_contain_text('+22.22%')
    value_card.get_by_role('button', name='Show gain in euros', exact=True).press('Enter')
    playwright.expect(value_card.get_by_test_id('stMetricDelta')).to_contain_text('+€60.00')
    page.screenshot(path=str(directory / 'exposure.png'))
    table.locator('tr[data-position-id="c"]').click()
    page.get_by_role('button', name='Details for Invented Satellite', exact=True).click()
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
    # The selector arrives before Plotly finishes replacing the previous view.
    page.wait_for_function("document.querySelector('.st-key-exposure_results .js-plotly-plot')?.data?.[0]?.labels?.[0] === 'Theme'")
    assert chart.evaluate('el => el.data[0].labels[0]') == 'Theme'
    label = chart.locator('text.slicetext').filter(has_text='Theme').first
    label.scroll_into_view_if_needed()
    box = label.bounding_box()
    page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
    playwright.expect(page.get_by_role('combobox', name='Detail view', exact=True)).to_have_value('All selected labels')
    page.wait_for_function("document.querySelector('.st-key-exposure_results .js-plotly-plot')?.data?.[0]?.labels?.[0] === 'Selected labels'")
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
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
    sent = []
    page.on('websocket', lambda socket: socket.on('framesent', lambda frame: sent.append(frame)))
    page.reload()
    page.get_by_role('tab', name='Exposure', exact=True).click()
    table = page.get_by_role('table', name='Exposure assets', exact=True)
    row = table.locator('tr[data-position-id="c"]')
    playwright.expect(row).to_contain_text('3 positions')
    playwright.expect(table.get_by_role('columnheader', name='Direct (EUR)', exact=True)).to_have_count(0)
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
    sent.clear()
    # The DOM must expand in the click handler itself, without a server response.
    assert row.evaluate("""el => {
        el.querySelector('[data-preview-toggle]').click();
        return !!el.nextElementSibling?.querySelector('.preview-panel');
    }""")
    preview = page.get_by_role('region', name='Sources for Invented Satellite', exact=True)
    playwright.expect(preview).not_to_contain_text('Total exposure')
    for name, amount, share in [('Invented Global', '€100.00', '48.78% of asset'),
                               ('Direct · Invented Satellite', '€80.00', '39.02% of asset'),
                               ('Invented Regional', '€25.00', '12.20% of asset')]:
        source = preview.get_by_role('listitem').filter(has_text=name)
        playwright.expect(source).to_contain_text(amount)
        playwright.expect(source).to_contain_text(share)
    playwright.expect(preview.get_by_role('table')).to_have_count(0)
    playwright.expect(preview).not_to_contain_text('×')
    playwright.expect(preview.get_by_role('button')).to_have_count(0)
    def assert_tree_alignment():
        boxes = row.evaluate("""el => {
            const headers = [...el.closest('table').querySelector('thead tr').children];
            const index = headers.findIndex(th => th.textContent.includes('Total (EUR)'));
            const parent = el.children[index].getBoundingClientRect();
            const panel = el.nextElementSibling.querySelector('.preview-panel');
            const childElement = panel.querySelector('.tree-amount');
            const child = childElement.getBoundingClientRect();
            const range = document.createRange();range.selectNodeContents(el.children[index]);
            const parentText = range.getBoundingClientRect();
            const childText = childElement.querySelector('span').getBoundingClientRect();
            const decimals = [...panel.querySelectorAll('.tree-amount > span')].map(span => {
                const text = span.firstChild;
                const decimal = text.textContent.lastIndexOf('.');
                const range = document.createRange();range.setStart(text, decimal);range.setEnd(text, decimal + 1);
                return range.getBoundingClientRect().x;
            });
            const branch = child.x + parseFloat(getComputedStyle(childElement, '::before').left);
            const pctIndex = headers.findIndex(th => th.textContent.includes('% of selected portfolio'));
            const pctParent = el.children[pctIndex].getBoundingClientRect();
            const pctChild = panel.querySelector('.tree-share').getBoundingClientRect();
            const first = panel.querySelector('li').getBoundingClientRect();
            const details = el.querySelector('.source-details').getBoundingClientRect();
            const toggle = el.querySelector('[data-preview-toggle]').getBoundingClientRect();
            return {parent: parent.toJSON(), child: child.toJSON(), first: first.toJSON(), details: details.toJSON(), parentText: parentText.toJSON(), childText: childText.toJSON(), branch, pctParent: pctParent.toJSON(), pctChild: pctChild.toJSON(), decimals, toggle: toggle.toJSON()};
        }""")
        assert abs(boxes['parent']['x'] - boxes['child']['x']) < 2
        assert abs(boxes['parent']['right'] - boxes['child']['right']) < 2
        assert abs(boxes['branch'] - boxes['parentText']['x']) < 2
        assert boxes['childText']['x'] - boxes['parentText']['x'] >= 19
        assert max(boxes['decimals']) - min(boxes['decimals']) < 1
        assert abs(boxes['pctParent']['right'] - boxes['pctChild']['right']) < 2
        assert 0 < boxes['details']['x'] - boxes['toggle']['right'] <= 16
        assert boxes['details']['bottom'] <= boxes['parent']['bottom']
        assert abs(boxes['details']['y'] + boxes['details']['height']/2 - boxes['toggle']['y'] - boxes['toggle']['height']/2) < 2
    assert_tree_alignment()
    page.set_viewport_size({'width': 1100, 'height': 1000})
    # ResizeObserver aligns the child branches with the live table columns.
    page.wait_for_timeout(100)
    assert_tree_alignment()
    page.set_viewport_size({'width': 1440, 'height': 1000})
    page.wait_for_timeout(100)
    page.screenshot(path=str(directory / 'exposure-source-preview.png'))
    page.get_by_role('button', name='Hide sources for Invented Satellite', exact=True).click()
    playwright.expect(preview).to_have_count(0)
    playwright.expect(row.get_by_role('button', name='Details for Invented Satellite', exact=True)).not_to_be_visible()
    page.get_by_role('button', name='Show sources for Invented Satellite', exact=True).click()
    playwright.expect(preview).to_be_visible()
    assert not sent, 'Expanding and collapsing must not send Streamlit rerun requests'
    page.get_by_role('button', name='Data & settings', exact=True).click()
    page.get_by_text('Show tickers', exact=True).click()
    page.keyboard.press('Escape')
    playwright.expect(table.get_by_role('columnheader', name='Ticker', exact=True)).to_be_visible()
    playwright.expect(preview).to_be_visible()
    assert_tree_alignment()
    page.set_viewport_size({'width': 700, 'height': 1000})
    page.wait_for_timeout(100)
    assert_tree_alignment()
    page.screenshot(path=str(directory / 'exposure-source-tree-narrow.png'))
    page.set_viewport_size({'width': 1440, 'height': 1000})
    row.get_by_role('button', name='Details for Invented Satellite', exact=True).click()
    dialog = page.get_by_role('dialog')
    sources = dialog.get_by_role('table', name='Exposure sources', exact=True)
    playwright.expect(sources.locator('tbody tr')).to_have_count(3)
    playwright.expect(sources.get_by_role('columnheader', name='Position (EUR)', exact=True)).to_be_visible()
    playwright.expect(sources.get_by_role('columnheader', name='Asset weight (%)', exact=True)).to_be_visible()
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
    playwright.expect(preview).to_be_visible()
    row.get_by_role('button', name='Details for Invented Satellite', exact=True).click()
    playwright.expect(sources).to_be_visible()
    dialog.get_by_role('button', name='Close exposure details').click()
    playwright.expect(dialog).to_have_count(0)
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
    page.get_by_role('textbox', name='Search exposure').fill('Other')
    page.get_by_role('textbox', name='Search exposure').press('Enter')
    playwright.expect(page.get_by_text('2 matching assets', exact=False)).to_be_visible()
    playwright.expect(preview).to_have_count(0)
