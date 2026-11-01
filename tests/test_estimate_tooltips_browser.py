"""Estimated synthetic flows retain context in gain/return tooltips only."""
from dataclasses import replace
from datetime import date, timedelta
import re

from portfolio_app.account_journal import Journal, save_journal
from portfolio_app.history_commands import book_for
from portfolio_app.history_observations import Observation, ObservationStore, save_observations
from portfolio_app.positions import read_snapshot
from portfolio_app.security_history import SecurityEvent
from browser_support import settle
from test_ux_browser import ux_page, playwright


def test_gain_and_twr_estimates_survive_toggle_and_chart_hover_in_tooltips(ux_page):
    page, root = ux_page
    today = date.today()
    start = today-timedelta(days=40)
    rows = read_snapshot(root/'holdings.csv').holdings
    books, observations = [], []
    for _, row in rows.iterrows():
        events = (
            SecurityEvent(row.position_id+'-open', 'opening', start.isoformat(),
                quantity=str(row.shares), amount=str(row.shares*row.acquisition_price), currency='EUR'),
            SecurityEvent(row.position_id+'-fee', 'fee', (today-timedelta(days=5)).isoformat(),
                amount=1, currency='EUR', estimated=True, charges_known=True))
        books.append(replace(book_for(Journal(), row), records=events, active_ids=tuple(e.id for e in events)))
        observations.extend(Observation(row.position_id+str(offset), 'price', row.position_id,
            (start+timedelta(days=offset)).isoformat(), 80+offset, 'EUR') for offset in range(41))
    save_journal(root, Journal(version=5, securities=tuple(books)), None)
    save_observations(root, ObservationStore(tuple(observations)), None)
    page.reload()
    page.get_by_role('radio', name='Performance', exact=True).wait_for(); settle(page)
    page.get_by_role('radio', name='1M', exact=True).click(); settle(page)
    badge = page.get_by_test_id('stMetricDelta')
    playwright.expect(badge).to_have_attribute('title', re.compile('Gain.*estimates'))
    playwright.expect(page.get_by_text('Estimated gain', exact=True)).to_have_count(0)
    chart = page.locator('.js-plotly-plot').first
    def hover():
        chart.scroll_into_view_if_needed()
        point = chart.evaluate('el=>{const b=el.getBoundingClientRect(),l=el._fullLayout;return {x:b.x+l.xaxis._offset+l.xaxis._length*.8,y:b.y+l.yaxis._offset+10}}')
        page.mouse.move(point['x'], point['y'])
        playwright.expect(chart.locator('.hoverlayer')).to_contain_text('Value:')
    hover()
    playwright.expect(badge).to_have_attribute('title', re.compile('Gain.*estimates'))
    page.mouse.move(5, 5)
    badge.click(); settle(page)
    playwright.expect(badge).to_have_attribute('title', re.compile('Estimated TWR'))
    playwright.expect(page.get_by_text('Estimated TWR', exact=True)).to_have_count(0)
    hover()
    playwright.expect(badge).to_have_attribute('title', re.compile('Estimated TWR'))
    page.mouse.move(5, 5)
    page.get_by_role('radio', name='Allocation', exact=True).click(); settle(page)
    allocation = page.locator('.st-key-overview_value').get_by_test_id('stMetricDelta')
    playwright.expect(allocation).to_have_attribute('title', re.compile('Estimated TWR'))
    allocation.click(); settle(page)
    playwright.expect(allocation).to_have_attribute('title', re.compile('gain.*estimates'))
    playwright.expect(page.get_by_text('Estimated gain', exact=True)).to_have_count(0)
