"""Independent economic oracles and fail-closed format challenges."""
from decimal import Decimal as D

import pytest

from consors_audit_fixtures import BUY, FREE, SALE, LOSS_SALE, SUPPLEMENTARY_DETAILS, TAX_PAGE, TAX_INFORMATION, LEGACY, COMPANY_FOOTER, DOCUMENT_REFERENCE, text_pdf, cell_pdf, with_margin_reference
from portfolio_app.consors_pdf import parse_consors_pdf, _parse_rows
from portfolio_app.transaction_imports import as_event, draft_problems
from portfolio_app.account_journal import Journal


def blocking(draft):
    return draft_problems(draft, {'isin': 'XX0000000777'}, Journal(version=2))


@pytest.mark.parametrize('source', [text_pdf(BUY), cell_pdf(), cell_pdf(reverse=True), cell_pdf(jitter=2),
    text_pdf(tuple('  '.join(r.swapcase().split()) for r in BUY))],
    ids=['text-lines', 'cells', 'reverse-cells', 'offset-cells', 'case-and-spacing'])
def test_independent_layouts_have_exact_buy_economics(source):
    draft = parse_consors_pdf(source)
    assert not blocking(draft)
    event = as_event(draft, charges_known=True)
    assert (event.kind, event.date, event.value_date, event.quantity, event.amount, event.currency, event.price) == (
        'buy', '2025-03-13', '2025-03-17', D('4'), D('125'), 'EUR', None)
    assert [(c.kind, c.amount, c.currency) for c in event.charges] == [('fee', D('1'), 'EUR')]
    assert draft.unit_price.amount == D('31.250000') and draft.trade_time == '09:42:10'


def test_multipage_sale_keeps_equal_joint_taxes_and_provenance():
    draft = parse_consors_pdf(text_pdf(SALE, TAX_PAGE))
    assert not blocking(draft)
    event = as_event(draft, charges_known=True)
    assert (event.kind, event.quantity, event.amount, event.value_date) == ('sell', D(2), D(90), '2025-03-20')
    assert [(c.kind, c.amount) for c in event.charges] == [('fee', D('.50')), ('tax', D('.50')), ('tax', D('.50'))]
    assert [(p.page, p.row) for p in draft.provenance if p.field == 'Tax'] == [(2, 4), (2, 5)]
    assert 'Tax: page 2, row 4' in event.note
    assert 'SYNTHETIC ORCHARD' not in event.note


def test_legacy_explicit_currency_wkn_only_savings_plan():
    draft = parse_consors_pdf(text_pdf(LEGACY))
    assert not blocking(draft)
    assert (draft.wkn, draft.isin, draft.quantity, draft.gross.amount) == ('SYN777', None, D('.625'), D(20))
    assert draft.zero_statement_charges


@pytest.mark.parametrize('extra', [
    'Unknown refund - 7,25 EUR', 'Unknown debit 7,25 DEM', 'Unknown debit 7,25 EUR',
    'Unknown charge 7,25', 'Unknown refund - 7,25',
    'Unknown debit EUR 7,25 extra words', 'Unknown debit 7,25 €',
    'Provision - 7,25 EUR', 'Provision EUR 7,25-', 'Provision + 7,25 EUR',
    'Provision – 7,25 EUR', 'Provision 7,25 EUR −',
    'zzgl. UNKNOWN 7,25 EUR', 'Neuabrechnung', 'Storno', 'Stornierung', 'Berichtigung',
    'Korrektur', 'Devisenkurs EUR USD',
    'Kurswert EUR 125,00', 'Kurswert EUR 126,00', 'ST 4,00000',
])
def test_unsupported_rows_block_even_when_other_totals_balance(extra):
    # Unicode minus exercised directly as base-font PDF cannot represent it.
    draft = _parse_rows([*FREE, extra], 'invented')
    assert blocking(draft), extra
    assert not draft.zero_statement_charges


@pytest.mark.parametrize('quantity', ['ST 4,00000 -', 'ST - 4,00000', 'ST 4,00000+', 'ST 4.00', 'ST 4,00000 8,00000'])
def test_quantity_is_not_partially_parsed(quantity):
    draft = parse_consors_pdf(text_pdf((*FREE[:2], quantity, *FREE[3:])))
    assert blocking(draft)
    assert not draft.zero_statement_charges


@pytest.mark.parametrize('before,after', [
    ('Zulasten', 'Zugunsten'), ('31,250000', '0,000000'),
    ('125,00', '0,00'), ('XX0000000777', 'XX0000000888'),
])
def test_invalid_financial_fields_are_blocking(before, after):
    draft = parse_consors_pdf(text_pdf(tuple(r.replace(before, after) for r in FREE)))
    assert blocking(draft)
    assert not draft.zero_statement_charges or before == 'XX0000000777'


@pytest.mark.parametrize('extra', ['CBL 000987', 'CBF 000987', 'BLZ 00000000', 'BIC SYNTHXXX',
    'Seite 1 VON 2', 'Bestand zugunsten Wertpapierrechnung Luxemburg'])
def test_nonmonetary_metadata_preserves_automatic_zero(extra):
    draft = parse_consors_pdf(text_pdf((*FREE, extra)))
    assert not blocking(draft) and draft.zero_statement_charges


@pytest.mark.parametrize('row', ['KapSt-pflichtiger Kapitalertrag - 17,00 EUR',
    'Kapitalertragsteuer-Bemessungsgrundlage 4,00 EUR'])
def test_tax_bases_are_not_tax_charges(row):
    draft = parse_consors_pdf(text_pdf((*FREE, row)))
    assert not blocking(draft) and not draft.taxes


@pytest.mark.parametrize('index', range(len(FREE)-1))
def test_missing_essential_row_cannot_save_or_autoconfirm(index):
    draft = parse_consors_pdf(text_pdf(FREE[:index]+FREE[index+1:]))
    assert blocking(draft) and not draft.zero_statement_charges


def test_price_rounding_preserves_gross_without_manufactured_unit_price():
    lines = tuple(r.replace('4,00000', '0,33333').replace('31,250000', '30,000000').replace('125,00', '10,00') for r in FREE)
    draft = parse_consors_pdf(text_pdf(lines))
    assert not blocking(draft)
    assert as_event(draft).amount == D(10) and as_event(draft).price is None
    assert draft.unit_price.amount == D('30')
    assert draft.zero_statement_charges and as_event(draft).charges_known


@pytest.mark.parametrize('lines', [
    tuple(r.replace('31,250000 EUR', '31,250000') for r in FREE),
    tuple(r.replace('EUR', 'DEM') for r in FREE),
    tuple(r.replace('EUR', 'USD') if r.startswith('Zulasten') else r for r in FREE),
    (*FREE, 'Kauf AM 13.03.2025'),
    (*FREE, 'Valuta 18.03.2025'),
    tuple(r.replace('13.03.2025', '31.02.2025') for r in FREE),
    tuple(r.replace('09:42:10', '25:42:10') for r in FREE),
])
def test_ambiguous_dates_currencies_and_multiple_executions_fail_closed(lines):
    assert blocking(parse_consors_pdf(text_pdf(lines)))


def test_separate_errors_do_not_cancel_to_make_zero_fees():
    draft = parse_consors_pdf(text_pdf((*FREE, 'Unknown charge 7,25 EUR', 'Unknown refund - 7,25 EUR')))
    assert blocking(draft) and not draft.zero_statement_charges


def test_zero_trade_cannot_be_retained_as_execution():
    draft = parse_consors_pdf(text_pdf(tuple(r.replace('31,250000','0,000000').replace('125,00','0,00') for r in FREE)))
    assert blocking(draft)


def test_repeated_equal_fields_keep_conflicting_source_locations():
    draft = parse_consors_pdf(text_pdf(FREE, ('Kurswert EUR 125,00',)))
    assert blocking(draft)
    assert [(p.page,p.row) for p in draft.provenance if p.field == 'Gross amount'] == [(1,5),(2,1)]


def test_refund_sign_cannot_be_hidden_by_unicode_normalization():
    draft = _parse_rows([*FREE, 'Provision \u2212 3,00 EUR'], 'invented')
    assert blocking(draft)


@pytest.mark.parametrize('row', [
    'Kurswert 999,00 EUR 125,00 EUR', 'Provision 9,00 EUR 0,00 EUR',
    'Preis pro Anteil 999,00 EUR 31,250000 EUR',
    'KapSt 9,00 EUR 0,00 EUR', 'Zulasten 7,25 USD 125,00 EUR',
])
def test_rightmost_amount_is_not_enough_to_resolve_extra_values(row):
    field = row.split()[0]
    originals = tuple(r for r in FREE if not r.startswith(field))
    draft = parse_consors_pdf(text_pdf((*originals, row)))
    assert blocking(draft)
    assert not draft.zero_statement_charges


def test_unrelated_document_date_cannot_supply_missing_trade_date():
    rows = ('Kauf', *FREE[1:], 'Erstellt AM 13.03.2025 UM 09:42:10')
    draft = parse_consors_pdf(text_pdf(rows))
    assert draft.trade_date is None and draft.trade_time is None and blocking(draft)


@pytest.mark.parametrize('time', ['09:42', '09:42:1', 'UNREADABLE'])
def test_explicit_unreadable_time_is_not_treated_as_absent(time):
    rows = tuple(r.replace('09:42:10', time) for r in FREE)
    assert blocking(parse_consors_pdf(text_pdf(rows)))


@pytest.mark.parametrize('label', ['Provision Erstattung', 'KapSt Erstattung',
    'Kapitalertragsteuer Bemessungsgrundlage', 'Provision enthalten'])
def test_charge_prefix_cannot_hide_refund_or_tax_calculation_heading(label):
    draft = parse_consors_pdf(text_pdf((*FREE, f'{label} 0,00 EUR')))
    assert blocking(draft) and not draft.zero_statement_charges


@pytest.mark.parametrize('metadata', [
    '00000 SYNTHETIC Seite: 2 VON 4',
    'Tel +00 (0) 000 / 11111, Fax +00 (0) 000 / 22222, Service',
    'Kontakt Tel.: +00 (0) 000 / 11111, Fax: +00 (0) 000 / 22222, Service',
    'Telefon +00 (0) 000 / 11111, Telefax +00 (0) 000 / 22222',
])
def test_embedded_page_counter_and_contact_numbers_are_not_charges(metadata):
    # Independently invented metadata, never a copied statement/footer.
    draft = parse_consors_pdf(text_pdf((*FREE, metadata)))
    assert not blocking(draft)
    assert draft.zero_statement_charges


@pytest.mark.parametrize('extra', [
    'Unbekannte Belastung 6,75 EUR', 'Unbekannte Belastung 6,75',
    'EUR 6,75', 'DEM 6,75', 'Unbekannte Erstattung - 6,75 EUR',
])
@pytest.mark.parametrize('metadata', [
    '00000 SYNTHETIC Seite: 2 VON 4',
    'Tel +00 (0) 000 / 11111, Fax +00 (0) 000 / 22222,',
    'Kontakt Tel.: +00 (0) 000 / 11111, Fax: +00 (0) 000 / 22222,',
])
def test_metadata_exclusion_does_not_hide_money_elsewhere_on_same_row(metadata, extra):
    draft = parse_consors_pdf(text_pdf((*FREE, metadata+' '+extra)))
    assert blocking(draft)
    assert not draft.zero_statement_charges


@pytest.mark.parametrize('row', ['Fax 6,75 EUR', 'Tel. 6,75', 'UnknownCode DEM 6',
    'Tel +00 (0) 000 / 11111 EUR', 'Fax +00 (0) 000 / 11111 DEM'])
def test_contact_words_are_not_a_general_money_exemption(row):
    assert blocking(parse_consors_pdf(text_pdf((*FREE,row))))


@pytest.mark.parametrize('row', TAX_INFORMATION)
@pytest.mark.parametrize('variant', ['original', 'case-spacing'])
def test_exact_tax_information_is_not_a_charge(row, variant):
    if variant == 'case-spacing':
        row = '  '.join(row.swapcase().split())
    draft = parse_consors_pdf(text_pdf((*FREE, row)))
    assert not blocking(draft) and draft.zero_statement_charges
    assert draft.taxes == () and draft.fees == ()
    assert draft.provenance[-1].field == 'Tax calculation'


@pytest.mark.parametrize('row', (*TAX_INFORMATION, 'Limitkurs 43,750000 EUR',
    'KapSt-pflichtiger Kapitalertrag - 17,00 EUR'))
@pytest.mark.parametrize('suffix', [' Unknown charge 6,75 EUR', ' 6,75', ' Erstattung'])
def test_known_informational_label_never_hides_extra_amount_or_qualifier(row, suffix):
    draft = parse_consors_pdf(text_pdf((*FREE, row+suffix)))
    assert blocking(draft) and not draft.zero_statement_charges


@pytest.mark.parametrize('row', [
    'Limitkurs - 43,75 EUR', 'Limitkurs 43,75 DEM', 'Limitkurs 43,75',
    'KapSt-pflichtiger Kapitalertrag', 'KapSt-pflichtige Kapitalerträge UNKNOWN',
    'Bemessungsgrundlage für Kapitalertragsteuer 4,00 DEM',
    'Verrechnungstopf Unbekannt nach dem Geschäft 4,00 EUR',
])
def test_unsupported_information_stays_blocked(row):
    assert blocking(parse_consors_pdf(text_pdf((*FREE,row))))


def test_order_limit_and_tax_details_preserve_sale_economics_and_actual_taxes():
    draft = parse_consors_pdf(text_pdf((*SALE, 'Limitkurs 43,750000 EUR'),
                                     TAX_PAGE, TAX_INFORMATION))
    assert not blocking(draft)
    assert not draft.zero_statement_charges
    event = as_event(draft, charges_known=True)
    assert (event.kind, event.quantity, event.amount) == ('sell', D(2), D(90))
    assert draft.unit_price.amount == D(45) and draft.settlement.amount == D('88.50')
    assert [(c.kind,c.amount) for c in event.charges] == [
        ('fee', D('.50')), ('tax', D('.50')), ('tax', D('.50'))]
    assert 'Order limit: page 1, row 9' in event.note
    assert 'Tax calculation: page 3, row 1' in event.note


def test_unrecognized_charge_on_tax_information_page_stays_blocked():
    draft = parse_consors_pdf(text_pdf(SALE, TAX_PAGE,
        (*TAX_INFORMATION, 'Unknown tax adjustment 6,75 EUR')))
    assert blocking(draft)
    assert len(draft.taxes) == 2


@pytest.mark.parametrize('row', [COMPANY_FOOTER, COMPANY_FOOTER.swapcase(),
    COMPANY_FOOTER.replace('IdNr. ', 'IdNr.: '), COMPANY_FOOTER+', Service'])
def test_company_footer_preserves_zero_fees_with_rounded_execution_price(row):
    rows = tuple(r.replace('31,250000', '31,250001') for r in FREE)
    draft = parse_consors_pdf(text_pdf((*rows, row)))
    assert not blocking(draft) and draft.zero_statement_charges
    assert as_event(draft).charges_known and not as_event(draft).charges
    assert draft.gross.amount == D(125)


@pytest.mark.parametrize('extra', ['Unknown charge 6,75 EUR', 'Unknown charge 6,75',
    'DEM 6,75', 'Unknown refund - 6,75 EUR'])
@pytest.mark.parametrize('before', [False, True])
def test_vat_metadata_does_not_hide_other_amounts(extra, before):
    row = extra+' '+COMPANY_FOOTER if before else COMPANY_FOOTER+' '+extra
    draft = parse_consors_pdf(text_pdf((*FREE,row)))
    assert blocking(draft) and not draft.zero_statement_charges


@pytest.mark.parametrize('row', ['Reference 12345 USt', 'Unknown 12345 UST',
    'Reference 12345 USt-IdNr. UNKNOWN', 'Reference 12345 USt-IdNr. DE00000000',
    'Reference 12345 USt-IdNr. DE0000000000'])
def test_ust_is_not_a_general_currency_exemption(row):
    assert blocking(parse_consors_pdf(text_pdf((*FREE,row))))


@pytest.mark.parametrize('rotation', [0,90,270])
@pytest.mark.parametrize('chunk', ['1ABC','1AB1','1AED','ABCD','1234'])
def test_margin_reference_content_and_rotation_cannot_change_trade_economics(rotation, chunk):
    source = text_pdf((*FREE, COMPANY_FOOTER))
    reference = DOCUMENT_REFERENCE.replace('1ABC',chunk)
    draft = parse_consors_pdf(with_margin_reference(source, reference, rotation=rotation))
    assert not blocking(draft) and draft.zero_statement_charges
    event = as_event(draft)
    assert (event.quantity,event.amount,event.charges_known,event.charges) == (D(4),D(125),True,())
    assert reference not in event.note


@pytest.mark.parametrize('extra', ['Unknown charge 6,75 EUR', 'Unknown charge 6,75',
    'Unknown refund - 6,75 EUR', 'Unknown charge 1ABC', 'DEM 6,75'])
@pytest.mark.parametrize('same_row', [False,True])
def test_document_reference_cannot_hide_adjacent_amounts(extra, same_row):
    source = (text_pdf((*FREE, DOCUMENT_REFERENCE+' '+extra)) if same_row else
              with_margin_reference(text_pdf((*FREE,extra))))
    draft = parse_consors_pdf(source)
    assert blocking(draft) and not draft.zero_statement_charges


@pytest.mark.parametrize('reference', [
    DOCUMENT_REFERENCE[:-1], DOCUMENT_REFERENCE+'0',
    DOCUMENT_REFERENCE.replace('00000000-', '0000000G-', 1),
    DOCUMENT_REFERENCE+'-1', '1ABC', '6,75AED',
])
def test_partial_or_nonhex_reference_is_not_a_money_exemption(reference):
    assert blocking(parse_consors_pdf(text_pdf((*FREE,reference))))


@pytest.mark.parametrize('rotation', [90,270])
@pytest.mark.parametrize('amount', ['6,75EUR','6,75DEM','-6,75EUR'])
def test_vertical_money_is_not_ignored_based_on_orientation(rotation, amount):
    draft = parse_consors_pdf(with_margin_reference(text_pdf(FREE), amount, rotation=rotation))
    assert blocking(draft) and not draft.zero_statement_charges


def test_supplementary_loss_details_do_not_supply_trade_values_or_charges():
    source = text_pdf((*LOSS_SALE, 'Verbleibender synthetischer Bestand ST 7,25000'),
                      SUPPLEMENTARY_DETAILS)
    draft = parse_consors_pdf(source)
    assert not blocking(draft) and draft.review_notes
    event = as_event(draft)
    assert (event.kind,event.quantity,event.amount,event.charges_known) == ('sell',D(2),D(40),False)
    assert [(c.kind,c.amount) for c in event.charges] == [('fee',D('.50'))]
    assert draft.settlement.amount == D('39.50')
    assert [(p.field,p.page,p.row) for p in draft.provenance if p.field.startswith('Supplementary')] == [
        ('Supplementary quantity',1,9), ('Supplementary tax detail',2,2),
        ('Supplementary tax detail',2,4), ('Supplementary tax detail',2,5)]
    assert 'Rechengröße' not in event.note and '7,25000' not in event.note


@pytest.mark.parametrize('label', ['Unbekannte Rechengröße', 'Abweichender Rechenposten',
    'Invented calculation alpha', 'Invented calculation beta'])
@pytest.mark.parametrize('value', ['17,25 EUR','- 17,25 EUR','EUR 17,25','EUR - 17,25'])
def test_unseen_labels_in_tax_context_are_reviewable_not_financial_inputs(label, value):
    draft = parse_consors_pdf(text_pdf(FREE,('Details zur Investmentbesteuerung',label+' '+value)))
    assert not blocking(draft) and draft.review_notes and not draft.zero_statement_charges
    assert as_event(draft).amount == D(125) and not as_event(draft).charges_known
    assert not draft.fees and not draft.taxes


@pytest.mark.parametrize('pages', [
    ((*FREE,'Unknown calculation 17,25 EUR'),),
    (FREE,('Unknown calculation 17,25 EUR',)),
    (('Details zur Investmentbesteuerung','Unknown calculation 17,25 EUR'),FREE),
    (FREE,('Details zur Investmentbesteuerung',),('Unknown calculation 17,25 EUR',)),
    (FREE,('Details zur Investmentbesteuerung','Hinweis für Orderabrechnungen:',
           'Unknown calculation 17,25 EUR')),
    ((*FREE[:5],'Unbekannter Bestand ST 7,25',*FREE[5:]),),
])
def test_supplementary_context_requires_settlement_and_does_not_leak(pages):
    assert blocking(parse_consors_pdf(text_pdf(*pages)))


@pytest.mark.parametrize('extra', [
    'Unknown charge 6,75 EUR', 'Unknown refund - 6,75 EUR',
    'Neue Gebühr 6,75 EUR', 'abzgl. Unbekannt 6,75 EUR', 'KapSt Erstattung 6,75 EUR',
    'Unbekannte Rechengröße 6,75 DEM', 'Unbekannte Rechengröße 6,75 EUR 6,75 EUR',
    'KapSt-pflichtige Kapitalerträge 17,25 EUR 6,75 EUR',
    'ST 4,00000', 'Kurswert 125,00 EUR', 'Devisenkurs USD EUR',
])
def test_supplementary_section_does_not_hide_charges_ambiguities_or_conversion(extra):
    draft = parse_consors_pdf(text_pdf(FREE,('Details zur Investmentbesteuerung',extra)))
    assert blocking(draft) and not draft.zero_statement_charges


def test_balanced_cancelling_adjustments_still_block_in_tax_context():
    draft = parse_consors_pdf(text_pdf(FREE,('Details zur Investmentbesteuerung',
        'Unknown charge 6,75 EUR','Unknown refund - 6,75 EUR')))
    assert blocking(draft) and not draft.zero_statement_charges


def test_reconciliation_never_manufactures_a_missing_charge_from_supplementary_amount():
    rows = tuple(r.replace('39,50','38,50') for r in LOSS_SALE)
    draft = parse_consors_pdf(text_pdf(rows,('Details zur Investmentbesteuerung',
        'Unbekannte Rechengröße 1,00 EUR')))
    assert blocking(draft)
    assert 'Settlement does not match the gross amount and listed fees/taxes.' in draft.issues
    assert [f.amount for f in draft.fees] == [D('.50')] and not draft.taxes


def test_explicit_tax_charge_in_supplementary_section_is_still_counted():
    rows = tuple(r.replace('39,50','38,75') for r in LOSS_SALE)
    draft = parse_consors_pdf(text_pdf(rows,(*SUPPLEMENTARY_DETAILS,'KapSt 0,75 EUR')))
    assert not blocking(draft)
    assert [t.amount for t in draft.taxes] == [D('.75')]
    assert [(c.kind,c.amount) for c in as_event(draft).charges] == [('fee',D('.50')),('tax',D('.75'))]


@pytest.mark.parametrize('quantity,price', [
    ('1','125,000000'), ('1,00000','125,000000'),
    ('2','62,500000'), ('2,00000','62,500000'), ('0,62500','200,000000'),
])
def test_execution_quantity_one_or_fraction_does_not_trigger_supplementary_review(quantity, price):
    rows = tuple(r.replace('ST 4,00000','ST '+quantity).replace('31,250000',price) for r in FREE)
    draft = parse_consors_pdf(text_pdf(rows))
    assert not blocking(draft) and not draft.review_notes and draft.zero_statement_charges
    assert draft.quantity == D(quantity.replace(',','.')) and draft.gross.amount == D(125)
    assert [(p.page,p.row) for p in draft.provenance if p.field == 'Quantity'] == [(1,3)]


@pytest.mark.parametrize('quantity', ['1','1,00000','2','2,00000','0,62500'])
def test_supplementary_st_quantity_review_is_independent_of_integer_or_decimal_value(quantity):
    draft = parse_consors_pdf(text_pdf((*FREE,'Synthetic remaining quantity ST '+quantity)))
    assert not blocking(draft) and draft.review_notes and not draft.zero_statement_charges
    assert draft.quantity == D(4) and draft.gross.amount == D(125)
    assert [(p.page,p.row) for p in draft.provenance if p.field == 'Supplementary quantity'] == [(1,8)]
