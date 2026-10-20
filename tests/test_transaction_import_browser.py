"""Browser acceptance of transaction review with independently invented CSV."""
import csv
from io import StringIO
import pytest
from test_import_browser import import_page
from test_transaction_imports import scenario
from portfolio_app.transaction_csv import FIELDS
from portfolio_app.account_journal import load_journal
from portfolio_app.security_history import replay_securities
from browser_support import settle

playwright = pytest.importorskip('playwright.sync_api')


def test_upload_evidence_reconcile_reimport_and_navigation(import_page):
    page,path=import_page
    page.get_by_role('button',name='Import portfolio — experimental',exact=True).click()
    page.get_by_text('Transactions',exact=True).click();settle(page)
    page.get_by_text('Transaction CSV',exact=True).wait_for()
    stream=StringIO();writer=csv.DictWriter(stream,fieldnames=FIELDS);writer.writeheader();writer.writerows(r.facts for r in scenario())
    content=stream.getvalue().encode()
    page.locator('input[type=file]').set_input_files({'name':'invented-transactions.csv','mimeType':'text/csv','buffer':content})
    page.get_by_role('button',name='Save evidence only',exact=True).click();settle(page)
    assert len(load_journal(path.parent)[0].transaction_imports)==7
    assert not load_journal(path.parent)[0].securities
    page.get_by_role('tab',name='Overview',exact=True).click();settle(page)
    page.get_by_role('tab',name='Positions',exact=True).click();settle(page)
    playwright.expect(page.get_by_text('invented-transactions.csv',exact=True)).to_be_visible()
    page.get_by_text('I reviewed instrument identity, owned custody accounts and funding',exact=True).click()
    page.get_by_role('button',name='Create missing positions',exact=True).click();settle(page)
    page.get_by_text('Holdings were zero before the first recorded movement',exact=True).click();settle(page)
    playwright.expect(page.get_by_text('Reconcile balances',exact=True)).to_be_visible()
    page.get_by_text('Accept reconstructed balances and shown cash changes',exact=True).click()
    page.get_by_role('button',name='Apply reviewed transactions',exact=True).click();settle(page)
    playwright.expect(page.get_by_text('7 movements applied. History completeness is reviewed separately.',exact=True)).to_be_visible()
    journal,revision=load_journal(path.parent)
    assert len(journal.transaction_imports)==7
    assert sum(s.quantity for s in replay_securities(journal.securities).positions.values())>0
    # Identical reupload leaves all active balances and evidence unchanged.
    page.get_by_text('Upload CSV',exact=True).click();settle(page)
    page.locator('input[type=file]').set_input_files({'name':'invented-transactions.csv','mimeType':'text/csv','buffer':content});settle(page)
    page.get_by_role('button',name='Save evidence only',exact=True).click();settle(page)
    assert load_journal(path.parent)[1]==revision
    page.get_by_role('button',name='Cancel transaction import',exact=True).click();settle(page)
    page.get_by_role('tab',name='Overview',exact=True).click();settle(page)
    page.get_by_role('tab',name='Positions',exact=True).click();settle(page)
    assert not page.get_by_test_id('stException').count()
    assert not page.get_by_role('button',name='Apply reviewed transactions',exact=True).count()


def test_estimated_checkpoint_requires_explicit_reconciliation(import_page):
    from test_inventory_imports import row, estimate
    from portfolio_app.transaction_csv import FIELDS_V2
    page,path=import_page
    page.get_by_role('button',name='Import portfolio — experimental',exact=True).click()
    page.get_by_text('Transactions',exact=True).click();settle(page)
    page.get_by_role('combobox',name='CSV format',exact=True).click()
    page.get_by_role('option',name='Generic CSV v2',exact=True).click();settle(page)
    r=row('balance',**estimate())
    stream=StringIO();writer=csv.DictWriter(stream,fieldnames=FIELDS_V2);writer.writeheader();writer.writerow(r.facts)
    page.locator('input[type=file]').set_input_files({'name':'invented-checkpoint.csv','mimeType':'text/csv','buffer':stream.getvalue().encode()});settle(page)
    page.get_by_text('I reviewed instrument identity, owned custody accounts and funding',exact=True).click()
    page.get_by_role('button',name='Create missing positions',exact=True).click();settle(page)
    page.get_by_role('combobox',name='Movements to apply',exact=True).click()
    page.get_by_role('option',name='balance · checkpoint · AUR',exact=True).click()
    page.keyboard.press('Escape');settle(page)
    page.get_by_text('Balance checkpoints',exact=True).wait_for()
    page.get_by_text('Accept reconstructed balances and shown cash changes',exact=True).click();settle(page)
    apply=page.get_by_role('button',name='Apply reviewed transactions',exact=True)
    playwright.expect(apply).to_be_disabled()
    page.get_by_text('Accept checkpoints and retain unresolved differences',exact=True).click();settle(page)
    playwright.expect(apply).to_be_enabled()
    apply.click();settle(page)
    page.get_by_text('These events are already applied. Reimport leaves balances unchanged.',exact=True).wait_for()
    journal,_=load_journal(path.parent)
    states=replay_securities(journal.securities).positions
    assert sum(s.quantity for s in states.values())==10
    assert next(iter(states.values())).costs[0]['quality']=='estimated'
    assert not page.get_by_test_id('stException').count()
