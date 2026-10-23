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
    dialog.get_by_text('I checked that all fees and taxes are included', exact=True).click(); settle(page)
    watch_dialog(page)
    dialog.get_by_role('button', name='Save transactions and review', exact=True).click(); settle(page)
    current,_=load_journal(root)
    assert len(current.securities[0].records)==len(before.securities[0].records)+2
    assert not current.securities[0].active
    assert effective_holdings(read_snapshot(root/'holdings.csv').holdings,current.securities).iloc[0].shares==2
    assert page.evaluate('window.missingDialogFrames')==0
    assert dialog.evaluate('e=>e===window.originalDialog')
    dialog.get_by_role('button',name='Apply history to portfolio',exact=True).click(); settle(page)
    applied,_=load_journal(root)
    assert applied.securities[0].active
    assert effective_holdings(read_snapshot(root/'holdings.csv').holdings,applied.securities).iloc[0].shares==2
    assert not page.get_by_test_id('stException').count()
    # Re-uploading an already saved PDF never adds another record.
    back=dialog.get_by_role('button',name='Back to transactions',exact=True)
    if back.count(): back.click(); settle(page)
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
    dialog.get_by_role('button',name='Apply history to portfolio',exact=True).click();settle(page)
    dialog.get_by_role('button',name='Review against portfolio',exact=True).click();settle(page)
    assert dialog.get_by_role('button',name='Apply history to portfolio',exact=True).count()==1
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
    assert dialog.get_by_text(
        'Supplementary rows were excluded from transaction values; review their source locations.',
        exact=True).count() == 1
    with page.expect_download() as pending:
        dialog.get_by_role('button',name='Download masked layout report',exact=True).click()
    from pathlib import Path
    report = Path(pending.value.path()).read_text()
    assert 'Supplementary quantity (review required)' in report
    assert 'Supplementary tax detail (review required)' in report
    assert all(value not in report for value in ('7,25000','22,50','39,50','synthetic-context.pdf'))
    if confirm_charges:
        dialog.get_by_text('I checked that all fees and taxes are included',exact=True).click(); settle(page)
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
    assert dialog.get_by_role('button', name='Save transactions and review', exact=True).is_enabled()
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
    assert dialog.get_by_text('I checked that all fees and taxes are included', exact=True).count() == 1
    dialog.get_by_text('Statement details', exact=True).click(); settle(page)
    assert dialog.get_by_text('Field sources · visual rows counted from the top of each page', exact=True).count() == 1
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
    assert len(added) == 2 and [event.charges_known for event in added] == [True,False]
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
