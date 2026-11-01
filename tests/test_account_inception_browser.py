"""New account funding through the actual app, using only invented accounts."""
from browser_support import settle, select_tab
from portfolio_app.account_journal import load_journal, replay
from portfolio_app.history_observations import HistoryScope, ObservationStore
from portfolio_app.personal_performance import value_at
from test_futures_freshness_browser import futures_page, playwright  # noqa: F401


def test_newly_funded_account_can_be_reviewed_saved_and_reopened(futures_page):
    page, root, original, _ = futures_page
    select_tab(page, 'Futures')
    page.get_by_role('radio', name='Cash & financing', exact=True).click()
    settle(page)
    select = page.get_by_test_id('stSelectbox').filter(has_text='Cash action').get_by_role('combobox')
    select.click()
    page.get_by_role('option', name='Create account', exact=True).click()
    settle(page)
    name = page.get_by_role('textbox', name='Account name', exact=True)
    name.fill('Invented browser funding'); name.press('Enter'); settle(page)
    page.get_by_text('Newly funded account', exact=True).click()
    settle(page)
    amount = page.get_by_role('textbox', name='Initial contribution (EUR)', exact=True)
    amount.fill('500'); amount.press('Enter'); settle(page)
    page.get_by_role('button', name='Review changes', exact=True).click(); settle(page)
    page.get_by_role('button', name='Save reviewed changes', exact=True).click(); settle(page)
    saved, _ = load_journal(root)
    assert saved.events[:-2] == original.events
    zero, funding = saved.events[-2:]
    assert zero.source == 'confirmed_zero_start' and zero.amount == 0
    assert funding.kind == 'cash_flow' and funding.amount == 500
    assert replay(saved).cash[funding.account_id, 'EUR'] == 500
    scope = HistoryScope(accounts=(funding.account_id,))
    assert value_at(saved, scope, ObservationStore(), '2020-01-01', 'EUR').amount == 0
    page.reload(); settle(page)
    select_tab(page, 'Futures')
    assert load_journal(root)[0] == saved
    assert page.get_by_test_id('stException').count() == 0
