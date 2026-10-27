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
    page.get_by_role('button', name='Import transactions', exact=True).click()
    settle(page)
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


def test_listing_suggestions_dropdown_and_live_search_survive_navigation(import_page):
    from portfolio_app.positions import read_snapshot
    page, path = import_page
    upload(page, b'reference,date,type,asset,quantity,amount,currency\nb,2024-01-02,buy,BTC,1,100,EUR\n')
    listing = page.get_by_role('combobox', name='Price listing · BTC', exact=True)
    playwright.expect(listing).to_have_value('BTC-EUR · Bitcoin EUR')
    listing.click()
    page.get_by_role('option', name='BTC-USD · Bitcoin USD', exact=True).click(); settle(page)
    assert not path.exists()
    page.get_by_role('tab', name='Overview', exact=True).click(); settle(page)
    page.get_by_role('tab', name='Positions', exact=True).click(); settle(page)
    playwright.expect(listing).to_have_value('BTC-USD · Bitcoin USD')
    search = page.get_by_role('button', name='Search', exact=True).and_(page.get_by_test_id('stBaseButton-secondary'))
    playwright.expect(search).to_have_count(1)
    search.click(); settle(page)
    query = page.get_by_role('searchbox', name='Find an investment', exact=True)
    query.fill('Bitcoin')
    page.get_by_role('button', name='Select BTC-EUR on', exact=False).click(); settle(page)
    playwright.expect(listing).to_have_value('BTC-EUR · Bitcoin EUR')
    assert not path.exists()
    page.screenshot(path=str(path.parent.parent / 'synthetic-import-mapping.png'))
    page.get_by_text('Holdings were zero before these transactions', exact=True).click(); settle(page)
    page.get_by_text('Accept the displayed balance changes', exact=True).click(); settle(page)
    page.get_by_role('button', name='Apply incomplete history', exact=True).click(); settle(page)
    saved = read_snapshot(path).holdings
    assert saved.ticker.tolist() == ['BTC-EUR']
    assert saved.instrument_type.tolist() == ['crypto']
    assert not page.get_by_test_id('stException').count()


def test_existing_portfolio_opens_transaction_import_directly(import_page):
    page, path = import_page
    path.parent.mkdir(exist_ok=True)
    path.write_text('id,name,ticker,instrument_type,shares,account,quantity_unit,manual_price,manual_price_currency,manual_price_date\n'
                    'invented-btc,Bitcoin,BTC-EUR,crypto,2,Invented wallet,units,100,EUR,2024-01-01\n')
    before = path.read_bytes()
    page.reload()
    page.get_by_role('tab', name='Positions', exact=True).click(); settle(page)
    page.get_by_role('radio', name='Import', exact=True).click(); settle(page)
    playwright.expect(page.get_by_role('radio', name='Transactions', exact=True)).to_be_checked()
    playwright.expect(page.get_by_role('button', name='Download CSV template', exact=True)).to_be_visible()
    assert not page.get_by_text('empty portfolio', exact=False).count()
    assert not page.get_by_text('experimental', exact=False).count()
    page.locator('input[type=file]').set_input_files({'name':'invented-transactions.csv', 'mimeType':'text/csv',
        'buffer': b'reference,date,type,asset,quantity,amount,currency\nb,2024-01-02,buy,BTC,1,100,EUR\n'})
    settle(page)
    playwright.expect(page.get_by_role('combobox', name='Holding · BTC', exact=True)).to_have_value('Bitcoin · Invented wallet')
    assert not page.get_by_test_id('stException').count()
    assert path.read_bytes() == before
    page.get_by_role('radio', name='Holdings snapshot', exact=True).click(); settle(page)
    playwright.expect(page.get_by_text('Holdings snapshots initialize an empty portfolio.', exact=False)).to_be_visible()
    page.get_by_role('radio', name='Transactions', exact=True).click(); settle(page)
    playwright.expect(page.get_by_role('combobox', name='Holding · BTC', exact=True)).to_have_value('Bitcoin · Invented wallet')
    assert path.read_bytes() == before
