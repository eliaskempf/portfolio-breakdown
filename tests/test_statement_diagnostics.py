"""Privacy checks use invented PDFs and independent sentinel values only."""
import pytest

from portfolio_app.statement_diagnostics import _token, layout_report
from test_consors_pdf import statement


def test_report_retains_layout_but_no_source_values_or_identifiers():
    source = statement(extra=(
        'PRIVATE_SENTINEL_PERSON PRIVATE_SENTINEL_ACCOUNT',
        'Kurs 8.765,432100 EUR P.ST. NETTO',
        'Provision EUR 98,76-',
        'abzgl. Kapitalertragsteuer 25,00 % von 12,00 EUR 3,00 EUR',
    ))
    report = layout_report([source])
    for secret in ('PRIVATE_SENTINEL', 'INVENTED', 'FUND', 'SYN001', 'XX0000000000',
                   '07.02.2024', '09.02.2024', '10:30:00', '8.765,432100', '98,76',
                   '25,00', '12,00', '3,00', 'EUR'):
        assert secret not in report
    assert 'KURS <number:comma,dot> <currency-or-code> P.ST. NETTO' in report
    assert 'PROVISION <currency-or-code> <number:comma,trailing-sign>' in report
    assert 'AM <date> UM <time>' in report


@pytest.mark.parametrize('secret', [
    'SecretName', 'XX1234567890', 'account-1234', '999999999999999999999999999',
    'secret@example.invalid', '2024-02-07', 'https://example.invalid/private',
    'EUR123.45', 'SecretName:50', '\x00secret',
])
def test_non_allowlisted_text_cannot_survive(secret):
    assert secret not in _token(secret)


def test_bad_document_never_exports_parser_exception(monkeypatch):
    def fail(source):
        raise ValueError('PRIVATE_SENTINEL: raw source text')
    monkeypatch.setattr('portfolio_app.statement_diagnostics._rows', fail)
    report = layout_report([b'PRIVATE_SENTINEL'])
    assert 'PRIVATE_SENTINEL' not in report
    assert 'PDF could not be read.' in report


def test_reconciliation_clues_reveal_matches_without_amounts_or_repair():
    from portfolio_app.consors_pdf import parse_consors_pdf
    source = statement(sale=True, net='81,66', extra=(
        'Invented tax debit 11,34 EUR',
        'Invented unrelated value 44,55 EUR'))
    report = layout_report([source])
    assert 'Parsed fee rows: 2; parsed tax rows: 0.' in report
    assert 'Settlement is below the amount expected' in report
    matches = [row for row in report.splitlines() if 'amount matches discrepancy magnitude' in row]
    assert len(matches) == 1
    assert '<text> <text> <text> <number:comma> <currency-or-code>' in matches[0]
    assert all(secret not in report for secret in ('81,66', '11,34', '44,55', 'Invented', 'EUR'))
    draft = parse_consors_pdf(source)
    assert not draft.taxes
    assert 'Unclassified monetary row; review the statement.' in draft.issues


def test_reconciliation_report_distinguishes_balance_excess_and_missing_inputs():
    assert 'balances within rounding tolerance' in layout_report([statement()])
    assert 'Settlement is above the amount expected' in layout_report([statement(net='118,34')])
    assert 'Settlement comparison unavailable.' in layout_report([statement(missing=('net',))])


def test_comparison_does_not_match_a_different_currency_amount():
    report = layout_report([statement(sale=True, net='81,66', extra=('OTHER 11,34 USD',))])
    assert 'amount matches discrepancy magnitude' not in report


def test_zero_fee_report_explains_exact_balance_and_extra_money_guards():
    report = layout_report([statement(fees=False, extra=('BIC 00000000', 'Seite 1 VON 2'))])
    assert 'Automatic zero fees: eligible.' in report
    assert 'unclassified monetary row' not in report
    report = layout_report([statement(fees=False, net='100,01')])
    assert 'Trade value and debit are not exactly equal in the same currency.' in report
    report = layout_report([statement(fees=False, extra=('Unknown extra 4,00 EUR',))])
    assert 'Unclassified monetary rows remain.' in report
    assert '[unclassified monetary row]' in report
    assert '4,00' not in report



def test_custody_reference_is_not_reported_as_unclassified_money():
    report = layout_report([statement(fees=False, extra=(
        'Bestand zugunsten Wertpapierrechnung Luxemburg', 'CBL 000123'))])
    assert 'Automatic zero fees: eligible.' in report
    assert 'unclassified monetary row' not in report
    assert 'CBL <number>' in report
    assert '000123' not in report
