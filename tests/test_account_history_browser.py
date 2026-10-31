"""Account history navigation uses applied records without confirmation controls."""
from browser_support import settle, select_tab
from portfolio_app.account_journal import load_journal
from portfolio_app.history_observations import load_observations
from test_futures_freshness_browser import futures_page, playwright  # noqa: F401


def test_contract_history_and_account_workspace_have_no_confirmation_step(futures_page):
    page, root, journal, _ = futures_page
    before = load_observations(root)[0]
    select_tab(page, 'Positions')
    row = page.locator('tr[data-position-id="investment:synthetic"]')
    row.locator('td').first.click(); settle(page)
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('radio', name='Contract history', exact=True).click(); settle(page)
    playwright.expect(dialog.get_by_text('Review account history completeness', exact=True)).to_have_count(0)
    playwright.expect(dialog.get_by_role('button', name='Confirm complete history', exact=True)).to_have_count(0)
    dialog.get_by_role('button', name='Close', exact=True).first.click(); settle(page)
    select_tab(page, 'Futures')
    page.get_by_role('radio', name='Positions & transactions', exact=True).click(); settle(page)
    playwright.expect(page.get_by_text('Review account history completeness', exact=True)).to_have_count(0)
    playwright.expect(page.get_by_role('button', name='Confirm complete history', exact=True)).to_have_count(0)
    assert load_journal(root)[0] == journal
    assert load_observations(root)[0] == before
