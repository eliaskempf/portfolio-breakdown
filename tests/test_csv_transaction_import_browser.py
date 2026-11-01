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


def listing_field(page, asset):
    from portfolio_app.transaction_csv import digest
    return page.locator(f'[class*="{digest(asset)[:12]}_listing_"][class*="_picker"]').get_by_role('button').first


def apply_reviewed_batch(page):
    for _ in range(2):
        page.get_by_role('button', name='Accept this holding', exact=True).click(); settle(page)
    page.get_by_role('button', name='Apply reviewed holdings', exact=True).click(); settle(page)


def open_transactions(page, name):
    assert not page.get_by_role('dialog').count()
    page.get_by_role('table', name='Positions', exact=True).get_by_text(name, exact=True).click(); settle(page)
    page.get_by_role('dialog').get_by_role('radio', name='Transactions', exact=True).click(); settle(page)


def open_correction(page, index):
    checkbox = page.get_by_role('checkbox', name='Use this correction', exact=True, include_hidden=True).nth(index)
    if not checkbox.is_visible():
        page.get_by_text(re.compile('^Changed transaction ·')).nth(index).click()
    return checkbox


def test_second_same_day_file_has_order_review_before_apply(import_page):
    page,path = import_page
    header = b'reference,date,type,asset,quantity,amount,currency\n'
    upload(page,header+b'a,2024-01-02,buy,BTC,2,20,EUR\n')
    page.get_by_role('button',name='Review against portfolio',exact=True).click(); settle(page)
    page.get_by_role('button',name='Apply history',exact=True).click(); settle(page)
    page.get_by_role('tab',name='Positions',exact=True).click(); settle(page)
    page.get_by_role('radio',name='Import',exact=True).click(); settle(page)
    page.locator('input[type=file]').set_input_files({'name':'invented-second.csv','mimeType':'text/csv',
        'buffer':header+b'b,2024-01-02,sell,BTC,1,30,EUR\n'})
    settle(page)
    page.get_by_role('button',name='Review against portfolio',exact=True).click(); settle(page)
    playwright.expect(page.get_by_text('Review same-day order',exact=True)).to_be_visible()
    playwright.expect(page.get_by_role('spinbutton')).to_have_count(2)
    assert not page.get_by_role('button',name='Apply history',exact=True).count()
    page.get_by_role('button',name='Use reviewed order',exact=True).click(); settle(page)
    page.get_by_role('button',name='Apply history',exact=True).click(); settle(page)
    assert not page.get_by_test_id('stException').count()
    assert next(iter(replay_securities(load_journal(path.parent)[0].securities).positions.values())).quantity == 1


def test_excluding_all_rows_has_explicit_save_and_reopens(import_page):
    page,path = import_page
    content = (b'reference,date,type,asset,quantity,amount,currency\n'
        b'a,2024-01-02,buy,BTC,1,10,EUR\nb,2024-01-02,buy,BTC,1,10,EUR\n')
    upload(page,content)
    page.get_by_role('button',name='Review against portfolio',exact=True).click(); settle(page)
    page.get_by_text(re.compile('^2 similar transactions')).click()
    choice = page.get_by_test_id('stMultiSelect').filter(has=page.get_by_text(re.compile('^Keep transactions')))
    choice.get_by_role('button',name=re.compile('^Remove ')).first.click(); settle(page)
    choice.get_by_role('button',name=re.compile('^Remove ')).first.click(); settle(page)
    page.get_by_role('button',name='Save exclusions',exact=True).click(); settle(page)
    assert not path.exists() and not load_journal(path.parent)[0].securities[0].active
    assert not page.get_by_test_id('stException').count()


@pytest.mark.parametrize('removal', [False, True])
@pytest.mark.parametrize('invalid_pending', [False, True])
def test_changed_sources_show_fields_and_require_individual_decisions(import_page, removal, invalid_pending):
    from dataclasses import replace
    from decimal import Decimal
    from portfolio_app.history_commands import correct_transaction, remove_transactions, save_history
    from portfolio_app.positions import read_snapshot
    from portfolio_app.transaction_csv import read_csv
    from portfolio_app.transaction_import_commands import save_transaction_import
    page, path = import_page
    header = 'reference,date,type,asset,quantity,amount,currency\n'
    original = read_csv(header+'a,2024-01-02,buy,AUR,10,100,EUR\nb,2024-01-03,buy,AUR,5,60,EUR\n')
    save_transaction_import(path.parent, original, {('Portfolio', 'AUR'): 'aur'},
        expected_journal_revision=None, expected_holdings_revision=None,
        additions=[dict(position_key='aur', id='aur', name='Invented Aurora', account='Portfolio')],
        selected_ids=[r.identity for r in original], starts_at_zero=True,
        accept_reconciliation=True, comparison_date='2024-01-04')
    journal, revision = load_journal(path.parent)
    candidate = correct_transaction(journal.securities[0], original[0].identity, quantity=Decimal(11))
    candidate = replace(candidate, active_ids=tuple(i for i in candidate.active_ids if i != original[0].identity)
                        + (candidate.records[-1].id,))
    if removal:
        candidate = remove_transactions(candidate, [candidate.records[-1].id])
    save_history(path.parent, candidate, expected_journal_revision=revision,
        expected_holdings_revision=read_snapshot(path).revision, activate=True, accept_balance_change=True)
    page.reload(); settle(page)
    page.get_by_role('tab', name='Positions', exact=True).click(); settle(page)
    page.get_by_role('radio', name='Import', exact=True).click(); settle(page)
    incoming = header+'a,2024-01-02,buy,AUR,10,101,EUR\nb,2024-01-03,buy,AUR,5,62,EUR\n'
    pending = incoming.replace(',101,', ',unknown,' if invalid_pending else ',102,').replace(',62,', ',61,')
    page.locator('input[type=file]').set_input_files({'name': 'invented-invalid-correction.csv', 'mimeType': 'text/csv',
        'buffer': pending.encode()})
    settle(page)
    playwright.expect(page.get_by_role('button', name='Save evidence only', exact=True)).to_be_visible()
    assert not page.get_by_test_id('stException').count()
    for index in range(2):
        checkbox = open_correction(page, index)
        page.get_by_text('Use this correction', exact=True).nth(index).click(); settle(page)
        playwright.expect(checkbox).to_be_checked()
    page.get_by_role('button', name='Save evidence only', exact=True).click(); settle(page)
    assert len(load_journal(path.parent)[0].transaction_imports) == 4
    page.locator('input[type=file]').set_input_files({'name': 'invented-corrections.csv', 'mimeType': 'text/csv', 'buffer': incoming.encode()})
    settle(page)
    review = page.get_by_role('button', name='Review against portfolio', exact=True)
    playwright.expect(review).to_be_disabled()
    expanders = page.get_by_text(re.compile('^Changed transaction ·'))
    playwright.expect(expanders).to_have_count(2)
    open_correction(page, 0)
    if removal:
        playwright.expect(page.get_by_text('This restores a transaction you removed.', exact=True)).to_be_visible()
    else:
        playwright.expect(page.get_by_text('This replaces your later edit to this transaction.', exact=True)).to_be_visible()
        playwright.expect(page.get_by_text('Current history', exact=True)).to_be_visible()
        playwright.expect(page.get_by_role('cell', name='11', exact=True)).to_be_visible()
    playwright.expect(page.get_by_text('Gross amount', exact=True).nth(0)).to_be_visible()
    checks = page.get_by_role('checkbox', name='Use this correction', exact=True, include_hidden=True)
    page.get_by_text('Use this correction', exact=True).nth(0).click(); settle(page)
    playwright.expect(checks.nth(0)).to_be_checked()
    playwright.expect(review).to_be_disabled()
    open_correction(page, 1)
    page.get_by_text('Use this correction', exact=True).nth(1).click(); settle(page)
    playwright.expect(checks.nth(1)).to_be_checked()
    playwright.expect(review).to_be_enabled()
    review.click(); settle(page)
    playwright.expect(page.get_by_role('button', name='Apply history', exact=True)).to_be_visible()
    assert replay_securities(load_journal(path.parent)[0].securities).positions['aur'].quantity == (5 if removal else 16)
    assert not page.get_by_test_id('stException').count()


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
    apply_reviewed_batch(page)
    open_transactions(page, 'Aur')
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
    apply_reviewed_batch(page)
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
    apply=page.get_by_role('button', name='Apply history', exact=True)
    playwright.expect(apply).to_be_disabled()
    page.get_by_text('Accept the displayed replacement balances', exact=True).click(); settle(page)
    playwright.expect(apply).to_be_enabled()
    apply.click(); settle(page)
    assert not page.get_by_role('dialog').count()
    states=replay_securities(load_journal(path.parent)[0].securities).positions
    assert sum(s.quantity for s in states.values()) == 10
    assert next(iter(states.values())).costs[0]['quality'] == 'estimated'
    assert not page.get_by_test_id('stException').count()


@pytest.mark.parametrize('width', [1440, 390])
def test_listing_popup_search_selection_dismissal_and_navigation(import_page, width):
    from portfolio_app.positions import read_snapshot
    page, path = import_page
    page.set_viewport_size({'width': width, 'height': 1000})
    upload(page, b'reference,date,type,asset,quantity,amount,currency\nb,2024-01-02,buy,BTC,1,100,EUR\n')
    holding = page.get_by_role('combobox', name='Holding · BTC', exact=True)
    playwright.expect(holding).to_have_value('New position')
    assert not page.get_by_role('combobox', name='Destination ·', exact=False).count()
    listing = listing_field(page, 'BTC')
    playwright.expect(listing).to_contain_text('BTC-EUR · Bitcoin')
    assert not page.get_by_role('button', name='Search', exact=True).and_(page.get_by_test_id('stBaseButton-secondary')).count()
    assert not page.get_by_role('searchbox', name='Search listings', exact=True).count()
    name = page.get_by_role('textbox', name='Name · BTC', exact=True)
    choice_box, name_box = holding.bounding_box(), name.bounding_box()
    if width > 600:
        assert name_box['x'] > choice_box['x'] + choice_box['width']
        assert abs(choice_box['y'] - name_box['y']) < 5
    name.fill('Invented Bitcoin holding'); name.press('Tab'); settle(page)
    listing.click(); settle(page)
    query = page.get_by_role('searchbox', name='Search listings', exact=True)
    playwright.expect(query).to_be_focused()
    results = page.get_by_role('button', name=re.compile('^Select BTC-'))
    playwright.expect(results.first).to_have_accessible_name('Select BTC-EUR')
    playwright.expect(results.first).to_be_in_viewport(ratio=1)
    playwright.expect(page.get_by_role('button', name='No price listing', exact=True)).to_be_in_viewport(ratio=1)
    page.screenshot(path=str(path.parent.parent / f'synthetic-listing-popup-{width}.png'))
    page.get_by_role('button', name='Select BTC-USD', exact=True).click(); settle(page)
    playwright.expect(query).not_to_be_visible()
    assert not path.exists()
    page.get_by_role('tab', name='Overview', exact=True).click(); settle(page)
    page.get_by_role('tab', name='Positions', exact=True).click(); settle(page)
    playwright.expect(listing).to_contain_text('BTC-USD · Bitcoin')
    playwright.expect(name).to_have_value('Invented Bitcoin holding')
    listing.click(); settle(page)
    query.fill('Nvidia'); query.press('Escape'); settle(page)
    playwright.expect(query).not_to_be_visible()
    playwright.expect(listing).to_contain_text('BTC-USD · Bitcoin')
    listing.click(); settle(page)
    page.get_by_role('textbox', name='Name · BTC', exact=True).click(); settle(page)
    playwright.expect(query).not_to_be_visible()
    playwright.expect(listing).to_contain_text('BTC-USD · Bitcoin')
    listing.click(); settle(page)
    query.fill('Bitcoin')
    page.get_by_role('button', name='Select BTC-EUR', exact=True).wait_for()
    query.press('ArrowDown'); page.keyboard.press('Enter'); settle(page)
    playwright.expect(query).not_to_be_visible()
    playwright.expect(listing).to_contain_text('BTC-EUR · Bitcoin')
    listing.click(); settle(page)
    page.get_by_role('button', name='No price listing', exact=True).click(); settle(page)
    playwright.expect(query).not_to_be_visible()
    playwright.expect(listing).to_contain_text('No price listing')
    playwright.expect(name).to_have_value('Invented Bitcoin holding')
    listing.click(); settle(page)
    page.get_by_role('button', name='Select BTC-EUR', exact=True).click(); settle(page)
    assert not path.exists()
    playwright.expect(page.get_by_role('textbox', name='Name · BTC', exact=True)).to_have_value('Invented Bitcoin holding')
    page.screenshot(path=str(path.parent.parent / 'synthetic-import-mapping.png'))
    page.get_by_role('button', name='Review against portfolio', exact=True).click(); settle(page)
    assert not page.get_by_role('combobox', name='Holding · BTC', exact=True).count()
    page.screenshot(path=str(path.parent.parent / 'synthetic-shared-review.png'))
    page.get_by_role('button', name='Apply history', exact=True).click(); settle(page)
    saved = read_snapshot(path).holdings
    assert saved.ticker.tolist() == ['BTC-EUR']
    assert saved.instrument_type.tolist() == ['crypto']
    assert saved.name.tolist() == ['Invented Bitcoin holding']
    assert not page.get_by_test_id('stException').count()


def test_mixed_portfolio_finds_eur_pairs_missing_from_initial_suggestions(import_page):
    page, path = import_page
    path.parent.mkdir(exist_ok=True)
    path.write_text('id,name,ticker,instrument_type,shares\n'
                    'invented-stock,Invented Company,INVENTED,equity,1\n'
                    'invented-crypto,Invented Bitcoin holding,BTC-USD,crypto,1\n')
    # Simulate broad search returning only USD; exact pair lookup returns EUR.
    script = path.parent.parent/'app.py'
    script.write_text('''
from portfolio_app.instruments import Instrument
import portfolio_app.transaction_mapping_ui as mapping_ui
def synthetic_search(query, cache_dir, demo):
    ticker = query if '-' in query else query+'-USD'
    return [Instrument(ticker, ticker, kind='CRYPTOCURRENCY')], ''
mapping_ui._fetch = synthetic_search
''' + script.read_text())
    before = path.read_bytes()
    page.reload()
    page.get_by_role('tab', name='Positions', exact=True).click(); settle(page)
    page.get_by_role('radio', name='Import', exact=True).click(); settle(page)
    page.locator('input[type=file]').set_input_files({'name':'invented-transactions.csv', 'mimeType':'text/csv',
        'buffer': b'reference,date,type,asset,quantity,amount,currency\n'
                  b'a,2024-01-02,buy,ADA,1,10,EUR\nb,2024-01-02,buy,LTC,1,20,EUR\n'})
    settle(page)
    for asset in ('ADA', 'LTC'):
        listing = listing_field(page, asset)
        playwright.expect(listing).to_contain_text(asset+'-EUR · '+asset+'-EUR')
    assert path.read_bytes() == before
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
    assert not page.get_by_role('button', name='Apply history', exact=True).count()
    page.get_by_role('button', name='Review against portfolio', exact=True).click(); settle(page)
    assert not page.get_by_role('combobox', name='Resolve match', exact=False).count()
    assert not page.get_by_role('combobox', name='Holding · AUR', exact=True).count()
    group = page.get_by_text('3 similar transactions · AUR', exact=True)
    playwright.expect(group).to_be_visible()
    playwright.expect(page.get_by_role('button', name='Apply history', exact=True)).to_be_enabled()
    group.click(); settle(page)
    page.screenshot(path=str(path.parent.parent / 'synthetic-partial-fills-review.png'))
    assert not path.exists()
    page.get_by_role('button', name='Apply history', exact=True).click(); settle(page)
    assert not page.get_by_role('dialog').count()
    journal, _ = load_journal(path.parent)
    assert not page.get_by_role('table', name='Positions', exact=True).get_by_text('Aur', exact=True).count()
    page.get_by_role('button', name='Portfolio settings', exact=True).click(); settle(page)
    page.get_by_text('Show sold positions', exact=True).click(); settle(page)
    page.get_by_role('button', name='Portfolio settings', exact=True).click(); settle(page)
    open_transactions(page, 'Aur')
    assert len(journal.transaction_imports) == 4
    assert next(iter(replay_securities(journal.securities).positions.values())).quantity == 0
