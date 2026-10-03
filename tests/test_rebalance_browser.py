"""Rendered planning and target editing with invented, offline inputs."""
from test_ux_browser import ux_page as _ux_page, playwright

ux_page = _ux_page


def settle(page):
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")


def test_plan_controls_results_and_narrow_layout(ux_page):
    page, directory = ux_page
    page.get_by_role('tab', name='Rebalance', exact=True).click()
    page.get_by_role('button', name='Options', exact=True).click()
    panel = page.get_by_test_id('stPopoverBody')
    playwright.expect(panel.get_by_text('Positions', exact=True)).to_be_visible()
    playwright.expect(panel.get_by_text('Purchase rules', exact=True)).to_be_visible()
    playwright.expect(panel.get_by_text('Tolerances & caps', exact=True)).to_be_visible()
    field = panel.get_by_role('spinbutton', name='Maximum trades', exact=True)
    field.fill('1')
    field.press('Enter')
    page.keyboard.press('Escape')
    page.get_by_role('button', name='Calculate plan', exact=True).click()
    playwright.expect(page.get_by_role('heading', name='Suggested trades', exact=True)).to_be_visible()
    playwright.expect(page.get_by_test_id('stMetric').filter(has=page.get_by_text('Trades', exact=True))).to_contain_text('1')
    playwright.expect(page.get_by_test_id('stRadio')).to_have_count(0)
    settle(page)
    page.get_by_role('table', name='Suggested trades', exact=True).wait_for()
    page.mouse.move(1000, 200)
    page.screenshot(path=str(directory / 'rebalance-plan.png'), full_page=True)
    page.get_by_text('Portfolio impact', exact=True).click()
    playwright.expect(page.get_by_role('combobox', name='Compare categories within', exact=True)).to_be_visible()
    page.get_by_text('Plan details', exact=True).click()
    playwright.expect(page.get_by_text('Category budgets', exact=True)).to_be_visible()
    page.get_by_role('radio', name='Within a category', exact=True).click()
    page.get_by_role('combobox', name='Rebalancing mode', exact=True).click()
    page.get_by_role('option', name='Allocate new money', exact=True).click()
    page.get_by_role('button', name='Calculate plan', exact=True).click()
    playwright.expect(page.get_by_role('heading', name='Suggested trades', exact=True)).to_be_visible()
    settle(page)
    page.set_viewport_size({'width': 700, 'height': 1000})
    playwright.expect(page.get_by_test_id('stSidebar')).to_have_count(0)
    page.mouse.move(680, 200)
    page.screenshot(path=str(directory / 'rebalance-plan-narrow.png'), full_page=True)
    button = page.get_by_role('button', name='Calculate plan', exact=True)
    button.scroll_into_view_if_needed()
    bounds = button.bounding_box()
    assert bounds['x'] + bounds['width'] <= 700
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


def test_target_grid_add_discard_and_keyboard_edit(ux_page):
    page, directory = ux_page
    page.get_by_role('tab', name='Rebalance', exact=True).click()
    page.get_by_role('tab', name='Targets', exact=True).click()
    playwright.expect(page.get_by_role('heading', name='Position targets', exact=True)).to_be_visible()
    grids = page.get_by_test_id('stDataFrame')
    playwright.expect(grids).to_have_count(2)
    settle(page)
    # Header plus two real category rows; no permanent insertion/padding rows.
    assert grids.first.bounding_box()['height'] < 130
    page.screenshot(path=str(directory / 'rebalance-targets.png'), full_page=True)
    page.get_by_role('button', name='Add category', exact=True).click()
    page.get_by_role('textbox', name='Category name', exact=True).fill('Invented future category')
    page.get_by_role('button', name='Add to draft', exact=True).click()
    playwright.expect(page.get_by_text('Portfolio: 100.00% assigned · 1 missing targets', exact=True)).to_be_visible()
    page.keyboard.press('Escape')
    settle(page)
    assert 'Invented future category' not in (directory / 'allocation.yaml').read_text()
    page.get_by_role('button', name='Save categories', exact=True).click()
    playwright.expect(page.get_by_text('Invented future category: no positions · planned capacity', exact=True)).to_be_visible()
    settle(page)
    assert 'Invented future category' in (directory / 'allocation.yaml').read_text()
    # Edit a real category name using the keyboard and discard that draft.
    canvas = grids.first.locator('canvas').first
    canvas.scroll_into_view_if_needed()
    bounds = canvas.bounding_box()
    page.mouse.dblclick(bounds['x'] + 80, bounds['y'] + 48)
    field = page.locator('[id^="gdg-overlay-"]').locator('textarea,input').first
    playwright.expect(field).to_be_focused()
    field.fill('Invented renamed category')
    field.press('Enter')
    settle(page)
    page.get_by_role('tab', name='Plan', exact=True).click()
    playwright.expect(page.get_by_role('button', name='Calculate plan', exact=True)).to_be_visible()
    page.get_by_role('tab', name='Targets', exact=True).click()
    playwright.expect(page.get_by_role('heading', name='Position targets', exact=True)).to_be_visible()
    page.get_by_role('button', name='Discard changes', exact=True).first.click()
    settle(page)
    assert 'Invented renamed category' not in (directory / 'allocation.yaml').read_text()
    page.set_viewport_size({'width': 700, 'height': 1000})
    playwright.expect(page.get_by_test_id('stSidebar')).to_have_count(0)
    page.screenshot(path=str(directory / 'rebalance-targets-narrow.png'), full_page=True)
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
