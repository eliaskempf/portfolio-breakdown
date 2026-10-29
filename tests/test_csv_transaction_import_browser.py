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
    asset_filter = page.get_by_role('combobox', name='Show transactions for', exact=True)
    asset_filter.evaluate("el => el.scrollIntoView({block:'center'})")
    asset_filter.click()
    page.get_by_role('option', name='ORB', exact=True).click(); settle(page)
    assert not path.exists()
    page.get_by_role('button', name='Save evidence only', exact=True).click(); settle(page)
    assert len(load_journal(path.parent)[0].transaction_imports) == 2
    assert not path.exists()
    page.get_by_role('tab', name='Overview', exact=True).click(); settle(page)
    page.get_by_role('tab', name='Positions', exact=True).click(); settle(page)
    page.get_by_role('button', name='Review against portfolio', exact=True).click(); settle(page)
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
    page.get_by_role('button', name='Review against portfolio', exact=True).click(); settle(page)
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
    holding = page.get_by_role('combobox', name='Holding · BTC', exact=True)
    playwright.expect(holding).to_have_value('New position')
    assert not page.get_by_role('combobox', name='Destination ·', exact=False).count()
    listing = page.get_by_role('combobox', name='Price listing · BTC', exact=True)
    playwright.expect(listing).to_have_value('BTC-EUR · Bitcoin')
    playwright.expect(listing).to_have_attribute('inputmode', 'none')
    listing.focus(); listing.press('Backspace'); listing.press_sequentially('Not an editable name')
    playwright.expect(listing).to_have_value('BTC-EUR · Bitcoin')
    listing.press('Escape')
    name = page.get_by_role('textbox', name='Name · BTC', exact=True)
    choice_box, name_box = holding.bounding_box(), name.bounding_box()
    assert name_box['x'] > choice_box['x'] + choice_box['width']
    assert abs(choice_box['y'] - name_box['y']) < 5
    name.fill('Invented Bitcoin holding'); name.press('Tab'); settle(page)
    listing.click()
    page.get_by_role('option', name='BTC-USD · Bitcoin', exact=True).click(); settle(page)
    assert not path.exists()
    page.get_by_role('tab', name='Overview', exact=True).click(); settle(page)
    page.get_by_role('tab', name='Positions', exact=True).click(); settle(page)
    playwright.expect(listing).to_have_value('BTC-USD · Bitcoin')
    playwright.expect(name).to_have_value('Invented Bitcoin holding')
    search = page.get_by_role('button', name='Search', exact=True).and_(page.get_by_test_id('stBaseButton-secondary'))
    playwright.expect(search).to_have_count(1)
    search.click(); settle(page)
    query = page.get_by_role('searchbox', name='Find an investment', exact=True)
    query.fill('Bitcoin')
    page.get_by_role('button', name='Select BTC-EUR on', exact=False).click(); settle(page)
    playwright.expect(listing).to_have_value('BTC-EUR · Bitcoin')
    assert not path.exists()
    playwright.expect(page.get_by_role('textbox', name='Name · BTC', exact=True)).to_have_value('Invented Bitcoin holding')
    page.screenshot(path=str(path.parent.parent / 'synthetic-import-mapping.png'))
    page.get_by_role('button', name='Review against portfolio', exact=True).click(); settle(page)
    assert not page.get_by_role('combobox', name='Holding · BTC', exact=True).count()
    page.screenshot(path=str(path.parent.parent / 'synthetic-shared-review.png'))
    page.get_by_role('button', name='Apply incomplete history', exact=True).click(); settle(page)
    saved = read_snapshot(path).holdings
    assert saved.ticker.tolist() == ['BTC-EUR']
    assert saved.instrument_type.tolist() == ['crypto']
    assert saved.name.tolist() == ['Invented Bitcoin holding']
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
    holding = page.get_by_role('combobox', name='Holding · BTC', exact=True)
    playwright.expect(holding).to_have_attribute('inputmode', 'none')
    holding.focus(); holding.press('Backspace'); holding.press_sequentially('Not an editable name')
    playwright.expect(holding).to_have_value('Bitcoin · Invented wallet')
    holding.press('Escape')
    assert not page.get_by_test_id('stException').count()
    assert path.read_bytes() == before

    full_width = holding.bounding_box()['width']
    holding.click()
    page.get_by_role('option', name='New position', exact=True).click(); settle(page)
    name = page.get_by_role('textbox', name='Name · BTC', exact=True)
    playwright.expect(name).to_be_visible()
    assert holding.bounding_box()['width'] < full_width
    assert name.bounding_box()['x'] > holding.bounding_box()['x']
    assert abs(holding.bounding_box()['y'] - name.bounding_box()['y']) < 5
    name.fill('Invented separate holding'); name.press('Tab'); settle(page)
    holding.click()
    page.get_by_role('option', name='Bitcoin · Invented wallet', exact=True).click(); settle(page)
    playwright.expect(name).to_have_count(0)
    assert holding.bounding_box()['width'] == full_width
    holding.click()
    page.get_by_role('option', name='New position', exact=True).click(); settle(page)
    playwright.expect(name).to_have_value('Invented separate holding')
    holding.click()
    page.get_by_role('option', name='Bitcoin · Invented wallet', exact=True).click(); settle(page)
    assert path.read_bytes() == before

    page.get_by_role('radio', name='Holdings snapshot', exact=True).click(); settle(page)
    playwright.expect(page.get_by_text('Holdings snapshots initialize an empty portfolio.', exact=False)).to_be_visible()
    page.get_by_role('radio', name='Transactions', exact=True).click(); settle(page)
    playwright.expect(page.get_by_role('combobox', name='Holding · BTC', exact=True)).to_have_value('Bitcoin · Invented wallet')
    holding = page.get_by_role('combobox', name='Holding · BTC', exact=True)
    playwright.expect(holding).to_have_attribute('inputmode', 'none')
    holding.focus(); holding.press('Backspace'); holding.press_sequentially('Not an editable name')
    playwright.expect(holding).to_have_value('Bitcoin · Invented wallet')
    holding.press('Escape')
    assert path.read_bytes() == before


def test_discard_exists_only_for_a_review_and_stays_in_import(import_page):
    page, path = import_page
    page.get_by_role('button', name='Import transactions', exact=True).click(); settle(page)
    assert not page.get_by_role('button', name='Cancel transaction import', exact=True).count()
    assert not page.get_by_role('button', name='Discard review', exact=True).count()
    page.locator('input[type=file]').set_input_files({'name':'invented.csv', 'mimeType':'text/csv',
        'buffer': b'reference,date,type,asset,quantity,amount,currency\nb,2024-01-02,buy,BTC,1,100,EUR\n'})
    settle(page)
    page.get_by_role('button', name='Discard review', exact=True).click(); settle(page)
    playwright.expect(page.get_by_role('radio', name='Import', exact=True)).to_be_checked()
    playwright.expect(page.get_by_role('radio', name='Transactions', exact=True)).to_be_checked()
    playwright.expect(page.get_by_role('button', name='Download CSV template', exact=True)).to_be_visible()
    assert not page.get_by_role('button', name='Discard review', exact=True).count()
    assert not path.exists()


def test_partial_fills_share_one_optional_group_and_apply_together(import_page):
    page, path = import_page
    upload(page, b'reference,date,type,asset,quantity,amount,currency\n'
        b'b,2024-01-01,buy,AUR,6,60,EUR\n'
        b's1,2024-01-02,sell,AUR,2,30,EUR\ns2,2024-01-02,sell,AUR,2,30,EUR\ns3,2024-01-02,sell,AUR,2,30,EUR\n')
    assert not page.get_by_role('button', name='Apply and confirm history', exact=True).count()
    page.get_by_role('button', name='Review against portfolio', exact=True).click(); settle(page)
    assert not page.get_by_role('combobox', name='Resolve match', exact=False).count()
    assert not page.get_by_role('combobox', name='Holding · AUR', exact=True).count()
    group = page.get_by_text('3 similar transactions · AUR', exact=True)
    playwright.expect(group).to_be_visible()
    playwright.expect(page.get_by_role('button', name='Apply and confirm history', exact=True)).to_be_enabled()
    group.click(); settle(page)
    page.screenshot(path=str(path.parent.parent / 'synthetic-partial-fills-review.png'))
    assert not path.exists()
    page.get_by_role('button', name='Apply incomplete history', exact=True).click(); settle(page)
    playwright.expect(page.get_by_role('table', name='Transactions', exact=True)).to_be_visible()
    journal, _ = load_journal(path.parent)
    assert len(journal.transaction_imports) == 4
    assert next(iter(replay_securities(journal.securities).positions.values())).quantity == 0
