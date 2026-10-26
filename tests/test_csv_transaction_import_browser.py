"""Browser acceptance of the common lifecycle, with invented transactions only."""
import csv
from io import StringIO
import re
import pytest
from test_import_browser import import_page
from portfolio_app.account_journal import load_journal
from portfolio_app.security_history import replay_securities
from browser_support import settle as render_settle

playwright = pytest.importorskip('playwright.sync_api')


def settle(page):
    try:
        render_settle(page)
    except AssertionError:
        assert not page.get_by_test_id('stException').count(), page.get_by_test_id('stException').all_text_contents()
        raise


def upload(page, content):
    page.get_by_role('button', name='Import portfolio — experimental', exact=True).click()
    page.get_by_text('Transactions', exact=True).click(); settle(page)
    page.locator('input[type=file]').set_input_files({'name':'invented-transactions.csv', 'mimeType':'text/csv', 'buffer':content})
    settle(page)


def test_simple_upload_apply_edit_and_remove_in_normal_transactions(import_page):
    page, path = import_page
    content = (b'reference,date,type,asset,quantity,amount,currency,received_asset,received_quantity\n'
               b'b,2024-01-02,buy,AUR,10,100,EUR,,\n'
               b'x,2024-01-03,exchange,AUR,2,25,EUR,ORB,4\n')
    upload(page, content)
    assert not path.exists()
    page.get_by_role('button', name='Save evidence only', exact=True).click(); settle(page)
    assert len(load_journal(path.parent)[0].transaction_imports) == 2
    assert not path.exists()
    page.get_by_role('tab', name='Overview', exact=True).click(); settle(page)
    page.get_by_role('tab', name='Positions', exact=True).click(); settle(page)
    page.get_by_text('Holdings were zero before these transactions', exact=True).click(); settle(page)
    page.get_by_text('Accept the displayed balance changes', exact=True).click(); settle(page)
    page.get_by_role('button', name='Apply incomplete history', exact=True).click(); settle(page)
    table = page.get_by_role('table', name='Transactions', exact=True)
    playwright.expect(table).to_be_visible()
    journal, _ = load_journal(path.parent)
    assert sorted(s.quantity for s in replay_securities(journal.securities).positions.values()) == [4,8]
    # Both legs use the normal list. Correction is saved as evidence first.
    table.get_by_role('button', name=re.compile('Edit .*Exchange')).click(); settle(page)
    value = page.get_by_role('textbox', name='Received quantity', exact=True)
    value.fill('5'); value.press('Tab'); settle(page)
    page.get_by_role('button', name='Save changes', exact=True).click(); settle(page)
    assert sorted(s.quantity for s in replay_securities(load_journal(path.parent)[0].securities).positions.values()) == [4,8]
    page.get_by_role('button', name='Review against portfolio', exact=True).click(); settle(page)
    page.get_by_role('button', name='Apply incomplete history', exact=True).click(); settle(page)
    assert sorted(s.quantity for s in replay_securities(load_journal(path.parent)[0].securities).positions.values()) == [5,8]
    table = page.get_by_role('table', name='Transactions', exact=True)
    table.get_by_role('checkbox', name=re.compile('Select .*Exchange')).check()
    page.get_by_role('button', name='Remove selected (1)', exact=True).click(); settle(page)
    page.get_by_role('button', name='Remove transactions', exact=True).click(); settle(page)
    assert sorted(s.quantity for s in replay_securities(load_journal(path.parent)[0].securities).positions.values()) == [0,10]
    assert not page.get_by_test_id('stException').count()


def test_estimated_checkpoint_requires_explicit_reconciliation(import_page):
    from test_inventory_imports import row, estimate
    from portfolio_app.transaction_csv import FIELDS_V2
    page, path = import_page
    stream=StringIO(); writer=csv.DictWriter(stream,fieldnames=FIELDS_V2); writer.writeheader(); writer.writerow(row('balance',**estimate()).facts)
    upload(page, stream.getvalue().encode())
    page.get_by_text('Accept the displayed balance changes', exact=True).click(); settle(page)
    apply=page.get_by_role('button', name='Apply and confirm history', exact=True)
    playwright.expect(apply).to_be_disabled()
    page.get_by_text('Accept the displayed replacement balances', exact=True).click(); settle(page)
    playwright.expect(apply).to_be_enabled()
    apply.click(); settle(page)
    playwright.expect(page.get_by_role('table', name='Transactions', exact=True)).to_be_visible()
    states=replay_securities(load_journal(path.parent)[0].securities).positions
    assert sum(s.quantity for s in states.values()) == 10
    assert next(iter(states.values())).costs[0]['quality'] == 'estimated'
    assert not page.get_by_test_id('stException').count()
