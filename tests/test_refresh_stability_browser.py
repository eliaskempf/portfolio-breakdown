"""Observe actual timer transitions using invented data and a controlled clock."""
from dataclasses import replace
from datetime import timedelta

from browser_support import settle, select_tab
from portfolio_app.futures_quotes import record_result
from portfolio_app.derivative_types import PriceObservation
from test_futures_freshness_browser import futures_page, playwright, NOW


def renders(root):
    return int((root/'app-renders.txt').read_text())


def set_clock(root, now):
    path = root/'clock.next'
    path.write_text(now.isoformat())
    path.replace(root/'clock.txt')


def test_idle_minutes_retrievals_and_failure_preserve_table_and_focus(futures_page):
    page, root, journal, contract = futures_page
    select_tab(page, 'Positions')
    row = page.locator('tr[data-position-id="investment:synthetic"]')
    summary = row.locator('summary')
    summary.focus(); page.keyboard.press('Enter')
    playwright.expect(row.locator('details')).to_have_attribute('open', '')
    row.evaluate("""el => {
        window.retainedRow=el;
        window.retainedRoot=el.getRootNode();
        window.retainedFocus=window.retainedRoot.activeElement;
        window.retainedScroll=window.scrollY;
    }""")
    count = renders(root)
    # Cross multiple wall-clock minutes; local polling still runs in real time.
    for minutes in (1, 2):
        set_clock(root, NOW+timedelta(minutes=minutes))
        page.wait_for_timeout(2400)
        assert renders(root) == count
        assert page.evaluate('window.retainedRow.isConnected && window.retainedRoot.activeElement===window.retainedFocus')
        assert page.evaluate('window.scrollY===window.retainedScroll')
        playwright.expect(row.locator('details')).to_have_attribute('open', '')
    old = PriceObservation(contract.id, '2026-01-05', 1020, kind='mark',
        source='Invented delayed quote', observed_at=(NOW+timedelta(minutes=2)).isoformat(),
        quote_at=(NOW-timedelta(days=2)).isoformat(), delay_minutes=15)
    record_result(root, contract, old, '', attempted_at=NOW+timedelta(minutes=2), now=NOW+timedelta(minutes=2))
    page.wait_for_timeout(2400)
    assert renders(root) == count
    record_result(root, contract, None, 'Invented second outage',
        attempted_at=NOW+timedelta(minutes=2), now=NOW+timedelta(minutes=2))
    page.wait_for_timeout(2400)
    assert renders(root) == count
    assert page.evaluate('window.retainedRow.isConnected && window.retainedRoot.activeElement===window.retainedFocus')
    page.get_by_role('button', name='Notifications', exact=False).click(); settle(page)
    playwright.expect(page.get_by_test_id('stPopoverBody')).to_contain_text('Futures quote refresh unavailable')
    count = renders(root)
    record_result(root, contract, old, '', attempted_at=NOW+timedelta(minutes=3), now=NOW+timedelta(minutes=3))
    playwright.expect(page.get_by_test_id('stPopoverBody').get_by_text('Futures quote refresh unavailable', exact=False)).to_have_count(0, timeout=10000)
    assert renders(root) == count
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


def test_freshness_boundary_updates_locally_and_new_price_waits_for_dialog_close(futures_page):
    page, root, journal, contract = futures_page
    now = NOW
    quote = PriceObservation(contract.id, '2026-01-07', 990, kind='mark',
        source='Invented live observation', quote_at=(now-timedelta(minutes=29)).isoformat())
    record_result(root, contract, quote, '', attempted_at=now, now=now)
    page.get_by_role('button', name='Refresh prices').click(); settle(page)
    select_tab(page, 'Positions')
    row = page.locator('tr[data-position-id="investment:synthetic"]')
    playwright.expect(row.locator('td.negative')).to_have_count(1)
    count = renders(root)
    row.evaluate('el => window.retainedRow=el')
    set_clock(root, now+timedelta(minutes=2))
    playwright.expect(row.locator('summary')).to_contain_text('Stale', timeout=10000)
    playwright.expect(row.locator('td.negative')).to_have_count(0)
    assert renders(root) == count
    assert page.evaluate('window.retainedRow.isConnected')
    row.locator('td').first.click()
    dialog = page.get_by_role('dialog'); playwright.expect(dialog).to_be_visible(); settle(page)
    playwright.expect(dialog.locator('.js-plotly-plot')).to_have_count(1)
    page.evaluate('window.retainedChart=document.querySelector(\'[role="dialog"] .js-plotly-plot\')')
    before = dialog.get_by_test_id('stMetricValue').first.inner_text()
    count = renders(root)
    later = now+timedelta(minutes=3)
    set_clock(root, later)
    record_result(root, contract, replace(quote, price=980, quote_at=later.isoformat()),
                  '', attempted_at=later, now=later)
    page.wait_for_timeout(2600)
    assert renders(root) == count
    assert page.evaluate('window.retainedChart.isConnected')
    assert dialog.get_by_test_id('stMetricValue').first.inner_text() == before
    playwright.expect(dialog.get_by_text('Stale · 32m', exact=True)).to_be_visible()
    dialog.get_by_role('button', name='Close', exact=True).filter(has_text='Close').click(); settle(page)
    playwright.expect(page.get_by_role('dialog')).to_have_count(0)
    playwright.expect(row.locator('summary')).to_have_count(0, timeout=10000)
    playwright.expect(row.locator('td.negative')).to_have_count(1)
    assert renders(root) > count
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


def test_session_transitions_and_unsaved_editor_survive_ticks(futures_page):
    page, root, journal, contract = futures_page
    from datetime import datetime
    friday = datetime.fromisoformat('2026-01-09T20:50:00+00:00')
    quote = PriceObservation(contract.id, '2026-01-09', 990, kind='mark',
        source='Invented Friday quote', quote_at='2026-01-09T20:45:00+00:00')
    set_clock(root, friday)
    record_result(root, contract, quote, '', attempted_at=friday, now=friday)
    page.get_by_role('button', name='Refresh prices').click(); settle(page)
    row = page.locator('tr[data-position-id="investment:synthetic"]')
    playwright.expect(row.locator('td.negative')).to_have_count(2)
    count = renders(root)
    # Quote stays current through the closed weekend, then ages after reopening.
    set_clock(root, datetime.fromisoformat('2026-01-10T12:00:00+00:00'))
    playwright.expect(row.locator('summary')).to_have_attribute('aria-label', 'Valuation details', timeout=10000)
    playwright.expect(row.locator('summary')).to_have_attribute('title', 'Market closed · Latest available quote')
    playwright.expect(row.locator('td.negative')).to_have_count(2)
    set_clock(root, datetime.fromisoformat('2026-01-12T00:20:00+00:00'))
    playwright.expect(row.locator('summary')).to_have_count(0, timeout=10000)
    set_clock(root, datetime.fromisoformat('2026-01-12T00:26:00+00:00'))
    playwright.expect(row.locator('summary')).to_contain_text('Stale', timeout=10000)
    playwright.expect(row.locator('td.negative')).to_have_count(0)
    assert renders(root) == count
    row.locator('td').first.click(); settle(page)
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('button', name='Record transaction', exact=True).click(); settle(page)
    amount = dialog.get_by_role('textbox', name='Settlement price', exact=True)
    amount.fill('1234.56')
    amount.evaluate('el => {window.unsavedField=el;window.editorDialog=el.closest(\'[role="dialog"]\');}')
    before = dialog.bounding_box()
    count = renders(root)
    set_clock(root, datetime.fromisoformat('2026-01-12T00:28:00+00:00'))
    page.wait_for_timeout(2600)
    assert renders(root) == count
    assert page.evaluate('window.unsavedField.isConnected && document.activeElement===window.unsavedField && window.editorDialog.isConnected')
    playwright.expect(amount).to_have_value('1234.56')
    after = dialog.bounding_box()
    assert all(abs(before[key]-after[key]) < 2 for key in ('x', 'y', 'width', 'height'))
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


def test_overview_metrics_and_category_qualification_update_without_replacing_charts(futures_page):
    page, root, journal, contract = futures_page
    quote = PriceObservation(contract.id, '2026-01-07', 990, kind='mark',
        source='Invented boundary quote', quote_at=(NOW-timedelta(minutes=29)).isoformat())
    record_result(root, contract, quote, '', attempted_at=NOW, now=NOW)
    page.get_by_role('button', name='Refresh prices').click(); settle(page)
    select_tab(page, 'Overview')
    value = page.locator('.st-key-overview_value')
    chart = page.locator('.js-plotly-plot').first
    chart.evaluate('el => window.allocationChart=el')
    count = renders(root)
    set_clock(root, NOW+timedelta(minutes=2))
    playwright.expect(value).to_contain_text('1 dated futures valuation', timeout=10000)
    assert renders(root) == count
    assert page.evaluate('window.allocationChart.isConnected')
    page.get_by_role('radio', name='Performance', exact=True).click(); settle(page)
    chart = page.locator('.st-key-category_performance_chart .js-plotly-plot')
    playwright.expect(chart).to_have_count(1)
    chart.evaluate('el => window.comparisonChart=el')
    values = chart.evaluate('el => JSON.stringify(el.data[0].x)')
    count = renders(root)
    set_clock(root, NOW)
    page.wait_for_function("getComputedStyle(window.comparisonChart.querySelector('.barlayer .point path')).fill !== 'rgb(128, 136, 144)'")
    set_clock(root, NOW+timedelta(minutes=2))
    page.wait_for_function("getComputedStyle(window.comparisonChart.querySelector('.barlayer .point path')).fill === 'rgb(128, 136, 144)'")
    assert renders(root) == count
    assert page.evaluate('window.comparisonChart.isConnected')
    assert chart.evaluate('el => JSON.stringify(el.data[0].x)') == values
