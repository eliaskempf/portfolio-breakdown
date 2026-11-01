"""Large synthetic portfolios and real tour/Exposure interactions."""
from dataclasses import asdict
import json
from time import perf_counter

import pytest
import yaml

from portfolio_app.futures import FuturePosition
from portfolio_app.futures_workspace import SANDBOX_FILE
from test_ux_browser import ux_page, playwright  # noqa: F401
from browser_support import select_tab, settle


def large_snapshot(directory, count):
    holdings = directory / 'holdings.csv'
    lines = holdings.read_text().splitlines()
    if 'instrument_type' not in lines[0]:
        holdings.write_text(lines[0] + ',instrument_type,exposure_kind\n' + '\n'.join(
            line + (',etf,equity' if 'SYNTH-A' in line or 'SYNTH-B' in line else ',equity,equity')
            for line in lines[1:]) + '\n')
    funds = directory / 'etfs'
    funds.mkdir(exist_ok=True)
    rows = ['constituent_id,name,ticker,isin,weight,instrument_type,sector,country']
    rows += [f'synthetic-{i},Invented constituent {i:05d},SYNTH-{i},,{1/count},equity,Industry {i % 10},Germany'
             for i in range(count)]
    (funds / 'invented.csv').write_text('\n'.join(rows) + '\n')
    (funds / 'invented.yaml').write_text(yaml.safe_dump(dict(fund_id='invented', name='Invented broad fund',
        isin='ZZ9999999999', tickers=['SYNTH-A'], as_of='2026-09-01', source='Synthetic',
        equity_fund=True, holdings_file='invented.csv')))
    position = FuturePosition(entry_price=5., mark_price=5., bucket_id='core', proxy_position_id='fund-a')
    (directory / SANDBOX_FILE).write_text(json.dumps(dict(version=2, simulation=True,
        instrument='FMWN', position=asdict(position))))


@pytest.mark.parametrize('count', [1000, 3000, 10000])
def test_large_exposure_paging_search_and_bounded_charts(ux_page, count):
    page, directory = ux_page
    page.set_default_timeout(30000)
    large_snapshot(directory, count)
    page.reload()
    settle(page)
    start = perf_counter()
    select_tab(page, 'Exposure')
    table = page.get_by_role('table', name='Exposure assets', exact=True)
    playwright.expect(table).to_be_visible()
    settle(page)
    cold = perf_counter() - start
    assert table.locator('tbody tr[data-position-id]').count() == 100
    playwright.expect(page.locator('.st-key-exposure_results .js-plotly-plot')).to_have_count(0)
    pages = page.get_by_role('navigation', name='Table pages', exact=True)
    page.get_by_role('button', name='Next', exact=True).click()
    playwright.expect(pages).to_contain_text('Page 2 of')
    table.get_by_role('button', name='Asset', exact=False).first.click()
    playwright.expect(pages).to_contain_text('Page 1 of')
    select_tab(page, 'Positions')
    start = perf_counter()
    select_tab(page, 'Exposure')
    playwright.expect(table).to_be_visible()
    settle(page)
    warm = perf_counter() - start
    print(f'\nSynthetic companies={count}: cold={cold:.3f}s warm={warm:.3f}s')
    search = page.get_by_role('textbox', name='Search exposure', exact=True)
    search.fill(f'Invented constituent {count-1:05d}')
    search.press('Enter')
    playwright.expect(table.locator('tbody tr[data-position-id]')).to_have_count(1)
    playwright.expect(table).to_contain_text(f'Invented constituent {count-1:05d}')
    search.fill(''); search.press('Enter'); settle(page)
    page.get_by_role('radio', name='Themes & sectors', exact=True).click()
    playwright.expect(page.get_by_role('radio', name='Themes & sectors', exact=True)).to_be_checked()
    settle(page)
    chart = page.locator('.st-key-exposure_results .js-plotly-plot').first
    playwright.expect(chart).to_be_visible()
    assert chart.evaluate('el => el.data[0].ids.length') < 100
    page.get_by_role('radio', name='Assets', exact=True).click()
    playwright.expect(page.get_by_role('radio', name='Assets', exact=True)).to_be_checked()
    settle(page)
    page.get_by_role('button', name='Data & settings', exact=True).click()
    show_chart = page.get_by_role('checkbox', name='Show largest exposures chart', exact=True)
    playwright.expect(show_chart).not_to_be_checked()
    page.get_by_text('Show largest exposures chart', exact=True).click()
    playwright.expect(show_chart).to_be_checked()
    settle(page)
    page.keyboard.press('Escape')
    settle(page)
    playwright.expect(chart).to_be_visible()
    assert chart.evaluate('el => el.data[0].y.length') == 30
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


@pytest.mark.parametrize('measure', ['Market exposure', 'Funded value'])
def test_tour_restores_futures_exposure_controls(ux_page, measure):
    page, directory = ux_page
    large_snapshot(directory, 20)
    page.reload()
    select_tab(page, 'Exposure')
    page.get_by_role('button', name='Data & settings', exact=True).click()
    selector = page.get_by_role('combobox', name='Analysis measure', exact=True)
    selector.click()
    page.get_by_role('option', name=measure, exact=True).click()
    page.keyboard.press('Escape')
    search = page.get_by_role('textbox', name='Search exposure', exact=True)
    search.fill('Invented'); search.press('Enter'); settle(page)
    page.get_by_text('Look through funds & futures', exact=True).click()
    settle(page)
    before = {p: p.read_bytes() for p in directory.iterdir() if p.suffix in {'.json', '.yaml', '.csv'}}
    page.get_by_role('button', name='?', exact=True).click()
    page.get_by_role('button', name='Take the tour', exact=True).click()
    card = page.locator('.st-key-app_tour')
    playwright.expect(card).to_be_visible()
    card.get_by_role('button', name='Skip to Exposure →', exact=True).click()
    for _ in range(4):
        overlay = page.locator('.portfolio-tour-overlay')
        playwright.expect(overlay).to_have_attribute('data-ready', 'true')
        settle(page)
        playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
        card.get_by_role('button', name='Next', exact=True).click()
    page.keyboard.press('Escape')
    playwright.expect(card).to_have_count(0)
    playwright.expect(search).to_have_value('Invented')
    playwright.expect(page.get_by_role('switch', name='Look through funds & futures', exact=True)).not_to_be_checked()
    page.get_by_role('button', name='Data & settings', exact=True).click()
    playwright.expect(selector).to_have_value(measure)
    assert all(p.read_bytes() == contents for p, contents in before.items())
