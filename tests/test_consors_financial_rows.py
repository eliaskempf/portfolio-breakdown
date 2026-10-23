"""Complete-row validation with invented statements and temporary portfolios."""
from decimal import Decimal

import pytest

from consors_audit_fixtures import FREE, DOCUMENT_REFERENCE, text_pdf
from portfolio_app.account_journal import load_journal
from portfolio_app.consors_pdf import parse_consors_pdf
from portfolio_app.holdings import DataError
from portfolio_app.positions import read_snapshot
from portfolio_app.statement_diagnostics import layout_report
from portfolio_app.transaction_imports import REVIEW_CHARGES, save_pdf_drafts


# Generated, deliberately invalid account data; never a real account literal.
SYNTHETIC_COMPACT_IBAN = 'DE' + '0' * 20


def replace_row(prefix, replacement):
    return tuple(replacement if row.startswith(prefix) else row for row in FREE)


@pytest.mark.parametrize('prefix,amount,field', [
    ('Preis pro Anteil', '31,250000 EUR NETTO', 'Unit price'),
    ('Kurswert', '125,00 EUR', 'Gross amount'),
    ('Zulasten', '125,00 EUR', 'Settlement amount'),
])
@pytest.mark.parametrize('extra', ['999,00', '999', '-999,00', '999.00', '999,00 EUR', 'unknown'])
def test_financial_field_requires_complete_prefix(prefix, amount, field, extra):
    source = text_pdf(replace_row(prefix, f'{prefix} {extra} {amount}'))
    draft = parse_consors_pdf(source)
    assert draft.issues == (f'{field} could not be read; review the statement.', REVIEW_CHARGES)
    assert not draft.zero_statement_charges
    report = layout_report([source])
    assert f'{field} unreadable' in report
    assert 'Automatic zero fees: review required.' in report
    assert '999' not in report


@pytest.mark.parametrize('account', [
    '', f'IBAN {SYNTHETIC_COMPACT_IBAN}', 'IBAN DE00 0000 0000 0000 0000 00',
    'Konto-Nr. 0000000000',
])
@pytest.mark.parametrize('direction', ['Zulasten', 'Netto zulasten', 'ZU LASTEN'])
@pytest.mark.parametrize('amount', ['125,00 EUR', 'EUR 125,00', '125 EUR'])
def test_account_metadata_preserves_exact_purchase_and_zero_charges(account, direction, amount):
    draft = parse_consors_pdf(text_pdf(replace_row('Zulasten', f'{direction} {account} {amount}')))
    assert draft.issues == (REVIEW_CHARGES,)
    assert draft.settlement.amount == Decimal(125)
    assert draft.gross == draft.settlement
    assert draft.unit_price.amount == Decimal('31.25')
    assert draft.zero_statement_charges


@pytest.mark.parametrize('prefix', [
    'IBAN DE00 0000 0000 0000 0000 00 999',
    '999 IBAN DE00 0000 0000 0000 0000 00',
    f'IBAN {SYNTHETIC_COMPACT_IBAN} 999,00',
    'IBAN DE00 0000 0000 0000 0000 999,00',
    'Konto-Nr. 0000000000 999', 'Konto-Nr. 999,00',
    'Konto-Nr. 0000000000 Erstattung 999',
])
def test_account_metadata_cannot_hide_an_adjacent_amount(prefix):
    draft = parse_consors_pdf(text_pdf(replace_row('Zulasten', f'Zulasten {prefix} 125,00 EUR')))
    assert draft.issues == ('Settlement amount could not be read; review the statement.', REVIEW_CHARGES)
    assert draft.settlement is None and not draft.zero_statement_charges


def test_value_date_settlement_remains_readable_but_needs_charge_review():
    draft = parse_consors_pdf(text_pdf(replace_row('Zulasten', 'Wert 17.03.2025 EUR 125,00')))
    assert draft.issues == (REVIEW_CHARGES,)
    assert draft.settlement.amount == Decimal(125)
    assert draft.settlement_date == '2025-03-17'
    assert not draft.zero_statement_charges  # No explicit debit direction.


@pytest.mark.parametrize('rate', ['25 % von 4,00 EUR', 'anteilig 50% 25% von EUR 4,00'])
def test_explicit_tax_rate_and_base_preserve_only_the_charge(rate):
    rows = (*replace_row('Zulasten', 'Zulasten 126,00 EUR'), f'KapSt {rate} 1,00 EUR')
    draft = parse_consors_pdf(text_pdf(rows))
    assert draft.issues == (REVIEW_CHARGES,)
    assert [tax.amount for tax in draft.taxes] == [Decimal(1)]
    assert draft.gross.amount == Decimal(125)
    assert draft.settlement.amount == Decimal(126)
    assert not draft.zero_statement_charges


@pytest.mark.parametrize('prefix', ['999', '25% 999', '25% von 4,00 EUR 999', '25% von 4,00 USD'])
def test_tax_metadata_does_not_hide_unexplained_numbers_or_currency(prefix):
    draft = parse_consors_pdf(text_pdf((*FREE, f'KapSt {prefix} 0,00 EUR')))
    assert draft.issues == ('Tax could not be read; review the statement.', REVIEW_CHARGES)
    assert not draft.zero_statement_charges


@pytest.mark.parametrize('row', [
    'Servicegebühr 4', 'Servicegebuehr:4', 'Zusatzkosten 4', 'Handling fee 4',
    'Servicegebühr 0', 'Servicegebühr -4', 'Servicegebühr 4-', 'Servicegebühr +4',
    'Servicegebühr 1.000', 'Servicegebühr 4,0', 'Servicegebühr 4,00',
    'Servicegebühr 4.00', 'Servicegebühr 4..00', 'Servicegebühr 13.03.2025.99',
    'Servicegebühr am 13.03.2025 4',
])
@pytest.mark.parametrize('detail_page', [False, True])
def test_unresolved_charge_numbers_block_even_in_tax_details(row, detail_page):
    source = text_pdf(FREE, ('Details zur Investmentbesteuerung', row)) if detail_page else text_pdf((*FREE, row))
    draft = parse_consors_pdf(source)
    assert draft.issues == ('Unclassified monetary row; review the statement.', REVIEW_CHARGES)
    assert not draft.review_notes and not draft.zero_statement_charges
    assert not draft.fees and not draft.taxes
    assert any(p.field == 'Unclassified monetary row' for p in draft.provenance)
    report = layout_report([source])
    assert '[unclassified monetary row]' in report
    assert 'Automatic zero fees: review required.' in report
    assert 'Servicegebühr' not in report


@pytest.mark.parametrize('row', [
    'Seite 1 von 2', 'Valuta 17.03.2025', 'Referenz SYN1234', DOCUMENT_REFERENCE,
    'Hinweis 4', 'Beratungsfreies Geschäft',
    'Informationen zu Servicegebühren ab 13.03.2025',
    'Informationen zu Servicegebühren ab 2025-03-13',
    'Servicegebühren Referenz SYN1234', f'Servicegebühren Referenz {DOCUMENT_REFERENCE}',
    'Servicegebühren Stand 09:42:10',
])
def test_non_amount_metadata_does_not_invent_charges(row):
    draft = parse_consors_pdf(text_pdf((*FREE, row)))
    assert draft.issues == (REVIEW_CHARGES,)
    assert draft.zero_statement_charges


@pytest.mark.parametrize('charges_known', [False, True])
@pytest.mark.parametrize('rows', [
    replace_row('Preis pro Anteil', 'Preis pro Anteil 999,00 31,250000 EUR NETTO'),
    replace_row('Kurswert', 'Kurswert 999,00 125,00 EUR'),
    replace_row('Zulasten', 'Zulasten 999,00 125,00 EUR'),
    (*FREE, 'Servicegebühr 4'),
])
def test_unresolved_evidence_blocks_entire_save_even_with_confirmation(tmp_path, rows, charges_known):
    holdings = tmp_path/'holdings.csv'
    original = ('id,name,isin,shares,acquisition_price,acquisition_currency\n'
                'invented,Invented,XX0000000777,4,31.25,EUR\n')
    holdings.write_text(original)
    snapshot = read_snapshot(holdings)
    journal, revision = load_journal(tmp_path)
    valid, invalid = [parse_consors_pdf(text_pdf(r)) for r in (FREE, rows)]
    assert valid.issues == (REVIEW_CHARGES,) and valid.zero_statement_charges
    with pytest.raises(DataError, match='could not be read|Unclassified monetary row'):
        save_pdf_drafts(tmp_path, snapshot.holdings.iloc[0], journal, revision, snapshot,
                        [valid, invalid], charges_known=charges_known)
    assert load_journal(tmp_path) == (journal, revision)
    assert holdings.read_text() == original
