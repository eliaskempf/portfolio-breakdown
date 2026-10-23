"""Independently invented statement fixtures; never read working portfolio data."""
from decimal import Decimal

import pytest

pdf = pytest.importorskip('pymupdf')
from portfolio_app.consors_pdf import parse_consors_pdf, parse_consors_batch


def statement(*, sale=False, fees=True, taxes=False, quantity='2,00000', price='50,00',
              gross='100,00', net=None, extra=(), missing=(), reverse=False):
    charge = (7 if fees else 0)+(3 if taxes else 0)
    total = 100-charge if sale else 100+charge
    rows = [
        ('title', [(40, 'Verkauf' if sale else 'Kauf'), (160, 'AM 07.02.2024 UM 10:30:00')]),
        ('head', [(40, 'Bezeichnung'), (310, 'WKN'), (410, 'ISIN')]),
        ('identity', [(40, 'INVENTED FUND'), (310, 'SYN001'), (410, 'XX0000000000')]),
        ('quantity', [(40, 'ST'), (160, quantity)]),
        ('price', [(40, 'Preis pro Anteil'), (350, price), (460, 'EUR')]),
        ('gross', [(40, 'Kurswert'), (350, 'EUR'), (460, gross)]),
    ]
    if fees:
        rows += [('fee1', [(40, 'abzgl. Provision'), (350, '5,00 EUR')]),
                 ('fee2', [(40, 'Börsenplatzgebühr'), (350, 'EUR 2,00')])]
    if taxes:
        rows += [('tax', [(40, 'abzgl. Kapitalertragsteuer'), (350, '3,00 EUR')])]
    rows += [('net', [(40, 'Zugunsten IBAN INVENTED' if sale else 'Zulasten IBAN INVENTED'),
                     (350, net or f'{total},00'), (460, 'EUR')]),
             ('date', [(40, 'Valuta 09.02.2024')])]
    rows += [('extra', [(40, row)]) for row in extra]
    with pdf.open() as doc:
        page = doc.new_page()
        cells = [(x, 45+n*28, text) for n, (key, columns) in enumerate(rows) if key not in missing
                 for x, text in columns]
        for x, y, text in reversed(cells) if reverse else cells:
            page.insert_text((x, y), text, fontsize=9)
        return doc.tobytes()


@pytest.mark.parametrize('sale', [False, True])
@pytest.mark.parametrize('reverse', [False, True])
def test_reads_trade_and_decimal_fees_independently_of_pdf_object_order(sale, reverse):
    draft = parse_consors_pdf(statement(sale=sale, taxes=True, reverse=reverse))
    assert draft.kind == ('sell' if sale else 'buy')
    assert draft.trade_date == '2024-02-07'
    assert draft.trade_time == '10:30:00'
    assert draft.settlement_date == '2024-02-09'
    assert draft.quantity == Decimal('2')
    assert draft.isin == 'XX0000000000' and draft.wkn == 'SYN001'
    assert draft.unit_price.amount == Decimal('50')
    assert draft.gross.amount == Decimal('100')
    assert sum(f.amount for f in draft.fees) == Decimal('7')
    assert sum(t.amount for t in draft.taxes) == Decimal('3')
    assert draft.settlement.amount == Decimal('90' if sale else '110')
    assert draft.issues == ('Confirm fees and taxes are complete before importing.',)


def test_balanced_purchase_has_zero_statement_charges_without_invented_fee_rows():
    draft = parse_consors_pdf(statement(fees=False))
    assert draft.fees == () and draft.taxes == ()
    assert draft.zero_statement_charges
    assert 'Confirm fees and taxes are complete before importing.' in draft.issues


@pytest.mark.parametrize('missing,issue', [
    ('quantity', 'Quantity is missing.'), ('identity', 'Instrument identity is missing.'),
    ('price', 'Unit price is missing.'), ('net', 'Settlement amount is missing.'),
])
def test_missing_required_fields_are_reported(missing, issue):
    assert issue in parse_consors_pdf(statement(missing=(missing,))).issues


@pytest.mark.parametrize('kwargs,issue', [
    ({'quantity':'3'}, 'Quantity times price does not match the gross amount.'),
    ({'net':'123,45'}, 'Settlement does not match the gross amount and listed fees/taxes.'),
    ({'extra':('Kurswert 200,00 EUR',)}, 'Gross amount is ambiguous; review the statement.'),
    ({'extra':('Storno',)}, 'Correction or currency conversion requires manual review.'),
])
def test_inconsistent_or_ambiguous_statements_require_resolution(kwargs, issue):
    assert issue in parse_consors_pdf(statement(**kwargs)).issues


def test_fractional_quantities_and_german_thousands():
    draft = parse_consors_pdf(statement(quantity='12,34567', price='100,00', gross='1.234,57', net='1.241,57'))
    assert draft.quantity == Decimal('12.34567')
    assert draft.gross.amount == Decimal('1234.57')
    assert draft.issues == ('Confirm fees and taxes are complete before importing.',)


def test_batch_keeps_invalid_and_duplicate_files_without_dropping_distinct_trades():
    first = statement()
    drafts = parse_consors_batch([first, statement(sale=True), first, b'PRIVATE INVALID PDF', statement()])
    assert len(drafts) == 5
    assert drafts[2].duplicate_of == 0
    assert all(drafts[n].duplicate_of is None for n in (0, 1, 3, 4))
    assert drafts[3].issues == ('PDF could not be read. Use a text-based, unencrypted statement.',)
    assert 'PRIVATE' not in repr(drafts[3])


def test_scanned_and_encrypted_documents_fail_without_exposing_source():
    with pdf.open() as doc:
        doc.new_page()
        assert parse_consors_pdf(doc.tobytes()).kind is None
        doc[0].insert_text((40,40),'PRIVATE DATA')
        encrypted = doc.tobytes(encryption=pdf.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='PRIVATE')
        draft = parse_consors_pdf(encrypted)
        assert draft.kind is None and 'PRIVATE' not in repr(draft)


def test_multiple_trade_headings_cannot_silently_collapse_into_one():
    draft = parse_consors_pdf(statement(extra=('Kauf AM 07.02.2024',)))
    assert 'Multiple trade headings found; split or review the document.' in draft.issues


@pytest.mark.parametrize('suffix', ['P.ST. NETTO', 'P.ST. FRANCO COURTAGE', 'NETTO', 'FRANCO COURTAGE'])
@pytest.mark.parametrize('currency_first', [False, True])
def test_unit_price_annotations_keep_explicit_currency(suffix, currency_first):
    amount = 'EUR 50,000000' if currency_first else '50,000000 EUR'
    draft = parse_consors_pdf(statement(missing=('price',), extra=(f'Kurs {amount} {suffix}',)))
    assert draft.unit_price.amount == Decimal('50')
    assert draft.unit_price.currency == 'EUR'
    assert draft.issues == ('Confirm fees and taxes are complete before importing.',)


@pytest.mark.parametrize('row', [
    'Kurs 50,000000 P.ST. NETTO',  # no explicit currency
    'Kurs 50,000000 EUR UNKNOWN',
    'Kurs -50,000000 EUR',
    'Kurs 50,000000 EUR-',
])
def test_unreadable_price_has_one_specific_error_without_guessing(row):
    draft = parse_consors_pdf(statement(missing=('price',), extra=(row,)))
    assert draft.unit_price is None
    assert draft.issues == ('Unit price could not be read; review the statement.',
                            'Confirm fees and taxes are complete before importing.')


def test_unreadable_charge_does_not_report_a_spurious_settlement_difference():
    draft = parse_consors_pdf(statement(missing=('fee1',), extra=('Provision EUR unknown',)))
    assert draft.issues == ('Fee could not be read; review the statement.',
                            'Confirm fees and taxes are complete before importing.')


def test_tax_base_and_rate_are_not_imported_as_tax_amount():
    draft = parse_consors_pdf(statement(sale=True, net='90,00', extra=(
        'abzgl. Kapitalertragsteuer 25,00 % von 12,00 EUR 3,00 EUR',)))
    assert [tax.amount for tax in draft.taxes] == [Decimal('3')]
    assert draft.issues == ('Confirm fees and taxes are complete before importing.',)


def test_equal_separate_tax_rows_are_retained_not_silently_deduplicated():
    draft = parse_consors_pdf(statement(sale=True, net='87,00', extra=(
        'KAPST anteilig 50,00% 25,00% EUR 3,00',
        'KAPST anteilig 50,00% 25,00% EUR 3,00')))
    assert [tax.amount for tax in draft.taxes] == [Decimal('3'), Decimal('3')]
    assert draft.issues == ('Confirm fees and taxes are complete before importing.',)


@pytest.mark.parametrize('sale,suffix', [(False, 'NETTO'), (True, 'FRANCO COURTAGE')])
def test_fund_price_suffix_and_venue_costs_reconcile(sale, suffix):
    draft = parse_consors_pdf(statement(sale=sale, missing=('price',),
        net='84,00' if sale else '116,00', extra=(
            f'Preis pro Anteil 50,000000 EUR {suffix}',
            'abzgl. Handelsplatzkosten 9,00 EUR')))
    assert draft.unit_price.amount == Decimal('50')
    assert sum(fee.amount for fee in draft.fees) == Decimal('16')
    assert draft.issues == ('Confirm fees and taxes are complete before importing.',)


@pytest.mark.parametrize('net', ['93,00', '84,00'])
def test_unknown_deduction_is_never_silently_ignored_even_when_net_matches(net):
    draft = parse_consors_pdf(statement(sale=True, net=net,
        extra=('abzgl. UNKNOWN_DEDUCTION 9,00 EUR',)))
    assert draft.issues == ('Unrecognized deduction or addition; review the statement.',
                            'Confirm fees and taxes are complete before importing.')
    assert sum(fee.amount for fee in draft.fees) == Decimal('7')


@pytest.mark.parametrize('kwargs', [
    {'sale': True},
    {'net': '100,01'},  # zero inference requires exact equality, not cent tolerance
    {'extra': ('Separate debit 3,00 EUR',)},
    {'extra': ('Separate debit 3,00 USD',)},
    {'extra': ('Provision UNKNOWN',)},
    {'extra': ('abzgl. UNKNOWN 1,00 EUR',)},
    {'extra': ('Devisenkurs',)},
    {'extra': ('Kurswert 100,00 EUR',)},  # duplicated financial rows
    {'missing': ('price',)},
    {'missing': ('identity',)},
    {'missing': ('net',), 'extra': ('Zugunsten 100,00 EUR',)},
])
def test_uncertain_or_nonpurchase_statements_never_autoconfirm_zero(kwargs):
    draft = parse_consors_pdf(statement(fees=False, **kwargs))
    assert not draft.zero_statement_charges


def test_bank_routing_metadata_is_not_mistaken_for_an_extra_charge():
    draft = parse_consors_pdf(statement(fees=False, extra=('BLZ 00000000',)))
    assert draft.zero_statement_charges


def test_explicit_zero_fee_row_is_supported_without_a_savings_plan_label():
    draft = parse_consors_pdf(statement(fees=False, extra=('Provision 0,00 EUR',)))
    assert draft.zero_statement_charges
    assert draft.fees[0].amount == 0


@pytest.mark.parametrize('heading', [
    'KapSt-pflichtiger Kapitalertrag',
    'KAPST-PFLICHTIGER KAPITALERTRAG',
    'Kapitalertragsteuer-Bemessungsgrundlage',
])
def test_tax_calculation_headings_are_not_tax_charges(heading):
    draft = parse_consors_pdf(statement(sale=True, extra=(f'{heading} - 37,19 EUR',)))
    assert draft.taxes == ()
    assert draft.issues == ('Confirm fees and taxes are complete before importing.',)


def test_real_tax_charge_is_kept_alongside_taxable_income_calculation():
    draft = parse_consors_pdf(statement(sale=True, taxes=True,
        extra=('KapSt-pflichtiger Kapitalertrag 12,00 EUR',)))
    assert [tax.amount for tax in draft.taxes] == [Decimal('3')]
    assert draft.issues == ('Confirm fees and taxes are complete before importing.',)


@pytest.mark.parametrize('metadata', [
    'BLZ 00000000', 'BIC 00000000', 'Seite 1 VON 2', 'Seite 1 von 2',
])
def test_non_currency_metadata_does_not_prevent_automatic_zero_fees(metadata):
    draft = parse_consors_pdf(statement(fees=False, extra=(metadata,)))
    assert draft.zero_statement_charges


@pytest.mark.parametrize('row,field', [
    ('KapSt - 3,00 EUR', 'Tax'),
    ('Provision - 3,00 EUR', 'Fee'),
    ('Provision - EUR 3,00', 'Fee'),
])
def test_separated_sign_is_not_discarded_from_actual_charges(row, field):
    draft = parse_consors_pdf(statement(extra=(row,)))
    assert f'{field} could not be read; review the statement.' in draft.issues


@pytest.mark.parametrize('custodian', ['CBL', 'CBF'])
def test_custody_reference_after_settlement_does_not_block_zero_fees(custodian):
    draft = parse_consors_pdf(statement(fees=False, extra=(
        'Bestand zugunsten Wertpapierrechnung Luxemburg', f'{custodian} 000123')))
    assert draft.zero_statement_charges
    assert draft.issues == ('Confirm fees and taxes are complete before importing.',)


@pytest.mark.parametrize('currency', ['EUR', 'USD', 'CHF', 'JPY', 'KRW', 'SGD'])
def test_actual_currency_amount_still_blocks_zero_fees(currency):
    draft = parse_consors_pdf(statement(fees=False, extra=(f'{currency} 4,00',)))
    assert not draft.zero_statement_charges


def test_non_currency_code_cannot_be_used_as_transaction_currency():
    draft = parse_consors_pdf(statement(missing=('gross',), extra=('Kurswert 100,00 CBL',)))
    assert draft.gross is None
    assert 'Gross amount could not be read; review the statement.' in draft.issues
