"""Percentage changes and monetary gains retain distinct visual directions."""
import re
from test_ux_browser import playwright, ux_page  # noqa: F401
from browser_support import settle


def test_position_percentage_directions_sort_by_numeric_value(ux_page):
    page, _ = ux_page
    table = page.get_by_role('table', name='Positions', exact=True)
    headers = table.locator('thead th').all_text_contents()
    pct = next(i for i, name in enumerate(headers) if 'Return (%)' in name)
    money = next(i for i, name in enumerate(headers) if name.startswith('Gain'))
    rows = table.locator('tbody tr')
    returns = [r.locator('td').nth(pct).inner_text() for r in rows.all()]
    gains = [r.locator('td').nth(money).inner_text() for r in rows.all()]
    assert any(v.startswith('▲ ') for v in returns)
    assert any(v.startswith('▼ ') for v in returns)
    assert all(not re.match(r'[+-]', v) for v in returns)
    assert any(v.startswith('+') for v in gains) and any(v.startswith('-') for v in gains)
    table.get_by_role('button', name='Return (%)', exact=True).click()
    playwright.expect(rows.first.locator('td').nth(pct)).to_have_text('▼ 16.67')
    table.get_by_role('button', name='Return (%) ↑', exact=True).click()
    playwright.expect(rows.first.locator('td').nth(pct)).to_have_text('▲ 60.00')
    settle(page)
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
