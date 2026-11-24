"""Account history navigation uses applied records without confirmation controls."""
from browser_support import settle, select_tab
from portfolio_app.ledger.account_journal import load_journal
from portfolio_app.performance.history_observations import load_observations
from test_futures_freshness_browser import futures_page, playwright  # noqa: F401


def test_cash_account_draft_resumes_and_saves_once(futures_page):
    page, root, original, _ = futures_page
    select_tab(page, 'Positions')
    page.get_by_role('button', name='Add position', exact=True).click()
    dialog = page.get_by_role('dialog')
    dialog.get_by_text('Cash account', exact=True).click()
    name = dialog.get_by_role('textbox', name='Account name')
    opening = dialog.get_by_role('spinbutton', name='Opening balance (EUR)')
    name.fill('Invented draft savings')
    name.press('Tab')
    settle(page)
    opening.fill('1234.56')
    opening.press('Tab')
    settle(page)
    page.keyboard.press('Escape')
    playwright.expect(dialog).to_have_count(0)
    page.get_by_role('button', name='Resume unsaved edit').click()
    playwright.expect(name).to_have_value('Invented draft savings')
    playwright.expect(opening).to_have_value('1234.56')
    assert load_journal(root)[0] == original
    dialog.get_by_role('button', name='Save cash account').click()
    playwright.expect(dialog).to_have_count(0)
    saved = load_journal(root)[0]
    assert len(saved.accounts) == len(original.accounts) + 1
    account, = [account for account in saved.accounts if account.name == 'Invented draft savings']
    event, = [event for event in saved.events if event.account_id == account.id]
    assert event.kind == 'cash_open' and str(event.amount) == '1234.56'


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


def test_named_savings_opens_its_statement_alongside_a_futures_investment(futures_page):
    from test_bank_accounts import bank, save
    page, root, original, _ = futures_page
    save(root, bank('reserve'), opening=250, day='2026-01-02')
    page.reload()
    select_tab(page, 'Positions')
    page.locator('tr[data-position-id="account-cash:reserve:EUR"]').locator('td').first.click()
    dialog = page.get_by_role('dialog')
    playwright.expect(dialog.get_by_text('€250.00', exact=True)).to_be_visible()
    dialog.get_by_role('button', name='Record transaction').click()
    amount = dialog.get_by_role('spinbutton', name='Amount (EUR)')
    amount.fill('50')
    dialog.get_by_role('button', name='Save transaction', exact=True).click()
    playwright.expect(dialog.get_by_text('€300.00', exact=True)).to_be_visible()
    def open_deposit(expected_amount):
        row = dialog.get_by_role('table', name='Cash account statement').get_by_role('row').filter(has_text='Deposit')
        playwright.expect(row.get_by_role('cell', name=expected_amount, exact=True)).to_be_visible()
        settle(page)
        row.press('Enter')
        playwright.expect(dialog.get_by_text('Transaction details', exact=True)).to_be_visible()

    open_deposit('+50.00')
    dialog.get_by_role('button', name='Correct entry').click()
    corrected = dialog.get_by_role('textbox', name='Corrected amount')
    corrected.fill('60')
    corrected.press('Tab')
    settle(page)
    dialog.get_by_role('button', name='Review correction').click()
    dialog.get_by_role('button', name='Save correction').click()
    playwright.expect(dialog.get_by_text('€310.00', exact=True)).to_be_visible()
    open_deposit('+60.00')
    dialog.get_by_role('button', name='Reverse only').click()
    dialog.get_by_role('button', name='Review reversal').click()
    dialog.get_by_role('button', name='Save reversal').click()
    playwright.expect(dialog.get_by_text('€250.00', exact=True)).to_be_visible()
    dialog.get_by_role('button', name='Edit cash account').click()
    dialog.get_by_test_id('stDateInput').get_by_role('spinbutton').first.click()
    page.get_by_role('grid').get_by_role('button', name='Thursday, January 1, 2026', exact=True).click()
    amount = dialog.get_by_role('spinbutton', name='Opening balance (EUR)')
    amount.fill('280')
    amount.press('Tab')
    settle(page)
    dialog.get_by_role('button', name='Save cash account').click()
    playwright.expect(dialog.get_by_text('€280.00', exact=True)).to_be_visible()
    from portfolio_app.ledger.account_journal import effective_events
    opening, = [e for e in effective_events(load_journal(root)[0]) if e.account_id == 'reserve' and e.kind == 'cash_open']
    assert opening.date == '2026-01-01' and opening.amount == 280
    page.keyboard.press('Escape')
    select_tab(page, 'Futures')
    page.get_by_role('radio', name='Collateral', exact=True).click()
    playwright.expect(page.get_by_role('combobox', name='Collateral account')).to_have_value(original.accounts[0].name)
    assert load_journal(root)[0].investments == original.investments
