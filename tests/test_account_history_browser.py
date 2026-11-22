"""Account history navigation uses applied records without confirmation controls."""
from browser_support import settle, select_tab
from portfolio_app.ledger.account_journal import load_journal
from portfolio_app.performance.history_observations import load_observations
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


def test_cash_row_opens_its_account_from_both_portfolio_views(futures_page):
    from portfolio_app.ledger import account_journal as ledger
    page, root, _, _ = futures_page
    journal, revision = ledger.load_journal(root)
    updated = ledger.add_account(journal, ledger.Account('second', 'Second invented account'))
    updated = ledger.append_event(updated, ledger.Event('opening-usd', '2026-01-02', 'cash_open',
        account_id='second', currency='USD', amount=100))
    updated = ledger.append_event(updated, ledger.Event('opening-eur', '2026-01-02', 'cash_open',
        account_id='second', currency='EUR', amount=50))
    ledger.save_journal(root, updated, revision)
    page.reload()
    for origin, currency in [('Overview', 'EUR'), ('Positions', 'USD')]:
        select_tab(page, origin)
        row = page.locator(f'tr[data-position-id="account-cash:second:{currency}"]')
        row.locator('td').first.click()
        account = page.get_by_role('combobox', name='Cash account', exact=True)
        playwright.expect(account).to_have_value('Second invented account')
        playwright.expect(page.get_by_role('tab', name='Futures', exact=True)).to_have_attribute('aria-selected', 'true')
        action = page.get_by_role('combobox', name='Cash action', exact=True)
        playwright.expect(action).to_have_value('')
        action.click()
        page.get_by_role('option', name='Deposit / withdrawal', exact=True).click()
        playwright.expect(page.get_by_role('combobox', name='Currency', exact=True)).to_have_value(currency)
        page.get_by_role('button', name=f'Back to {origin}', exact=True).click()
        playwright.expect(page.get_by_role('tab', name=origin, exact=True)).to_have_attribute('aria-selected', 'true')
        playwright.expect(row).to_be_visible()
    assert ledger.load_journal(root)[0] == updated
