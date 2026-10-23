"""Read-only Consorsbank buy/sale drafts; no portfolio writes or completeness claims.

Narrow text-layout support. All drafts require review before a future history
import. Original files and extracted values must stay local; diagnostics contain
fixed messages only. Absent charges remain unconfirmed unless the complete
execution meets the strict, exact-balance automatic-zero rule.
"""
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import re
import unicodedata

MAX_BYTES = 20 * 1024 * 1024
MAX_FILES = 100
MAX_BATCH_BYTES = 100 * 1024 * 1024


def pdf_engine():
    import pymupdf
    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)
    return pymupdf

# ISO 4217 current currency/fund codes; excludes no-currency/test markers.
# Source: SIX (ISO maintenance agency), list-one.xml:
# https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-one.xml
# Offline allowlist: custody identifiers such as CBL/CBF and bank/page labels
# must not become monetary amounts merely because they contain three capitals.
_CURRENCY_CODES = frozenset("""AED AFN ALL AMD AOA ARS AUD AWG AZN BAM BBD BDT BHD BIF BMD BND BOB BOV BRL BSD BTN BWP
BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY COP COU CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN
ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK
JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD
MMK MNT MOP MRU MUR MVR MWK MXN MXV MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP
PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP
SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD USN UYI UYU UYW UZS VED VES VND VUV
WST XAD XAF XAG XAU XBA XBB XBC XBD XCD XCG XDR XOF XPD XPF XPT XSU XUA YER ZAR ZMW ZWG""".split())

_NUMBER = r'(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?'
_MONEY = re.compile(rf'(?<![\w.,+−-])(?:([A-Z]{{3}})\s+({_NUMBER})|({_NUMBER})\s+([A-Z]{{3}}))\s*$', re.I)
# Only documented unit-price annotations are stripped; arbitrary suffixes and
# currency-free quotations remain unresolved.
_PRICE_SUFFIX = re.compile(r'\s+(?:P\.\s*ST\.\s+)?(?:NETTO|FRANCO\s+COURTAGE)\s*$', re.I)
_FEE = re.compile(r'^(?:abzgl\.\s*)?(?:Provision|Grundgebühr|Grundgebuehr|Börsenplatzgebühr|Boersenplatzgebuehr|Handelsentgelt|Handelsplatzkosten|Transaktionsentgelt|Courtage|Eig\.Spesen|Consorsbank Ausgabegebühr)\b', re.I)
_TAX = re.compile(r'^(?:abzgl\.\s*)?(?:Kapitalertragsteuer|Kapitalertragssteuer|Solidaritätszuschlag|Kirchensteuer|KapSt|SolZ|KiSt)(?=\s|:|$)', re.I)
_NET = re.compile(r'^(?:Netto\s+)?(?:zulasten|zugunsten|zu lasten|zu gunsten)\b|^Wert\s+\d{2}\.\d{2}\.\d{4}', re.I)
# These amounts describe order constraints or tax calculations, not cash charges.
# Match the whole remainder too: a known label must not conceal an extra amount.
_INFORMATION = re.compile(
    r'^(?P<limit>Limitkurs)|^(?:'
    r'KapSt-pflichtig(?:er Kapitalertrag|e Kapitalerträge)|'
    r'Kapitalertragsteuer-Bemessungsgrundlage|'
    r'Veräusserungsgewinn nach Differenzmethode|'
    r'Ergebnis nach Teilfreistellung\s*/\s*Vorabpauschale|'
    r'Steuerpflichtiger Gesamtbetrag|'
    r'mit Verrechnungstopf [„"]Allgemein[“"] verrechnet|'
    r'mit Sparerpauschbetrag verrechnet|'
    r'Bemessungsgrundlage für Kapitalertragsteuer|'
    r'Sparerpauschbetrag nach dem Geschäft|'
    r'Verrechnungstopf (?:Aktien|Allg\.|QUST) nach dem Geschäft|'
    rf'Berücksichtigung Teilfreistellung\s+{_NUMBER}\s*%)', re.I)
# Detection is deliberately broader than supported positive Money parsing:
# unsupported signs, currencies and suffixes must not disappear from validation.
_MONEY_LIKE = re.compile(rf'(?:\b(?!von\b)[A-Z]{{3}}\s+[+−-]?\s*{_NUMBER}|{_NUMBER}\s*[+−-]?\s*(?:(?!von\b)[A-Z]{{3}}\b|[€$£¥])|[€$£¥]\s*[+−-]?\s*{_NUMBER})', re.I)
_METADATA = re.compile(r'^(?:(?:CBL|CBF|BLZ)\s+\d+|BIC\s+[A-Z0-9]+|Seite\s*:?\s*\d+\s+von\s+\d+)$', re.I)
_UNLABELLED_AMOUNT = re.compile(r'(?<![\w.,])\d[\d.]*,\d+(?!\w)')
# Match labelled, structured telephone numbers only. Removing their spans still
# leaves any separate amount on the same visual row subject to money detection.
_CONTACT_NUMBER = re.compile(
    r'\b(?:Telefon|Telefax|Tel|Fax|Fon)\.?\s*:?\s*'
    r'(?:\+\d{1,3}\s*(?:\(0\)\s*)?)?'
    r'\(?\d{2,6}\)?\s*[/ -]\s*\d{3,}(?:[ -]\d{1,6})?(?![\d.,])', re.I)
# A complete labelled German VAT identifier is metadata. In particular, the
# preceding register number plus "USt" must not be mistaken for a currency amount.
_VAT_IDENTIFIER = re.compile(r'\bUSt-IdNr\.\s*:?\s*DE\d{9}(?![\w.,])', re.I)
# Margin document references use a full hexadecimal UUID-shaped token. Internal
# groups such as "1ABC" must not look like money; partial codes remain unresolved.
_DOCUMENT_REFERENCE = re.compile(
    r'(?<!\S)[0-9A-F]{8}(?:-[0-9A-F]{4}){3}-[0-9A-F]{12}(?!\S)', re.I)
_TAX_DETAIL_HEADER = re.compile(
    r'^(?:Details zur Investmentbesteuerung|Steuerberechnung|'
    r'Informationen bei (?:Gewinn|Käufen bzw\. Verlust) '
    r'(?:Aktien\s*/\s*)?Wertpapiere ungleich Aktien)$', re.I)
# Unknown cash adjustments must not be demoted merely because they appear in
# a detail section. This is a review boundary, not a dictionary of charge values.
_ADJUSTMENT_HINT = re.compile(
    r'abzgl\.|zzgl\.|erstattung|refund|fee|tax|charge|deduction|addition|'
    r'belastung|gutschrift|kosten|gebühr|gebuehr|spesen|provision|courtage|'
    r'kapitalertragsteuer|kapitalertragssteuer|kirchensteuer|solidaritätszuschlag', re.I)
_SUPPLEMENTARY_MONEY = re.compile(
    rf'(?P<label>.+?)\s+(?:[+-]?\s*{_NUMBER}\s+(?P<after>[A-Z]{{3}})|'
    rf'(?P<before>[A-Z]{{3}})\s+[+-]?\s*{_NUMBER})$', re.I)


@dataclass(frozen=True)
class StatementRow:
    text: str
    page: int
    row: int


@dataclass(frozen=True)
class FieldReference:
    field: str
    page: int
    row: int


def normalized_row(text):
    text = unicodedata.normalize('NFKC', text).translate(str.maketrans({'−': '-', '–': '-', '—': '-'}))
    return ' '.join(text.split())


def normalized_rows(rows):
    return [StatementRow(normalized_row(r.text), r.page, r.row) if isinstance(r, StatementRow)
            else StatementRow(normalized_row(r), 1, i) for i, r in enumerate(rows, 1)]


def informational_row(row):
    """Return a fixed label and validity, never values from a non-cash row."""
    match = _INFORMATION.match(row)
    if not match or (match.end() < len(row) and row[match.end()] not in ' :'):
        return None, False
    field = 'Order limit' if match['limit'] else 'Tax calculation'
    remainder = row[match.end():].strip().removeprefix(':').strip()
    sign = '' if match['limit'] else r'[+-]?\s*'
    amount = re.fullmatch(
        rf'(?:{sign}{_NUMBER}\s+(?P<after>[A-Z]{{3}})|'
        rf'(?P<before>[A-Z]{{3}})\s+{sign}{_NUMBER})', remainder, re.I)
    valid = bool(amount and (amount['after'] or amount['before']).upper() in _CURRENCY_CODES)
    return field, valid


def unclassified_money(row):
    information, valid = informational_row(row)
    if information:
        return not valid
    # Quantity rows have their own full-row validator (including WKN metadata).
    # A comma followed by whitespace is footer punctuation, not a decimal mark.
    row = re.sub(r',(?=\s|$)', ' ', row)
    row = _VAT_IDENTIFIER.sub(' ', row)
    row = _DOCUMENT_REFERENCE.sub(' ', row)

    def contact(match):
        suffix = row[match.end():].lstrip()
        code = re.match(r'([A-Z]{3})\b|([€$£¥])', suffix, re.I)
        if code and (code[2] or code[1].upper() not in {'TEL', 'FAX', 'FON', 'UND', 'VON'}):
            return match[0]  # An explicit currency must never be hidden as a phone.
        return ' '

    row = _CONTACT_NUMBER.sub(contact, row)
    return bool(not re.match(r'^ST\b', row, re.I) and not _METADATA.fullmatch(row)
                and (_MONEY_LIKE.search(row) or _UNLABELLED_AMOUNT.search(row)))


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class StatementDraft:
    fingerprint: str
    kind: str | None = None
    trade_date: str | None = None
    isin: str | None = None
    wkn: str | None = None
    quantity: Decimal | None = None
    unit_price: Money | None = None
    gross: Money | None = None
    settlement: Money | None = None
    fees: tuple[Money, ...] = ()
    taxes: tuple[Money, ...] = ()
    # Missing components are not filled with zero; review is always required.
    issues: tuple[str, ...] = ()
    duplicate_of: int | None = None
    trade_time: str | None = None
    settlement_date: str | None = None
    zero_statement_charges: bool = False
    provenance: tuple[FieldReference, ...] = ()
    review_notes: tuple[str, ...] = ()


def _number(value):
    return Decimal(value.replace('.', '').replace(',', '.'))


def _money(line):
    match = _MONEY.search(line)
    if not match:
        return None
    # Do not drop a separated sign and turn a refund/loss into a charge.
    if line[:match.start()].rstrip().endswith(('-', '−', '+')):
        return None
    currency_before, amount_after, amount_before, currency_after = match.groups()
    currency = (currency_before or currency_after).upper()
    if currency not in _CURRENCY_CODES:
        return None
    return Money(_number(amount_after or amount_before), currency)



def monetary_field(row):
    """Classify statement rows using the same rules for parsing and diagnostics."""
    if re.match(r'^(?:Preis pro Anteil|Preis pro Stück|Kurs)\b', row, re.I):
        return 'Unit price', _PRICE_SUFFIX.sub('', row)
    if re.match(r'^Kurswert\b', row, re.I):
        return 'Gross amount', row
    if _NET.search(row) and not re.fullmatch(r'Wert\s+\d{2}\.\d{2}\.\d{4}', row, re.I):
        return 'Settlement amount', row
    if _FEE.search(row):
        return 'Fee', row
    if _TAX.search(row):
        return 'Tax', row
    return None, row


def field_money(field, row):
    """Read one field without discarding extra amounts or unknown charge labels."""
    money = _money(row)
    if money is None:
        return None
    amounts = list(_MONEY_LIKE.finditer(row))
    tax_base = (field == 'Tax' and len(amounts) == 2 and '%' in row
                and re.search(r'\bvon\b', row, re.I)
                and _money(amounts[0][0]) is not None
                and _money(amounts[0][0]).currency == money.currency)
    if len(amounts) > 1 and not tax_base:
        return None
    if field in ('Fee', 'Tax'):
        label = (_FEE if field == 'Fee' else _TAX).match(row)
        prefix = row[label.end():_MONEY.search(row).start()].strip().lstrip(':').strip()
        if field == 'Fee' and prefix:
            return None
        # A tax prefix may contain documented percentage shares/rates and an
        # explicit base, never arbitrary text such as a refund or a heading.
        rate = rf'(?:(?:anteilig\s+)?{_NUMBER}\s*%\s*)*'
        base = rf'(?:von\s+(?:[A-Z]{{3}}\s+{_NUMBER}|{_NUMBER}\s+[A-Z]{{3}})\s*)?'
        if field == 'Tax' and not re.fullmatch(rate+base, prefix, re.I):
            return None
    return money


def supplementary_rows(rows):
    """Locate reviewable context without using it to supply transaction values.

    Explicit financial fields and malformed known information remain strict.
    Unknown single amounts are reviewable only in bounded tax-detail sections
    after settlement. Separately labelled ST quantities after settlement are
    contextual, never substitutes for the execution's leading ST row.
    """
    result = {}
    settled, tax_details, page = False, False, None
    for source in normalized_rows(rows):
        row = source.text
        if source.page != page:
            page, tax_details = source.page, False
        if re.match(r'^(?:Wertpapierabrechnung\s+)?(?:Kauf|Verkauf)\b', row, re.I):
            settled, tax_details = False, False
        if _TAX_DETAIL_HEADER.fullmatch(row):
            tax_details = True
        elif re.match(r'^(?:Hinweis für|Standort\b|Sitz\b)', row, re.I):
            tax_details = False
        field, monetary = monetary_field(row)
        if field == 'Settlement amount' and field_money(field, monetary):
            settled = True
        if not settled or field or informational_row(row)[0] or _ADJUSTMENT_HINT.search(row):
            continue
        quantity = re.fullmatch(rf'(?P<label>.+?)\s+ST\s+{_NUMBER}', row, re.I)
        if quantity and not re.search(r'\d', quantity['label']):
            result[source.page, source.row] = 'Supplementary quantity'
        elif tax_details and (amount := _SUPPLEMENTARY_MONEY.fullmatch(row)):
            label = re.sub(rf'{_NUMBER}\s*%', '', amount['label'])
            if (not re.search(r'\d', label)
                    and (amount['after'] or amount['before']).upper() in _CURRENCY_CODES):
                result[source.page, source.row] = 'Supplementary tax detail'
    return result


def settlement_difference(kind, gross, settlement, fees, taxes):
    """Signed actual-minus-expected settlement, or None without comparable inputs."""
    if kind not in ('buy', 'sell') or gross is None or settlement is None:
        return None
    if any(value.currency != gross.currency for value in (settlement, *fees, *taxes)):
        return None
    charges = sum((value.amount for value in (*fees, *taxes)), Decimal(0))
    expected = gross.amount+charges if kind == 'buy' else gross.amount-charges
    return settlement.amount-expected

def _rows(source):
    pdf = pdf_engine()
    with pdf.open(stream=source, filetype='pdf') as doc:
        if doc.needs_pass or not 1 <= len(doc) <= 30:
            raise ValueError('unsupported document')
        result = []
        for page_number, page in enumerate(doc, 1):
            words = page.get_text('words')
            if not words:
                raise ValueError('no selectable text')
            # Rejoin PDF text cells on the same visual row. This is independent
            # of object insertion order and permits separated currency cells.
            groups = []
            for word in sorted(words, key=lambda w: (w[1], w[0])):
                if not groups or abs(word[1]-groups[-1][0][1]) > 3:
                    groups.append([word])
                else:
                    groups[-1].append(word)
            result.extend(StatementRow(' '.join(w[4] for w in sorted(group, key=lambda w: w[0])),
                                       page_number, index) for index, group in enumerate(groups, 1))
        return result


def parse_consors_pdf(source: bytes) -> StatementDraft:
    """Extract a single trade draft without logging source data or changing files."""
    fingerprint = sha256(source).hexdigest()
    if len(source) > MAX_BYTES:
        return StatementDraft(fingerprint, issues=('PDF exceeds the size limit.',))
    try:
        rows = _rows(source)
        return _parse_rows(rows, fingerprint)
    except Exception:
        # Native parser exceptions may contain personal information.
        return StatementDraft(fingerprint, issues=('PDF could not be read. Use a text-based, unencrypted statement.',))


def _parse_rows(rows, fingerprint):
    rows = normalized_rows(rows)
    supplementary = supplementary_rows(rows)
    issues = []
    unreadable = set()
    provenance = []

    def unique(values, field, *, repeated=False):
        if not repeated:
            values = list(dict.fromkeys(values))
        if len(values) == 1:
            return values[0]
        if values or field not in unreadable:
            issues.append(f'{field} is missing.' if not values else f'{field} is ambiguous; review the statement.')
        return None

    kinds, dates, ids, quantities = [], [], [], []
    trade_rows = 0
    times, settlement_dates = [], []
    prices, gross, net, fees, taxes = [], [], [], [], []
    settlement_directions = []
    for source_row in rows:
        row = source_row.text

        def reference(field):
            provenance.append(FieldReference(field, source_row.page, source_row.row))

        trade = re.match(r'^(?:Wertpapierabrechnung\s+)?(Kauf|Verkauf)\b', row, re.I)
        if trade:
            reference('Buy/sale type')
            trade_rows += 1
            kinds.append('buy' if trade[1].casefold() == 'kauf' else 'sell')
        day = re.search(r'\bAM\s+(\d{2}\.\d{2}\.\d{4})\b', row, re.I) if trade else None
        if day:
            reference('Trade date')
            try:
                dates.append(datetime.strptime(day[1], '%d.%m.%Y').date().isoformat())
            except ValueError:
                issues.append('Trade date is invalid.')
        settlement_day = re.search(r'\b(?:Valuta|Wert)\s*:?\s*(\d{2}\.\d{2}\.\d{4})\b', row, re.I)
        if settlement_day:
            reference('Settlement date')
            try:
                settlement_dates.append(datetime.strptime(settlement_day[1], '%d.%m.%Y').date().isoformat())
            except ValueError:
                issues.append('Settlement date is invalid.')
        stamp = re.search(r'\bUM\s+(\d{2}:\d{2}:\d{2})\b', row, re.I) if trade else None
        if trade and re.search(r'\bUM\b', row, re.I) and stamp is None:
            issues.append('Trade time could not be read; review the statement.')
        if stamp:
            reference('Trade time')
            try:
                times.append(datetime.strptime(stamp[1], '%H:%M:%S').time().isoformat())
            except ValueError:
                issues.append('Trade time is invalid.')
        # Identity is only a candidate; portfolio matching must confirm it.
        identity = re.search(r'\b([A-Z0-9]{6})\s+([A-Z]{2}[A-Z0-9]{9}[0-9])\b', row.upper())
        if identity:
            reference('Instrument identity')
            ids.append((identity[1], identity[2]))
        elif legacy_id := re.search(r'\bWKN:\s*([A-Z0-9]{6})\b', row.upper()):
            reference('Instrument identity')
            ids.append((legacy_id[1], None))
        if re.match(r'^ST\b', row, re.I):
            reference('Quantity')
            quantity = re.fullmatch(rf'ST\s*:?\s*({_NUMBER})(?:\s+WKN:\s*[A-Z0-9]{{6}})?', row, re.I)
            if quantity:
                quantities.append(_number(quantity[1]))
            else:
                unreadable.add('Quantity')
                issues.append('Quantity could not be read; review the statement.')
        field, monetary_row = monetary_field(row)
        information, _ = informational_row(row)
        if information:
            reference(information)
        context_field = supplementary.get((source_row.page, source_row.row))
        if context_field:
            reference(context_field)
        if field:
            reference(field)
        if field == 'Settlement amount':
            direction = re.match(r'^(?:Netto\s+)?(zulasten|zu lasten|zugunsten|zu gunsten)\b', row, re.I)
            if direction:
                settlement_directions.append('buy' if 'lasten' in direction[1].casefold() else 'sell')
        destination = {'Unit price': prices, 'Gross amount': gross,
                       'Settlement amount': net, 'Fee': fees, 'Tax': taxes}.get(field)
        if destination is None and re.match(r'^(?:abzgl\.|zzgl\.)\s', row, re.I):
            unreadable.add('Adjustment')
            issues.append('Unrecognized deduction or addition; review the statement.')
        elif destination is None and not context_field and unclassified_money(row):
            reference('Unclassified monetary row')
            unreadable.add('Adjustment')
            issues.append('Unclassified monetary row; review the statement.')
        if destination is not None:
            money = field_money(field, monetary_row)
            if money is None:
                unreadable.add(field)
                issues.append(f'{field} could not be read; review the statement.')
            else:
                destination.append(money)
        if re.search(r'\b(?:Storno|Stornierung|Neuabrechnung|Berichtigung|Korrektur|Devisenkurs)\b', row, re.I):
            issues.append('Correction or currency conversion requires manual review.')

    if trade_rows > 1:
        issues.append('Multiple trade headings found; split or review the document.')
    kind = unique(kinds, 'Buy/sale type')
    trade_date = unique(dates, 'Trade date')
    trade_time = unique(times, 'Trade time') if times else None
    settlement_date = unique(settlement_dates, 'Settlement date') if settlement_dates else None
    if trade_date and settlement_date and settlement_date < trade_date:
        issues.append('Settlement date precedes the trade date.')
    identity = unique(ids, 'Instrument identity')
    quantity = unique(quantities, 'Quantity', repeated=True)
    unit_price = unique(prices, 'Unit price', repeated=True)
    gross_amount = unique(gross, 'Gross amount', repeated=True)
    settlement = unique(net, 'Settlement amount', repeated=True)
    if any(direction != kind for direction in settlement_directions):
        issues.append('Settlement direction does not match the buy/sale type.')
    for value, field in ((unit_price, 'Unit price'), (gross_amount, 'Gross amount')):
        if value is not None and value.amount <= 0:
            issues.append(f'{field} must be positive.')
    if quantity is not None and quantity <= 0:
        issues.append('Quantity must be positive.')
    if quantity is not None and unit_price and gross_amount:
        if unit_price.currency != gross_amount.currency:
            issues.append('Price and gross currencies differ; review conversion.')
        elif abs(quantity*unit_price.amount-gross_amount.amount) > Decimal('0.01'):
            issues.append('Quantity times price does not match the gross amount.')
    if gross_amount and settlement and kind and not unreadable.intersection({'Fee', 'Tax', 'Adjustment'}):
        components = [settlement, *fees, *taxes]
        if any(value.currency != gross_amount.currency for value in components):
            issues.append('Settlement components use different currencies; review conversion.')
        else:
            difference = settlement_difference(kind, gross_amount, settlement, fees, taxes)
            if abs(difference) > Decimal('0.01'):
                issues.append('Settlement does not match the gross amount and listed fees/taxes.')
    issues.append('Confirm fees and taxes are complete before importing.')
    draft = StatementDraft(fingerprint, kind, trade_date, identity[1] if identity else None,
                          identity[0] if identity else None, quantity, unit_price, gross_amount,
                          settlement, tuple(fees), tuple(taxes), tuple(dict.fromkeys(issues)), trade_time=trade_time,
                          settlement_date=settlement_date, provenance=tuple(provenance),
                          review_notes=('Supplementary rows were excluded from transaction values; review their source locations.',
                                        ) if supplementary else ())
    return replace(draft, zero_statement_charges=_zero_statement_charges(draft, rows))



def _zero_statement_charges(draft, rows):
    return not zero_charge_review_reasons(draft, rows)


def zero_charge_review_reasons(draft, rows):
    """Fixed, value-free reasons a purchase's charges still require review.

    Recognition covers this execution statement, not separately billed costs.
    Neither a savings-plan label nor an absent fee row alone establishes zero.
    """
    if draft.kind != 'buy':
        return ('Not a purchase.',)
    reasons = []
    if draft.review_notes:
        reasons.append('Supplementary rows require review.')
    if (not draft.gross or not draft.settlement or not draft.unit_price
            or draft.unit_price.amount <= 0 or draft.gross.amount <= 0
            or draft.issues != ('Confirm fees and taxes are complete before importing.',)):
        reasons.append('Statement has missing, invalid or unresolved fields.')
    if draft.gross != draft.settlement:
        reasons.append('Trade value and debit are not exactly equal in the same currency.')
    if any(value.amount != 0 for value in (*draft.fees, *draft.taxes)):
        reasons.append('Nonzero fees or taxes are listed.')
    counts = {}
    supplementary = supplementary_rows(rows)
    for source in normalized_rows(rows):
        row = source.text
        field, normalized = monetary_field(row)
        if field:
            counts[field] = counts.get(field, 0)+1
            if field == 'Settlement amount' and not re.match(
                    r'^(?:Netto\s+)?(?:zulasten|zu lasten)\b', row, re.I):
                reasons.append('Settlement is not explicitly labelled as a debit.')
        elif (source.page, source.row) not in supplementary and unclassified_money(normalized):
            reasons.append('Unclassified monetary rows remain.')
    if any(counts.get(field) != 1 for field in ('Unit price', 'Gross amount', 'Settlement amount')):
        reasons.append('Price, trade value or settlement rows are missing or repeated.')
    return tuple(dict.fromkeys(reasons))

def parse_consors_batch(sources: list[bytes]) -> tuple[StatementDraft, ...]:
    """Keep every draft; mark byte-identical files without silently discarding any."""
    if len(sources) > MAX_FILES:
        raise ValueError('Choose up to 100 statements per batch.')
    if sum(map(len, sources)) > MAX_BATCH_BYTES:
        raise ValueError('Choose a batch no larger than 100 MB.')
    seen, drafts = {}, []
    for source in sources:
        draft = parse_consors_pdf(source)
        if draft.fingerprint in seen:
            draft = replace(draft, duplicate_of=seen[draft.fingerprint],
                            issues=(*draft.issues, 'This file duplicates an earlier file in this batch.'))
        else:
            seen[draft.fingerprint] = len(drafts)
        drafts.append(draft)
    return tuple(drafts)
