"""History checklist navigation with invented holdings and offline evidence."""
from datetime import date
from decimal import Decimal

from browser_support import select_tab
from test_ux_browser import ux_page, playwright  # noqa: F401


def open_history(page):
    select_tab(page, 'Positions')
    page.get_by_role('radiogroup', name='Position view', exact=True).get_by_role('radio', name='History', exact=True).click()
    table = page.get_by_role('table', name='History status', exact=True)
    playwright.expect(table).to_be_visible()
    return table


def test_missing_history_list_opens_empty_transactions(ux_page):
    page, _ = ux_page
    table = open_history(page)
    playwright.expect(table.locator('tbody tr')).to_have_count(3)
    playwright.expect(table.get_by_text('No history', exact=True)).to_have_count(3)
    table.locator('tr[data-position-id="fund-a"] td').first.click()
    dialog = page.get_by_role('dialog')
    playwright.expect(dialog.get_by_role('radio', name='Transactions', exact=True)).to_be_checked()
    playwright.expect(dialog.get_by_role('button', name='Add buy', exact=False)).to_be_visible()
    assert page.get_by_test_id('stException').count() == 0


def test_history_filter_and_review_navigation(ux_page):
    from portfolio_app.account_journal import Journal, save_journal, load_journal
    from portfolio_app.history_observations import CoverageReview, HistoryScope, Observation, ObservationStore, save_observations
    from portfolio_app.security_history import SecurityBook, SecurityEvent, history_fingerprint
    page, root = ux_page
    start, end = '2024-01-01', date.today().isoformat()
    books = tuple(SecurityBook(identity, instrument, (str(shares), str(cost), 'EUR'),
        records=(SecurityEvent(identity+'-opening', 'opening', start, quantity=shares, amount=shares*cost, currency='EUR'),),
        active_ids=(identity+'-opening',)) for identity, instrument, shares, cost in [('fund-a', 'a', 2, 80), ('fund-b', 'b', 1, 60)])
    journal = Journal(version=2, securities=books)
    save_journal(root, journal, None)
    observations = tuple(Observation(identity+stamp, 'price', identity, stamp, Decimal(price), 'EUR')
                         for identity, price in [('fund-a', 100), ('fund-b', 70)] for stamp in (start, end))
    store = ObservationStore(observations, (CoverageReview('review', HistoryScope(('fund-a',)), start, end, history_fingerprint(journal)),))
    save_observations(root, store, None)
    page.reload(); page.locator('.js-plotly-plot').first.wait_for()
    table = open_history(page)
    playwright.expect(table.locator('tr[data-position-id="fund-a"]')).to_have_count(0)
    playwright.expect(table.locator('tr[data-position-id="fund-b"]')).to_contain_text('Not confirmed')
    checkbox = page.get_by_role('checkbox', name='Only show positions needing attention')
    checkbox.press('Space')
    playwright.expect(table.locator('tr[data-position-id="fund-a"]')).to_contain_text('Confirmed')
    table.locator('tr[data-position-id="fund-b"] td').first.click()
    dialog = page.get_by_role('dialog')
    playwright.expect(dialog.get_by_role('radio', name='Transactions', exact=True)).to_be_checked()
    playwright.expect(dialog.get_by_role('button', name='Apply and confirm history', exact=True)).to_be_visible()
    assert load_journal(root)[0] == journal  # Navigation never confirms or applies data.
    assert page.get_by_test_id('stException').count() == 0
