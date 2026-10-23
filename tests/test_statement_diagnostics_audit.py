"""Privacy assertions use invented sentinels; originals are never opened."""
from hashlib import sha256

import pymupdf
import pytest

from consors_audit_fixtures import FREE, SALE, LOSS_SALE, SUPPLEMENTARY_DETAILS, TAX_PAGE, TAX_INFORMATION, DOCUMENT_REFERENCE, text_pdf, with_margin_reference
from portfolio_app.consors_pdf import parse_consors_pdf, parse_consors_batch
from portfolio_app.statement_diagnostics import layout_report


@pytest.mark.parametrize('secret', ['PRIVATE_SENTINEL_PERSON', 'secret@example.invalid',
    'DE'+'9'*20, 'https://secret.invalid/account', 'AA1234567890',
    '987.654,3210', '09:42:10', '13.03.2025'])
def test_all_output_channels_are_free_of_invented_sensitive_sentinels(secret, capfd):
    raw = text_pdf((*FREE, secret))
    result = layout_report([raw])
    assert secret not in result and sha256(raw).hexdigest() not in result
    captured = capfd.readouterr()
    assert secret not in captured.out+captured.err


def test_coordinates_and_comparisons_without_raw_rows():
    source = text_pdf(SALE, TAX_PAGE)
    report = layout_report([source])
    assert 'Page 2, row 4' in report and 'Page 2, row 5' in report
    assert 'Tax read' in report
    for value in ('SYN777','XX0000000777','17,00','4,00','0,50','88,50','EUR','SYNTHETIC'):
        assert value not in report


@pytest.mark.parametrize('raw', [b'%PDF-1.7\nPRIVATE_SENTINEL_NATIVE\n%%EOF',
    b'PRIVATE_SENTINEL_NATIVE\x00\xff', text_pdf(('PRIVATE_SENTINEL_NATIVE',))[:50]])
def test_malformed_pdf_never_leaks_native_exception(raw, capfd):
    draft = parse_consors_pdf(raw)
    report = layout_report([raw])
    captured = capfd.readouterr()
    assert 'PRIVATE_SENTINEL_NATIVE' not in repr(draft)+report+captured.out+captured.err
    assert draft.kind is None


def test_parser_exception_is_fixed_message(monkeypatch):
    def fail(*args):
        raise ValueError('PRIVATE_SENTINEL raw private text')
    monkeypatch.setattr('portfolio_app.consors_pdf._rows', fail)
    assert parse_consors_pdf(b'synthetic').issues == (
        'PDF could not be read. Use a text-based, unencrypted statement.',)


def test_scanned_encrypted_and_page_limit_documents():
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.draw_rect(pymupdf.Rect(40,40,180,180), fill=(0,0,0))
        assert parse_consors_pdf(doc.tobytes()).kind is None
        page.insert_text((40,30),'PRIVATE_SENTINEL_ENCRYPTED')
        encrypted = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='secret')
        assert parse_consors_pdf(encrypted).kind is None
        assert 'PRIVATE_SENTINEL' not in layout_report([encrypted])
    supported = text_pdf(FREE, *((f'Seite {i} von 30',) for i in range(2,31)))
    assert parse_consors_pdf(supported).zero_statement_charges
    excessive = text_pdf(FREE, *((f'Seite {i} von 31',) for i in range(2,32)))
    assert parse_consors_pdf(excessive).kind is None


def test_file_and_batch_limits_are_checked_before_parsing(monkeypatch):
    source = text_pdf(FREE)
    monkeypatch.setattr('portfolio_app.consors_pdf.MAX_BYTES', len(source))
    assert parse_consors_pdf(source).zero_statement_charges
    assert parse_consors_pdf(source+b' ').issues == ('PDF exceeds the size limit.',)
    with pytest.raises(ValueError, match='100 statements'):
        parse_consors_batch([source]*101)
    monkeypatch.setattr('portfolio_app.consors_pdf.MAX_BATCH_BYTES', len(source)*2)
    assert len(parse_consors_batch([source,source])) == 2
    with pytest.raises(ValueError, match='100 MB'):
        parse_consors_batch([source,source,b' '])


def test_report_distinguishes_exact_and_tolerated_differences_without_amounts():
    exact = layout_report([text_pdf(FREE)])
    assert 'Settlement matches exactly.' in exact
    assert 'Quantity times quoted price: exact gross match.' in exact
    rounded = tuple(row.replace('31,250000','31,250001') for row in FREE)
    rounded = (*rounded[:5], rounded[5].replace('125,00','125,01'), *rounded[6:])
    report = layout_report([text_pdf(rounded)])
    assert 'Settlement has a nonzero difference within rounding tolerance.' in report
    assert 'Quantity times quoted price: nonzero difference within rounding tolerance.' in report
    assert '125,01' not in report and '31,250001' not in report


def test_masked_report_identifies_contact_labels_but_never_phone_numbers():
    report = layout_report([text_pdf((*FREE,
        'Kontakt Tel.: +00 (0) 000 / 11111, Fax: +00 (0) 000 / 22222, Service'))])
    assert 'TEL.:' in report and 'FAX:' in report
    assert 'Automatic zero fees: eligible.' in report
    for value in ('+00','11111','22222'):
        assert value not in report


def test_sale_report_has_fixed_parser_issues_even_when_settlement_balances():
    report = layout_report([text_pdf(SALE,TAX_PAGE,('Unknown charge 6,75 EUR',))])
    assert 'Issue: Unclassified monetary row; review the statement.' in report
    assert 'Settlement matches exactly.' in report
    assert '6,75' not in report


def test_report_labels_information_without_exporting_amounts_or_treating_it_as_tax():
    report = layout_report([text_pdf((*SALE, 'Limitkurs 43,750000 EUR'),
                                    TAX_PAGE, TAX_INFORMATION)])
    assert 'Parsed fee rows: 1; parsed tax rows: 2.' in report
    assert 'Order limit (informational)' in report
    assert report.count('Tax calculation (informational)') == 14
    assert 'unclassified monetary row' not in report
    for secret in ('43,750000', '17,50', '246,00', '12,50', 'EUR', 'SYN777'):
        assert secret not in report
    invalid = layout_report([text_pdf((*FREE, 'Limitkurs 43,75 EUR Unknown 6,75 EUR'))])
    assert 'Order limit unreadable; unclassified monetary row' in invalid


def test_rotated_document_reference_has_a_safe_diagnostic_label():
    source = with_margin_reference(text_pdf(FREE))
    report = layout_report([source])
    assert '<document-reference>' in report
    assert DOCUMENT_REFERENCE not in report and '1ABC' not in report
    assert 'Automatic zero fees: eligible.' in report
    assert 'unclassified monetary row' not in report


def test_supplementary_review_report_preserves_coordinates_but_no_values():
    report = layout_report([text_pdf(LOSS_SALE,SUPPLEMENTARY_DETAILS)])
    assert 'Review: Supplementary rows were excluded from transaction values' in report
    assert 'Page 2, row 2' in report and 'Page 2, row 5' in report
    assert report.count('Supplementary tax detail (review required)') == 3
    assert 'Issue:' not in report and 'Settlement matches exactly.' in report
    assert 'Parsed fee rows: 1; parsed tax rows: 0.' in report
    for value in ('22,50','11,25','39,50','SYN777','XX0000000777','synthetische','EUR'):
        assert value not in report
