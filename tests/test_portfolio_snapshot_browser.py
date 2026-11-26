"""Navigate a real synthetic Performance view, select a close, and return."""
from dataclasses import replace

import pytest

from browser_support import settle
from test_history_data_browser import history_page
from test_history_ui import fixture

playwright = pytest.importorskip('playwright.sync_api')


def test_history_snapshot_date_and_return(tmp_path):
    from portfolio_app.performance.history_observations import load_observations, save_observations
    fixture(tmp_path)
    (tmp_path / 'allocation.yaml').write_text('version: 3\nbuckets:\n  - {id: subset, name: Invented category}\n')
    holdings = tmp_path / 'holdings.csv'
    holdings.write_text('id,name,shares,acquisition_price,acquisition_currency,bucket_id\n'
                        'invented,Invented,10,10,EUR,subset\n')
    from portfolio_app.ledger.account_journal import Account, Event, load_journal, save_journal
    journal, rev = load_journal(tmp_path)
    save_journal(tmp_path, replace(journal, accounts=(Account('a', 'Invented account'),),
        events=(Event('cash', '2024-01-01', 'cash_open', 'a', 'EUR', 20),)), rev)
    store, revision = load_observations(tmp_path)
    store = replace(store, observations=(*store.observations,
        replace(store.observations[-1], id='c', date='2025-01-02', amount=15)))
    save_observations(tmp_path, store, revision)
    (tmp_path / 'demo_prices.json').write_text('{"prices": {}, "fx": {}}', encoding='utf-8')
    script = tmp_path / 'preview.py'
    script.write_text(
        'from pathlib import Path\nfrom portfolio_app.interface.ui import render_app\n'
        'render_app(Path(__file__).parent, demo=True)\n', encoding='utf-8')
    saved = {p: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    with history_page(script) as page:
        action = page.get_by_role('button', name='View holdings on a date', exact=True)
        category = page.get_by_role('combobox', name='Category', exact=True)
        category.click()
        page.get_by_role('option', name='Invented category', exact=True).click()
        playwright.expect(category).to_have_value('Invented category')
        settle(page)
        period = page.get_by_role('radiogroup', name='Period').get_by_role('radio', name='All', exact=True)
        period.click()
        playwright.expect(period).to_be_checked()
        settle(page)
        action.click()
        statement = page.locator('.st-key-historical_portfolio')
        playwright.expect(statement).to_be_visible()
        picker = statement.get_by_role('group', name='Portfolio date', exact=True)
        playwright.expect(picker).to_be_visible()
        picker.get_by_role('spinbutton').first.click()
        page.get_by_role('button', name='Wednesday, January 1, 2025', exact=True).click()
        settle(page)
        playwright.expect(statement.get_by_test_id('stMetricValue').first).to_have_text('€140.00')
        playwright.expect(statement.get_by_role('heading', name='Portfolio on 2025-01-01')).to_be_visible()
        table = statement.get_by_role('table', name='Historical holdings')
        playwright.expect(table).to_contain_text('Invented account')
        statement.get_by_text('Account balances', exact=True).click()
        playwright.expect(statement.get_by_role('table', name='Account balances')).to_contain_text('20')
        assert not page.get_by_role('button', name='Edit position').is_visible()
        page.get_by_role('button', name='Back to Performance', exact=True).click()
        playwright.expect(action).to_be_visible()
        playwright.expect(category).to_have_value('Invented category')
        playwright.expect(period).to_be_checked()
        action.click()
        playwright.expect(statement.get_by_test_id('stMetricValue').first).to_have_text('€140.00')
        playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
    assert all(path.read_bytes() == content for path,content in saved.items())
