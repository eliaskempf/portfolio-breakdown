"""Only independently generated PDF statements and synthetic portfolios."""
import pytest

from portfolio_app.account_journal import load_journal
from portfolio_app.positions import read_snapshot
from portfolio_app.security_history import effective_holdings
from test_consors_pdf import statement
from test_transaction_dialog_browser import transaction_page, watch_dialog  # noqa: F401
from browser_support import settle


def confirm_identity(page, dialog):
    # Upload completion can follow the preceding Streamlit render; wait for the
    # review control instead of sampling count() before it has appeared.
    checkbox = dialog.get_by_text('I checked the instrument identifiers', exact=True)
    checkbox.click(); settle(page)


def test_bulk_pdf_save_review_and_apply_keep_position_dialog(transaction_page):
    page, root = transaction_page
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('radio', name='Transactions', exact=True).click(); settle(page)
    dialog.get_by_role('button', name='Import PDFs', exact=True).click(); settle(page)
    buy, sale, duplicate, invalid = [root/name for name in ('invented-buy.pdf','invented-sale.pdf','invented-duplicate.pdf','invented-invalid.pdf')]
    buy.write_bytes(statement(missing=('price',), extra=('Preis pro Anteil 50,000000 EUR NETTO',)))
    sale.write_bytes(statement(sale=True, missing=('price',), net='84,00', extra=(
        'Preis pro Anteil 50,000000 EUR FRANCO COURTAGE',
        'abzgl. Handelsplatzkosten 9,00 EUR',
        'KapSt-pflichtiger Kapitalertrag - 37,19 EUR')))
    duplicate.write_bytes(buy.read_bytes())
    invalid.write_bytes(b'INVENTED INVALID PDF')
    before,_=load_journal(root)
    dialog.locator('input[type=file]').set_input_files([buy,sale,duplicate,invalid]); settle(page)
    confirm_identity(page, dialog)
    assert not page.get_by_test_id('stException').count()
    watch_dialog(page)
    dialog.get_by_role('button', name='Save transactions and review', exact=True).click(); settle(page)
    current,_=load_journal(root)
    assert len(current.securities[0].records)==len(before.securities[0].records)+2
    assert not current.securities[0].active
    assert effective_holdings(read_snapshot(root/'holdings.csv').holdings,current.securities).iloc[0].shares==2
    assert page.evaluate('window.missingDialogFrames')==0
    assert dialog.evaluate('e=>e===window.originalDialog')
    dialog.get_by_role('button',name='Apply history',exact=True).click(); settle(page)
    applied,_=load_journal(root)
    assert applied.securities[0].active
    assert effective_holdings(read_snapshot(root/'holdings.csv').holdings,applied.securities).iloc[0].shares==2
    assert not page.get_by_test_id('stException').count()
    # Re-uploading an already saved PDF never adds another record.
    # Applying history returns to the list. Do not click an outgoing review
    # button that can briefly remain while React replaces the review controls.
    dialog.get_by_role('button', name='Import PDFs', exact=True).wait_for(state='visible')
    dialog.get_by_role('button',name='Import PDFs',exact=True).click(); settle(page)
    dialog.locator('input[type=file]').set_input_files([buy]); settle(page)
    assert dialog.get_by_role('button',name='Save transactions and review',exact=True).count()==0
    assert len(load_journal(root)[0].securities[0].records)==len(applied.securities[0].records)
    page.evaluate('cancelAnimationFrame(window.dialogFrame)')


def test_review_replaces_list_actions_after_history_is_active(transaction_page):
    page,root=transaction_page
    dialog=page.get_by_role('dialog')
    dialog.get_by_role('radio',name='Transactions',exact=True).click();settle(page)
    dialog.get_by_role('button',name='Review against portfolio',exact=True).click();settle(page)
    dialog.get_by_role('button',name='Apply history',exact=True).click();settle(page)
    dialog.get_by_role('button',name='Review against portfolio',exact=True).click();settle(page)
    assert dialog.get_by_role('button',name='Apply history',exact=True).count()==1
    assert dialog.get_by_role('button',name='Review against portfolio',exact=True).count()==0
    assert dialog.get_by_role('button',name='Replace balance',exact=True).count()==0
    assert not page.get_by_test_id('stException').count()


def test_failed_statement_offers_local_masked_report_without_saving(transaction_page):
    page, root = transaction_page
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('radio', name='Transactions', exact=True).click(); settle(page)
    dialog.get_by_role('button', name='Import PDFs', exact=True).click(); settle(page)
    statement_path = root/'PRIVATE_SENTINEL_filename.pdf'
    statement_path.write_bytes(statement(missing=('price',), extra=(
        'Kurs 50,000000 EUR UNKNOWN_SUFFIX', 'PRIVATE_SENTINEL_PERSON')))
    before = load_journal(root)[1]
    dialog.locator('input[type=file]').set_input_files([statement_path]); settle(page)
    dialog.get_by_text('Statement details', exact=True).click(); settle(page)
    with page.expect_download() as pending:
        dialog.get_by_role('button', name='Download masked layout report', exact=True).click()
    download = pending.value
    assert download.suggested_filename == 'statement-layout.txt'
    from pathlib import Path
    report = Path(download.path()).read_text()
    assert 'Statement layout report v2' in report
    assert 'Parsed fee rows: 2; parsed tax rows: 0.' in report
    assert 'KURS <number:comma> <currency-or-code> <text>' in report
    for secret in ('PRIVATE_SENTINEL', 'XX0000000000', '50,000000', '07.02.2024'):
        assert secret not in report
    assert load_journal(root)[1] == before
    assert not page.get_by_test_id('stException').count()
    assert dialog.get_by_role('button', name='Save transactions and review', exact=True).count() == 0


def test_fee_free_purchase_needs_no_manual_confirmation(transaction_page):
    from consors_audit_fixtures import COMPANY_FOOTER, with_margin_reference
    page, root = transaction_page
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('radio', name='Transactions', exact=True).click(); settle(page)
    dialog.get_by_role('button', name='Import PDFs', exact=True).click(); settle(page)
    source = root/'invented-savings-plan.pdf'
    source.write_bytes(with_margin_reference(statement(fees=False, price='50,000001', extra=(
        'Bestand zugunsten Wertpapierrechnung Luxemburg', 'CBL 000123',
        'BIC 00000000', '00000 SYNTHETIC Seite: 2 VON 4',
        'Tel +00 (0) 000 / 11111, Fax +00 (0) 000 / 22222,', COMPANY_FOOTER))))
    dialog.locator('input[type=file]').set_input_files([source]); settle(page)
    confirm_identity(page, dialog)
    assert dialog.get_by_text('I checked that all fees and taxes are included', exact=True).count() == 0
    dialog.get_by_role('button', name='Save transactions and review', exact=True).click(); settle(page)
    saved = load_journal(root)[0].securities[0].records[-1]
    assert saved.charges_known and saved.charges == ()
    assert not page.get_by_test_id('stException').count()


@pytest.mark.parametrize('confirm_charges', [False,True])
def test_supplementary_rows_allow_review_without_inventing_charges(transaction_page, confirm_charges):
    from decimal import Decimal
    from consors_audit_fixtures import LOSS_SALE, SUPPLEMENTARY_DETAILS, text_pdf
    page, root = transaction_page
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('radio',name='Transactions',exact=True).click(); settle(page)
    dialog.get_by_role('button',name='Import PDFs',exact=True).click(); settle(page)
    source = root/'synthetic-context.pdf'
    source.write_bytes(text_pdf((*LOSS_SALE,'Verbleibender synthetischer Bestand ST 7,25000'),
                                SUPPLEMENTARY_DETAILS))
    dialog.locator('input[type=file]').set_input_files([source]); settle(page)
    confirm_identity(page,dialog)
    dialog.get_by_text('Statement details',exact=True).click(); settle(page)
    assert dialog.get_by_text('Additional statement rows',exact=True).count() == 1
    assert dialog.get_by_text('Verbleibender synthetischer Bestand ST 7,25000',exact=True).count() == 1
    assert dialog.get_by_role('button',name='Save transactions and review',exact=True).is_disabled()
    with page.expect_download() as pending:
        dialog.get_by_role('button',name='Download masked layout report',exact=True).click()
    from pathlib import Path
    report = Path(pending.value.path()).read_text()
    assert 'Supplementary quantity (review required)' in report
    assert 'Supplementary tax detail (review required)' in report
    assert all(value not in report for value in ('7,25000','22,50','39,50','synthetic-context.pdf'))
    action = 'Confirm statement charges' if confirm_charges else 'Keep charges unchecked'
    dialog.get_by_role('button', name=action,exact=True).click(); settle(page)
    before = load_journal(root)[0]
    dialog.get_by_role('button',name='Save transactions and review',exact=True).click(); settle(page)
    journal = load_journal(root)[0]
    event = journal.securities[0].records[-1]
    assert len(journal.securities[0].records) == len(before.securities[0].records)+1
    assert not journal.securities[0].active
    assert (event.kind,event.quantity,event.amount,event.charges_known) == (
        'sell',Decimal(2),Decimal(40),confirm_charges)
    assert [(charge.kind,charge.amount) for charge in event.charges] == [('fee',Decimal('.50'))]
    assert 'Supplementary quantity: page 1, row 9' in event.note
    assert not page.get_by_test_id('stException').count()


def test_unconfirmed_fee_purchase_can_download_eligibility_diagnostic(transaction_page):
    from pathlib import Path
    page, root = transaction_page
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('radio', name='Transactions', exact=True).click(); settle(page)
    dialog.get_by_role('button', name='Import PDFs', exact=True).click(); settle(page)
    source = root/'invented-unconfirmed.pdf'
    source.write_bytes(statement(fees=False, net='100,01'))
    dialog.locator('input[type=file]').set_input_files([source]); settle(page)
    confirm_identity(page, dialog)
    assert dialog.get_by_role('button', name='Save transactions and review', exact=True).is_disabled()
    dialog.get_by_text('Statement details', exact=True).click(); settle(page)
    with page.expect_download() as pending:
        dialog.get_by_role('button', name='Download masked layout report', exact=True).click()
    report = Path(pending.value.path()).read_text()
    assert 'Automatic zero fees: review required.' in report
    assert 'Trade value and debit are not exactly equal in the same currency.' in report
    assert '100,01' not in report
    assert not page.get_by_test_id('stException').count()


def test_independent_mixed_batch_provenance_and_identifier_review(transaction_page):
    from consors_audit_fixtures import FREE, SALE, TAX_PAGE, TAX_INFORMATION, text_pdf
    page, root = transaction_page
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('radio', name='Transactions', exact=True).click(); settle(page)
    dialog.get_by_role('button', name='Import PDFs', exact=True).click(); settle(page)
    free, sale, invalid, duplicate = [root/name for name in ('free.pdf','sale.pdf','invalid.pdf','duplicate.pdf')]
    free.write_bytes(text_pdf(FREE))
    sale.write_bytes(text_pdf((*SALE,'Limitkurs 43,750000 EUR'),TAX_PAGE,TAX_INFORMATION))
    invalid.write_bytes(text_pdf((*FREE,'Unknown refund - 7,25 EUR')))
    duplicate.write_bytes(free.read_bytes())
    dialog.locator('input[type=file]').set_input_files([free,sale,invalid,duplicate]); settle(page)
    button = dialog.get_by_role('button', name='Save transactions and review', exact=True)
    assert button.is_disabled()  # This fixture deliberately has no stored identifiers.
    confirm_identity(page, dialog)
    assert button.is_enabled()
    assert dialog.get_by_role('button', name='Confirm statement charges', exact=True).count() == 0
    dialog.get_by_text('Statement details', exact=True).click(); settle(page)
    assert dialog.get_by_role('button', name='Download masked layout report', exact=True).count() == 1
    from pathlib import Path
    with page.expect_download() as pending:
        dialog.get_by_role('button', name='Download masked layout report', exact=True).click()
    report = Path(pending.value.path()).read_text()
    assert 'Page 2, row 4' in report and 'Unclassified monetary rows remain.' in report
    assert 'Order limit (informational)' in report and 'Tax calculation (informational)' in report
    assert all(secret not in report for secret in ('XX0000000777','7,25','SYNTHETIC','125,00'))
    before = load_journal(root)[0]
    button.click(); settle(page)
    current = load_journal(root)[0]
    added = current.securities[0].records[len(before.securities[0].records):]
    assert len(added) == 2 and [event.charges_known for event in added] == [True,True]
    assert not current.securities[0].active
    assert 'Tax: page 2, row 4' in added[1].note
    assert dialog.get_by_role('button', name='Save transactions and review', exact=True).count() == 0


def test_unsupported_signed_money_cannot_be_saved_in_browser(transaction_page):
    from consors_audit_fixtures import FREE, text_pdf
    page, root = transaction_page
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('radio', name='Transactions', exact=True).click(); settle(page)
    dialog.get_by_role('button', name='Import PDFs', exact=True).click(); settle(page)
    source = root/'signed-refund.pdf'
    source.write_bytes(text_pdf((*FREE,'Unknown refund - 7,25 EUR')))
    before = load_journal(root)
    dialog.locator('input[type=file]').set_input_files([source]); settle(page)
    assert dialog.get_by_role('button', name='Save transactions and review', exact=True).count() == 0
    assert dialog.get_by_text('I checked that all fees and taxes are included', exact=True).count() == 0
    assert load_journal(root) == before


@pytest.mark.parametrize('case', ['extra amount', 'currency-free charge'])
def test_unresolved_financial_rows_offer_diagnostics_but_no_save(transaction_page, case):
    from pathlib import Path
    from consors_audit_fixtures import FREE, text_pdf
    page, root = transaction_page
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('radio', name='Transactions', exact=True).click(); settle(page)
    dialog.get_by_role('button', name='Import PDFs', exact=True).click(); settle(page)
    rows = (tuple('Kurswert 999,00 125,00 EUR' if r.startswith('Kurswert') else r for r in FREE)
            if case == 'extra amount' else (*FREE, 'Servicegebühr 4'))
    source = root/'invented-unresolved.pdf'
    source.write_bytes(text_pdf(rows))
    before = load_journal(root)
    holdings = (root/'holdings.csv').read_bytes()
    dialog.locator('input[type=file]').set_input_files([source]); settle(page)
    assert dialog.get_by_text(
        'No importable transactions. Use Add buy / Add sell for unsupported statements or missing fields.',
        exact=True).count() == 1
    assert dialog.get_by_role('button', name='Save transactions and review', exact=True).count() == 0
    assert dialog.get_by_text('I checked that all fees and taxes are included', exact=True).count() == 0
    dialog.get_by_text('Statement details', exact=True).click(); settle(page)
    with page.expect_download() as pending:
        dialog.get_by_role('button', name='Download masked layout report', exact=True).click()
    report = Path(pending.value.path()).read_text()
    assert 'Automatic zero fees: review required.' in report
    expected = 'Gross amount unreadable' if case == 'extra amount' else 'unclassified monetary row'
    assert expected in report
    assert all(secret not in report for secret in ('999,00', '125,00', 'Servicegebühr', 'XX0000000777'))
    assert load_journal(root) == before
    assert (root/'holdings.csv').read_bytes() == holdings
    assert not page.get_by_test_id('stException').count()


def test_stale_pdf_review_keeps_draft_and_rejects_save(transaction_page):
    from consors_audit_fixtures import FREE, text_pdf
    from portfolio_app.account_journal import save_journal
    from dataclasses import replace
    page, root = transaction_page
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('radio', name='Transactions', exact=True).click(); settle(page)
    dialog.get_by_role('button', name='Import PDFs', exact=True).click(); settle(page)
    source = root/'free.pdf'
    source.write_bytes(text_pdf(FREE))
    dialog.locator('input[type=file]').set_input_files([source]); settle(page)
    confirm_identity(page, dialog)
    journal, revision = load_journal(root)
    # Simulate another local writer changing retained evidence after review.
    book = journal.securities[0]
    modified = replace(book, records=(replace(book.records[0], note='Synthetic concurrent edit'),))
    save_journal(root, replace(journal, securities=(modified,)), revision)
    expected = load_journal(root)
    dialog.get_by_role('button', name='Save transactions and review', exact=True).click(); settle(page)
    assert load_journal(root) == expected
    assert dialog.get_by_text('History or holdings changed. Reload and review again.', exact=True).count() == 1
    assert dialog.locator('input[type=file]').count() == 1


def test_review_decisions_are_per_statement_in_a_mixed_batch(transaction_page):
    from consors_audit_fixtures import BUY, LOSS_SALE, SUPPLEMENTARY_DETAILS, text_pdf
    page, root = transaction_page
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('radio', name='Transactions', exact=True).click(); settle(page)
    dialog.get_by_role('button', name='Import PDFs', exact=True).click(); settle(page)
    sources = [root/name for name in ('ready-buy.pdf','check-first.pdf','check-second.pdf')]
    sources[0].write_bytes(text_pdf(BUY))
    sources[1].write_bytes(text_pdf(LOSS_SALE, SUPPLEMENTARY_DETAILS))
    sources[2].write_bytes(text_pdf(tuple(r.replace('18.03.2025','19.03.2025') for r in LOSS_SALE), SUPPLEMENTARY_DETAILS))
    dialog.locator('input[type=file]').set_input_files(sources); settle(page)
    confirm_identity(page,dialog)
    save=dialog.get_by_role('button',name='Save transactions and review',exact=True)
    assert save.is_disabled()
    assert dialog.get_by_test_id('stText').filter(has_text='check-first.pdf').count()==1
    dialog.get_by_role('button',name='Confirm statement charges',exact=True).click();settle(page)
    assert save.is_disabled()
    # The next unresolved statement becomes the default selection.
    assert dialog.get_by_test_id('stText').filter(has_text='check-second.pdf').count()==1
    dialog.get_by_role('button',name='Keep charges unchecked',exact=True).click();settle(page)
    assert save.is_enabled()
    before=load_journal(root)[0]
    save.click();settle(page)
    added=load_journal(root)[0].securities[0].records[len(before.securities[0].records):]
    assert len(added)==3
    assert [e.charges_known for e in added]==[True,True,False]
    assert not load_journal(root)[0].securities[0].active
    assert not page.get_by_test_id('stException').count()


def test_excluding_one_statement_survives_selecting_another_row(transaction_page):
    from consors_audit_fixtures import BUY, LOSS_SALE, SUPPLEMENTARY_DETAILS, text_pdf
    page,root=transaction_page
    dialog=page.get_by_role('dialog')
    dialog.get_by_role('radio',name='Transactions',exact=True).click();settle(page)
    dialog.get_by_role('button',name='Import PDFs',exact=True).click();settle(page)
    ready,review=root/'ready.pdf',root/'review.pdf'
    ready.write_bytes(text_pdf(BUY));review.write_bytes(text_pdf(LOSS_SALE,SUPPLEMENTARY_DETAILS))
    dialog.locator('input[type=file]').set_input_files([ready,review]);settle(page)
    confirm_identity(page,dialog)
    dialog.get_by_text('Include this transaction',exact=True).click();settle(page)
    assert dialog.get_by_role('button',name='Save transactions and review',exact=True).is_enabled()
    dialog.get_by_text('Show ready statements (1)', exact=True).click();settle(page)
    # Select the ready row, then return to the excluded statement. Selection
    # must not re-include it or change what the save action writes.
    table=dialog.get_by_test_id('stDataFrame').first
    # Glide's accessible text cells are off-screen; use its visible row selectors.
    canvas=table.get_by_test_id('data-grid-canvas').first
    canvas.scroll_into_view_if_needed();bounds=canvas.bounding_box()
    page.mouse.click(bounds['x']+16,bounds['y']+90);settle(page)
    assert dialog.get_by_test_id('stText').filter(has_text='ready.pdf').count()==1
    canvas.scroll_into_view_if_needed();bounds=canvas.bounding_box()
    page.mouse.click(bounds['x']+16,bounds['y']+55);settle(page)
    assert dialog.get_by_test_id('stText').filter(has_text='review.pdf').count()==1
    assert not dialog.get_by_role('checkbox',name='Include this transaction',exact=True).is_checked()
    before=load_journal(root)[0]
    dialog.get_by_role('button',name='Save transactions and review',exact=True).click();settle(page)
    added=load_journal(root)[0].securities[0].records[len(before.securities[0].records):]
    assert len(added)==1 and added[0].kind=='buy' and added[0].charges_known
    assert not page.get_by_test_id('stException').count()


def test_pdf_partial_sale_of_three_units_reviews_and_applies(transaction_page):
    from consors_audit_fixtures import text_pdf
    from portfolio_app.account_journal import Journal,save_journal
    from portfolio_app.security_history import replay_securities
    from decimal import Decimal
    page,root=transaction_page
    # Remove the fixture's invented pending trade, so this tests only the two
    # PDFs below. The user's portfolio is never read by this fixture.
    _,revision=load_journal(root)
    save_journal(root,Journal(version=2),revision)
    dialog=page.get_by_role('dialog')
    dialog.get_by_role('radio',name='Transactions',exact=True).click();settle(page)
    dialog.get_by_role('button',name='Import PDFs',exact=True).click();settle(page)
    buy,sale=root/'invented-three.pdf',root/'invented-one.pdf'
    buy.write_bytes(text_pdf(('Kauf AM 10.02.2025', 'INVENTED SYN777 XX0000000777','ST 3,00000',
        'Preis pro Anteil 10,000000 EUR','Kurswert 30,00 EUR','Zulasten 30,00 EUR')))
    sale.write_bytes(text_pdf(('Verkauf AM 11.02.2025','INVENTED SYN777 XX0000000777','ST 1,00000',
        'Preis pro Anteil 12,000000 EUR','Kurswert 12,00 EUR','Zugunsten 12,00 EUR')))
    dialog.locator('input[type=file]').set_input_files([buy,sale]);settle(page)
    confirm_identity(page,dialog)
    dialog.get_by_role('button',name='Save transactions and review',exact=True).click();settle(page)
    assert dialog.get_by_text('Units and cost match',exact=True).count()==1
    assert dialog.get_by_role('button',name='Apply history',exact=True).is_enabled()
    dialog.get_by_role('button',name='Apply history',exact=True).click();settle(page)
    journal,_=load_journal(root)
    state=replay_securities(journal.securities).positions['position-0']
    assert state.quantity==2
    assert sum(Decimal(p['amount']) for p in state.costs)==20
    assert not page.get_by_test_id('stException').count()


@pytest.mark.parametrize('applied', [False, True])
def test_bulk_removal_select_all_previews_and_preserves_originals(transaction_page, applied):
    from dataclasses import replace
    from decimal import Decimal
    from portfolio_app.account_journal import save_journal
    from portfolio_app.history_commands import reconstruction_candidate
    from portfolio_app.security_history import SecurityEvent, current_records
    page, root = transaction_page
    journal, revision = load_journal(root)
    book = journal.securities[0]
    extra = SecurityEvent('extra', 'buy', '2024-02-01', quantity=Decimal(1), price=Decimal(10),
                          currency='EUR', charges_known=True)
    book = replace(book, records=(*book.records, extra))
    if applied:
        book = reconstruction_candidate(book, ['buy', 'extra'], starts_at_zero=True)
    save_journal(root, replace(journal, securities=(book,)), revision)
    page.reload(); settle(page)
    page.get_by_role('button', name='Open position', exact=True).click(); settle(page)
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('radio', name='Transactions', exact=True).click(); settle(page)
    dialog.get_by_role('checkbox', name='Select all transactions', exact=True).check()
    dialog.get_by_role('button', name='Remove selected (2)', exact=True).click(); settle(page)
    before = load_journal(root)[1]
    if applied:
        assert dialog.get_by_text('Units differ', exact=True).count() == 1
    else:
        assert dialog.get_by_text('These entries are not applied. Your portfolio balances stay unchanged.', exact=True).count() == 1
    # Cancelling is read-only; choosing again then confirms the same two entries.
    dialog.get_by_role('button', name='Back to transactions', exact=False).click(); settle(page)
    assert load_journal(root)[1] == before
    dialog.get_by_role('checkbox', name='Select all transactions', exact=True).check()
    dialog.get_by_role('button', name='Remove selected (2)', exact=True).click(); settle(page)
    dialog.get_by_role('button', name='Remove transactions', exact=True).click(); settle(page)
    saved, _ = load_journal(root)
    assert not current_records(saved.securities[0])
    assert saved.securities[0].records[:len(book.records)] == book.records
    actual = effective_holdings(read_snapshot(root/'holdings.csv').holdings, saved.securities).iloc[0]
    assert actual.shares == (0 if applied else 2)
    assert dialog.get_by_role('table', name='Transactions', exact=True).count() == 0
    assert dialog.get_by_role('checkbox', name='Select all transactions', exact=True).count() == 0
    assert dialog.get_by_role('button', name='Review against portfolio', exact=True).count() == 0
    assert dialog.get_by_role('button', name='Replace balance', exact=True).count() == 0
    from playwright.sync_api import expect
    expect(dialog.get_by_text('No transactions yet. Add buys and sells, then review them against your portfolio.', exact=True)).to_be_visible()
    assert dialog.get_by_role('button', name='Import PDFs', exact=True).is_enabled()
    assert not page.get_by_test_id('stException').count()


def test_double_click_edits_selected_transaction_after_sorting_without_dropdown(transaction_page):
    from dataclasses import replace
    from portfolio_app.account_journal import save_journal
    from portfolio_app.security_history import SecurityEvent
    page, root = transaction_page
    journal, revision = load_journal(root)
    book = journal.securities[0]
    extra = SecurityEvent('newer', 'buy', '2024-02-01', quantity=1, amount='1326.999906', currency='EUR', charges_known=True)
    save_journal(root, replace(journal, securities=(replace(book, records=(*book.records, extra)),)), revision)
    page.reload(); settle(page)
    page.get_by_role('button', name='Open position', exact=True).click(); settle(page)
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('radio', name='Transactions', exact=True).click(); settle(page)
    table = dialog.get_by_role('table', name='Transactions', exact=True)
    assert table.locator('tbody tr').first.get_attribute('data-transaction-id') == 'newer'
    assert '1327.00 EUR' in table.locator('tbody tr').first.inner_text()
    table.get_by_role('button', name='Date ↓', exact=True).click()
    assert table.locator('tbody tr').first.get_attribute('data-transaction-id') == 'buy'
    table.locator('tr[data-transaction-id="newer"] td').nth(2).dblclick(); settle(page)
    assert not dialog.get_by_role('combobox', name='Transaction to edit', exact=True).count()
    amount = dialog.get_by_role('textbox', name='Trade value (excluding fees)', exact=True)
    from playwright.sync_api import expect
    expect(amount).to_have_value('1326.999906')
    amount.fill('1426.999906')
    dialog.get_by_role('button', name='Save changes', exact=True).click(); settle(page)
    saved, _ = load_journal(root)
    assert saved.securities[0].records[-1].replaces == 'newer'
    assert str(saved.securities[0].records[-1].amount) == '1426.999906'
    assert not saved.securities[0].active
    table = dialog.get_by_role('table', name='Transactions', exact=True)
    # The visible edit button remains available for keyboard/touch access.
    table.locator('tr[data-transaction-id="buy"]').get_by_role('button').click(); settle(page)
    expect(dialog.get_by_role('textbox', name='Unit price', exact=True)).to_have_value('10')
    assert not page.get_by_test_id('stException').count()


def test_bulk_selection_includes_offscreen_transactions(transaction_page):
    from dataclasses import replace
    from portfolio_app.account_journal import save_journal
    from portfolio_app.security_history import SecurityEvent, current_records
    page, root = transaction_page
    journal, revision = load_journal(root)
    book = journal.securities[0]
    events = tuple(SecurityEvent(f'buy-{i}', 'buy', f'2024-02-{1+i%28:02}', sequence=i,
        quantity=1, price=10+i, currency='EUR', charges_known=True) for i in range(65))
    save_journal(root, replace(journal, securities=(replace(book, records=events),)), revision)
    page.reload(); settle(page)
    page.get_by_role('button', name='Open position', exact=True).click(); settle(page)
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('radio', name='Transactions', exact=True).click(); settle(page)
    table = dialog.get_by_role('table', name='Transactions', exact=True)
    assert table.locator('tbody tr').count() == 65
    dialog.get_by_role('checkbox', name='Select all transactions', exact=True).check()
    dialog.get_by_role('button', name='Remove selected (65)', exact=True).click(); settle(page)
    dialog.get_by_role('button', name='Remove transactions', exact=True).click(); settle(page)
    saved, _ = load_journal(root)
    assert not current_records(saved.securities[0])
    assert len(saved.securities[0].records) == 130
    assert not page.get_by_test_id('stException').count()


def test_ready_pdfs_hide_by_default_and_checks_remain_visible(transaction_page):
    from consors_audit_fixtures import BUY, LOSS_SALE, SUPPLEMENTARY_DETAILS, text_pdf
    from playwright.sync_api import expect
    page, root = transaction_page
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('radio', name='Transactions', exact=True).click(); settle(page)
    dialog.get_by_role('button', name='Import PDFs', exact=True).click(); settle(page)
    ready, review = root/'ready-invented.pdf', root/'review-invented.pdf'
    ready.write_bytes(text_pdf(BUY))
    review.write_bytes(text_pdf(LOSS_SALE, SUPPLEMENTARY_DETAILS))
    dialog.locator('input[type=file]').set_input_files([ready, review]); settle(page)
    confirm_identity(page, dialog)
    table = dialog.locator('[data-testid="stDataFrame"]:visible').first
    assert table.get_by_text('ready-invented.pdf', exact=True).count() == 0
    expect(table.get_by_text('review-invented.pdf', exact=True)).to_have_count(1)
    assert dialog.get_by_test_id('stText').filter(has_text='review-invented.pdf').count() == 1
    save = dialog.get_by_role('button', name='Save transactions and review', exact=True)
    assert save.is_disabled()
    dialog.get_by_role('button', name='Confirm statement charges', exact=True).click(); settle(page)
    assert dialog.locator('[data-testid="stDataFrame"]:visible').count() == 0
    assert dialog.get_by_text('All selected statements are ready.', exact=True).count() == 1
    assert save.is_enabled()
    dialog.get_by_text('Show ready statements (2)', exact=True).click(); settle(page)
    table = dialog.locator('[data-testid="stDataFrame"]:visible').first
    expect(table.get_by_text('ready-invented.pdf', exact=True)).to_have_count(1)
    expect(table.get_by_text('review-invented.pdf', exact=True)).to_have_count(1)
    before = load_journal(root)[0]
    save.click(); settle(page)
    saved = load_journal(root)[0].securities[0]
    assert len(saved.records) == len(before.securities[0].records)+2
    assert all(e.charges_known for e in saved.records[-2:])
    assert not saved.active


def test_closing_after_bulk_import_refreshes_parent_once(transaction_page):
    from datetime import date, timedelta
    from consors_audit_fixtures import BUY, text_pdf
    from playwright.sync_api import expect
    page, root = transaction_page
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('radio', name='Transactions', exact=True).click(); settle(page)
    dialog.get_by_role('button', name='Import PDFs', exact=True).click(); settle(page)
    files = []
    for index in range(40):
        stamp = (date(2025, 1, 1)+timedelta(days=index)).strftime('%d.%m.%Y')
        source = root/f'invented-monthly-{index}.pdf'
        source.write_bytes(text_pdf(tuple(row.replace('13.03.2025', stamp).replace('17.03.2025', stamp) for row in BUY)))
        files.append(source)
    before, _ = load_journal(root)
    dialog.locator('input[type=file]').set_input_files(files); settle(page)
    confirm_identity(page, dialog)
    expect(dialog.get_by_text('Show ready statements (40)', exact=True)).to_be_visible()
    dialog.get_by_role('button', name='Save transactions and review', exact=True).click(); settle(page)
    saved, _ = load_journal(root)
    assert len(saved.securities[0].records) == len(before.securities[0].records)+40
    assert not saved.securities[0].active
    assert effective_holdings(read_snapshot(root/'holdings.csv').holdings, saved.securities).iloc[0].shares == 2
    assert page.locator('#page-runs').inner_text() == '1'
    dialog.get_by_role('button', name='Close', exact=True).first.click(); settle(page)
    expect(dialog).to_have_count(0)
    expect(page.locator('#page-runs')).to_have_text('2')
    assert not page.get_by_test_id('stException').count()
