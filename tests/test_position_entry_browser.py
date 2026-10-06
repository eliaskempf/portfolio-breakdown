"""Real-browser position entry using only synthetic workspaces and offline search."""
import json

from test_ux_browser import playwright, ux_page  # noqa: F401


def settle(page):
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


def add_manually(page):
    page.get_by_role('tab', name='Positions', exact=True).click()
    page.get_by_role('button', name='add Add position', exact=True).click()
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('button', name='Enter manually', exact=True).click()
    return dialog


def enter(dialog, label, value):
    field = dialog.get_by_role('textbox', name=label, exact=True)
    field.fill(value)
    field.press('Tab')


def test_linked_entry_layout_tooltips_and_save(ux_page):
    page, directory = ux_page
    dialog = add_manually(page)
    quantity = dialog.get_by_role('textbox', name='Quantity held (total)', exact=True)
    playwright.expect(quantity).to_have_value('')
    currency = dialog.get_by_role('combobox', name='Buy-in currency', exact=True)
    playwright.expect(currency).to_be_visible()
    currency.click()
    playwright.expect(page.get_by_role('option')).to_have_count(1)
    playwright.expect(page.get_by_role('option', name='EUR', exact=True)).to_be_visible()
    page.keyboard.press('Escape')
    enter(dialog, 'Instrument name', 'Invented browser addition')
    enter(dialog, 'Quantity held (total)', '2.5')
    enter(dialog, 'Average buy-in per unit (optional)', '12.34')
    total = dialog.get_by_role('textbox', name='Total buy-in (optional)', exact=True)
    playwright.expect(total).to_have_value('30.85')
    enter(dialog, 'Total buy-in (optional)', '100')
    # Wait for the cost callback before editing the dependent quantity.
    playwright.expect(dialog.get_by_role('textbox', name='Average buy-in per unit (optional)', exact=True)).to_have_value('40')
    enter(dialog, 'Quantity held (total)', '4')
    playwright.expect(dialog.get_by_role('textbox', name='Average buy-in per unit (optional)', exact=True)).to_have_value('25')
    category = dialog.get_by_role('combobox', name='Category', exact=True)
    target = dialog.get_by_role('spinbutton', name='Target within category % (optional)', exact=True)
    assert abs(category.bounding_box()['y'] - target.bounding_box()['y']) < 6
    for width in (620, 390):
        page.set_viewport_size({'width': width, 'height': 950})
        assert dialog.evaluate('el => el.scrollWidth <= el.clientWidth + 1')
    page.set_viewport_size({'width': 1440, 'height': 1000})
    dialog.get_by_role('button', name='Help for Quantity held (total)', exact=True).focus()
    playwright.expect(page.get_by_role('tooltip').filter(has_text='Total units currently held')).to_be_visible()
    page.keyboard.press('Escape')
    # Escape on a help tooltip must not discard the draft if the dialog closes.
    if dialog.count() == 0:
        page.get_by_role('button', name='Resume unsaved edit', exact=True).click()
    dialog.get_by_role('button', name='Save position', exact=True).click()
    playwright.expect(dialog).to_have_count(0)
    from portfolio_app.holdings import load_holdings
    saved = load_holdings(directory / 'holdings.csv').iloc[-1]
    assert saved['name'] == 'Invented browser addition' and saved.shares == 4
    assert saved.acquisition_price == 25
    settle(page)


def test_purchase_table_paste_and_draft_resume(ux_page):
    page, directory = ux_page
    dialog = add_manually(page)
    enter(dialog, 'Instrument name', 'Invented purchase rows')
    dialog.get_by_text('More details', exact=True).click()
    dialog.get_by_text('Calculate from purchases', exact=True).click()
    canvas = dialog.get_by_test_id('stDataFrame').locator('canvas').first
    canvas.scroll_into_view_if_needed()
    bounds = canvas.bounding_box()
    page.mouse.click(bounds['x'] + 75, bounds['y'] + 48)
    page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
    page.evaluate("navigator.clipboard.writeText('2026-01-01\\t2\\t100\\t1\\n2026-02-01\\t3\\t120\\t2')")
    page.keyboard.press('Control+V')
    total = dialog.get_by_role('textbox', name='Total buy-in (optional)', exact=True)
    playwright.expect(total).to_have_value('563')
    quantity = dialog.get_by_role('textbox', name='Quantity held (total)', exact=True)
    playwright.expect(quantity).to_have_value('5')
    playwright.expect(quantity).to_be_disabled()
    # Explicit dialog dismissal retains the rendered table draft.
    dialog.get_by_role('button', name='Close', exact=True).click()
    page.get_by_role('button', name='Resume unsaved edit', exact=True).click()
    playwright.expect(total).to_have_value('563')
    dialog.get_by_role('button', name='Save position', exact=True).click()
    playwright.expect(dialog).to_have_count(0)
    from portfolio_app.holdings import load_holdings
    saved = load_holdings(directory / 'holdings.csv').iloc[-1]
    assert saved.shares == 5 and saved.acquisition_price == 112.6
    assert len(json.loads(saved.purchase_history)[0]['purchases']) == 2
    settle(page)


def test_search_selection_and_settings_help(ux_page):
    page, _ = ux_page
    table = page.get_by_role('table', name='Allocation', exact=True)
    header = table.get_by_role('button', name='Target (%)', exact=True)
    header.focus()
    playwright.expect(table.locator('.header-help').filter(has_text='Desired allocation')).to_be_visible()
    assert 'portfolio' in header.get_attribute('aria-description').lower()
    page.get_by_role('button', name='tune Portfolio settings', exact=True).click()
    playwright.expect(page.get_by_text('Appearance: choose Light, Dark or System from the top-right app menu.', exact=True)).to_be_visible()
    page.keyboard.press('Escape')
    page.get_by_role('button', name='Main menu', exact=True).click()
    playwright.expect(page.get_by_text('Clear cache', exact=True)).to_have_count(0)
    page.keyboard.press('Escape')
    page.get_by_role('tab', name='Positions', exact=True).click()
    page.get_by_role('button', name='add Add position', exact=True).click()
    dialog = page.get_by_role('dialog')
    for suggestion in ('VanEck', 'Semiconductors', 'NVIDIA'):
        playwright.expect(dialog.get_by_role('button', name=suggestion, exact=True)).to_have_count(0)
    search = dialog.get_by_role('searchbox', name='Find an investment', exact=True)
    search.fill('IE00BMC38736')
    chooser = dialog.locator('summary').filter(has_text='Choose listing')
    playwright.expect(chooser).to_have_count(1)
    search.press('ArrowDown')
    playwright.expect(chooser).to_be_focused()
    chooser.press('Enter')
    choice = dialog.get_by_role('button', name='Select VVSM.DE on Xetra', exact=True)
    playwright.expect(choice).to_be_visible()
    choice.focus()
    choice.press('Enter')
    playwright.expect(dialog.get_by_role('button', name='Change investment', exact=True)).to_be_visible()
    playwright.expect(search).to_have_count(0)
    name = dialog.get_by_role('textbox', name='Instrument name', exact=True)
    ticker = dialog.get_by_role('textbox', name='Ticker', exact=True)
    playwright.expect(name).to_have_value('VanEck Semiconductor UCITS ETF')
    playwright.expect(name).to_be_editable()
    playwright.expect(ticker).to_have_value('VVSM.DE')
    playwright.expect(ticker).to_be_disabled()
    assert abs(name.bounding_box()['y'] - ticker.bounding_box()['y']) < 6
    playwright.expect(dialog.get_by_text('Investment details', exact=True)).to_have_count(0)
    playwright.expect(dialog.get_by_text('VanEck Semiconductor UCITS ETF', exact=True)).to_have_count(0)
    enter(dialog, 'Instrument name', 'Invented display name')
    playwright.expect(name).to_have_value('Invented display name')
    dialog.get_by_role('button', name='Close', exact=True).click()
    page.get_by_role('button', name='Resume unsaved edit', exact=True).click()
    playwright.expect(name).to_have_value('Invented display name')
    playwright.expect(ticker).to_have_value('VVSM.DE')
    dialog.get_by_text('More details', exact=True).click()
    isin = dialog.get_by_role('textbox', name='ISIN (optional)', exact=True)
    playwright.expect(isin).to_have_value('IE00BMC38736')
    playwright.expect(isin).to_be_disabled()
    settle(page)
