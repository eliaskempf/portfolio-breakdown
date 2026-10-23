"""Local, allowlisted layout diagnostics: never export source text or values."""
from decimal import Decimal
import re

from portfolio_app.consors_pdf import MAX_BYTES, _rows, _money, _parse_rows, monetary_field, settlement_difference, zero_charge_review_reasons, normalized_row, unclassified_money, field_money, informational_row, _DOCUMENT_REFERENCE, supplementary_rows

# Only fixed financial/layout vocabulary can survive. Unknown words (including
# names, identifiers and descriptions) are replaced, not scrubbed by a denylist.
_WORDS = frozenset('''PREIS PRO ANTEIL STÜCK STUECK KURS KURSWERT NETTO P.ST.
FRANCO COURTAGE PROVISION GRUNDGEBÜHR GRUNDGEBUEHR BÖRSENPLATZGEBÜHR
BOERSENPLATZGEBUEHR HANDELSPLATZKOSTEN HANDELSENTGELT TRANSAKTIONSENTGELT EIG.SPESEN
AUSGABEGEBÜHR AUSGABEGEBUEHR ABZGL. ZZGL. KAPITALERTRAGSTEUER
KAPITALERTRAGSSTEUER SOLIDARITÄTSZUSCHLAG SOLIDARITAETSZUSCHLAG KIRCHENSTEUER
KAPST SOLZ KIST QUELLENSTEUER FINANZTRANSAKTIONSSTEUER ANRECHENBARE
ANTEILIG VON ZULASTEN ZUGUNSTEN ZU LASTEN GUNSTEN WERT VALUTA DEVISENKURS
KAUF VERKAUF WERTPAPIERABRECHNUNG ST ST: EINHEIT UMSATZ AM UM IN IBAN
BEZEICHNUNG WKN ISIN SUMME GESAMT STEUERN GEBÜHREN SPESEN BONIFIKATION
BONIFIKAT RABATT BLZ BIC CBL CBF BESTAND WERTPAPIERRECHNUNG SEITE SEITE: KAPST-PFLICHTIGER KAPITALERTRAG BEMESSUNGSGRUNDLAGE STEUERABZUG STEUERBELASTUNG STEUERGUTSCHRIFT EINBEHALTENE
ABGEFÜHRTE ABGEFUEHRTE VERRECHNETE ERSTATTETE INKL. INKLUSIVE
TELEFON TELEFON: TELEFAX TELEFAX: TEL TEL. TEL: TEL.: FAX FAX: FAX. FON
ANSCHAFFUNGSKOSTEN ANSCHAFFUNGSWERT VERÄUSSERUNGSERLÖS VERAEUSSERUNGSERLOES
VERÄUSSERUNGSGEWINN VERAEUSSERUNGSGEWINN VERLUSTVERRECHNUNG VERLUSTTOPF
AKTIENVERLUSTTOPF FREISTELLUNGSAUFTRAG SPARER-PAUSCHBETRAG TEILFREISTELLUNG
STEUERLICHER STEUERLICHE STEUERLICHEN BEMESSUNGSGRUNDLAGE
BRUTTOERTRAG BRUTTOERLÖS BRUTTOERLOES ZWISCHENGEWINN VORABPAUSCHALE
LIMITKURS VERÄUSSERUNGSGEWINN DIFFERENZMETHODE KAPST-PFLICHTIGE KAPITALERTRÄGE
BERÜCKSICHTIGUNG ERGEBNIS STEUERPFLICHTIGER GESAMTBETRAG VERRECHNUNGSTOPF
SPARERPAUSCHBETRAG VERRECHNET AKTIEN ALLG. QUST NACH DEM GESCHÄFT MIT FÜR
STANDORT SITZ HRB REGISTERGERICHT UST-IDNR. UST-IDNR.:'''.split())


def _token(token):
    upper = token.upper()
    if _DOCUMENT_REFERENCE.fullmatch(token):
        return '<document-reference>'
    if upper in _WORDS:
        return upper
    if re.fullmatch(r'\d{2}\.\d{2}\.\d{4}', token):
        return '<date>'
    if re.fullmatch(r'\d{2}:\d{2}(?::\d{2})?', token):
        return '<time>'
    if token in ('€', '$', '£', '¥'):
        return '<currency-symbol>'
    if re.fullmatch(r'[A-Z]{3}', token):
        return '<currency-or-code>'
    if token in ('%', '+', '-', '−', ':', '/', '(', ')'):
        return token
    if re.fullmatch(r'[+−-]?\d[\d.,]*[%+−-]?', token):
        # Record separators and sign placement, never digits or their count.
        markers = []
        if ',' in token:
            markers.append('comma')
        if '.' in token:
            markers.append('dot')
        if token[0] in '+−-':
            markers.append('leading-sign')
        if token[-1] in '+−-':
            markers.append('trailing-sign')
        if token.endswith('%'):
            markers.append('percent')
        return '<number'+(':'+','.join(markers) if markers else '')+'>'
    return '<text>'


def layout_report(sources: list[bytes]) -> str:
    """Export token shapes only; no filenames, hashes, metadata or raw errors.

    All emitted source-dependent tokens come from _token's finite vocabulary.
    This is intentionally lossy: it cannot establish financial correctness.
    """
    report = ['Statement layout report v2',
              'Names, identifiers, dates, times and amounts are replaced.',
              'Only fixed layout words, token types and amount-comparison results remain.']
    for index, source in enumerate(sources, 1):
        report.append(f'\nDocument {index}')
        if len(source) > MAX_BYTES:
            report.append('Size limit exceeded.')
            continue
        try:
            rows = _rows(source)
            diagnostic = _reconciliation_report(rows)
        except Exception:
            report.append('PDF could not be read.')
            continue
        report.extend(diagnostic)
        if len(rows) > 500:
            report.append('Remaining rows omitted.')
    return '\n'.join(report)+'\n'


def _reconciliation_report(rows):
    draft = _parse_rows(rows, '')
    supplementary = supplementary_rows(rows)
    difference = settlement_difference(draft.kind, draft.gross, draft.settlement, draft.fees, draft.taxes)
    result = [f'Parsed fee rows: {len(draft.fees)}; parsed tax rows: {len(draft.taxes)}.']
    result.extend('Issue: '+issue for issue in draft.issues
                  if issue != 'Confirm fees and taxes are complete before importing.')
    result.extend('Review: '+note for note in draft.review_notes)
    if draft.kind == 'buy':
        reasons = zero_charge_review_reasons(draft, rows)
        result.append('Automatic zero fees: '+('review required.' if reasons else 'eligible.'))
        result.extend(reasons)
    if difference is None:
        result.append('Settlement comparison unavailable.')
    elif abs(difference) <= Decimal('0.01'):
        result.append('Parsed settlement balances within rounding tolerance.')
        result.append('Settlement matches exactly.' if difference == 0 else
                      'Settlement has a nonzero difference within rounding tolerance.')
    else:
        direction = 'below' if difference < 0 else 'above'
        result.append(f'Settlement is {direction} the amount expected from parsed gross and charges.')
    if draft.quantity is not None and draft.unit_price and draft.gross and draft.unit_price.currency == draft.gross.currency:
        price_difference = draft.quantity*draft.unit_price.amount-draft.gross.amount
        result.append('Quantity times quoted price: '+('exact gross match.' if price_difference == 0 else
            'nonzero difference within rounding tolerance.' if abs(price_difference) <= Decimal('0.01') else
            'difference exceeds rounding tolerance.'))
    result.append('A row matching the discrepancy is only a clue, not a verified adjustment.')
    for index, row in enumerate(rows[:500], 1):
        location = f'Page {row.page}, row {row.row}'
        context_field = supplementary.get((row.page, row.row))
        row = normalized_row(row.text)
        field, normalized = monetary_field(row)
        information, valid_information = informational_row(normalized)
        value = field_money(field, normalized) if field else _money(normalized)
        markers = []
        if field:
            markers.append(field+(' read' if value else ' unreadable'))
        elif information:
            markers.append(information+(' (informational)' if valid_information else ' unreadable'))
        elif context_field:
            markers.append(context_field+' (review required)')
        if not field and not context_field and unclassified_money(normalized):
            markers.append('unclassified monetary row')
        if (difference is not None and abs(difference) > Decimal('0.01') and value
                and value.currency == draft.gross.currency
                and abs(value.amount-abs(difference)) <= Decimal('0.01')):
            markers.append('amount matches discrepancy magnitude')
        result.append(f'Row {index} ({location}): '+ ' '.join(_token(word) for word in row.split())+
                      (' ['+'; '.join(markers)+']' if markers else ''))
    return result
