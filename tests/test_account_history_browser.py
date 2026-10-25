"""Real dialog/account navigation with synthetic futures only."""
from browser_support import settle, select_tab
from portfolio_app.account_journal import load_journal
from portfolio_app.history_observations import HistoryScope, load_observations
from test_futures_freshness_browser import futures_page, playwright  # noqa: F401


def test_confirm_in_contract_history_and_revisit_account_workspace(futures_page):
    page, root, journal, _ = futures_page
    select_tab(page, 'Positions')
    row = page.locator('tr[data-position-id="investment:synthetic"]')
    row.locator('td').first.click(); settle(page)
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('radio', name='Contract history', exact=True).click(); settle(page)
    dialog.get_by_text('Review account history completeness', exact=True).click(); settle(page)
    playwright.expect(dialog.get_by_text('Account: Invented account', exact=True)).to_be_visible()
    confirm = dialog.get_by_role('button', name='Confirm complete history', exact=True)
    playwright.expect(confirm).to_be_visible()
    assert not load_observations(root)[0].reviews
    confirm.click(); settle(page)
    playwright.expect(dialog).to_be_visible()
    playwright.expect(dialog.get_by_text('History confirmed', exact=False)).to_be_visible()
    store, _ = load_observations(root)
    assert len(store.reviews) == 1 and store.reviews[0].scope == HistoryScope(accounts=('a',))
    assert load_journal(root)[0] == journal
    dialog.get_by_role('button', name='Close', exact=True).first.click(); settle(page)
    select_tab(page, 'Futures')
    page.get_by_role('radio', name='Positions & transactions', exact=True).click(); settle(page)
    page.get_by_text('Review account history completeness', exact=True).click(); settle(page)
    playwright.expect(page.get_by_text('History confirmed', exact=False)).to_be_visible()
    playwright.expect(page.get_by_role('button', name='Confirm complete history', exact=True)).to_have_count(0)
    assert load_journal(root)[0] == journal
