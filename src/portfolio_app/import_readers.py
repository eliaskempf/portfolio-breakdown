"""Local table readers and provisional column suggestions; no portfolio semantics."""
import csv
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO, StringIO

import pandas as pd

from portfolio_app.holdings import DataError


@dataclass(frozen=True)
class TextFormat:
    encoding: str
    delimiter: str


def detect_text_format(content: bytes) -> TextFormat:
    encoding = 'utf-16' if content.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
    try:
        decoded = content.decode(encoding)
    except UnicodeError:
        encoding = 'cp1252'
        try:
            decoded = content.decode(encoding)
        except UnicodeError as exc:
            raise DataError('Cannot decode this table. Select its text encoding.') from exc
    try:
        delimiter = csv.Sniffer().sniff(decoded[:16000], delimiters=';\t,|').delimiter
    except csv.Error:
        delimiter = max(';\t,|', key=lambda char: decoded[:16000].count(char))
    return TextFormat(encoding, delimiter)


def excel_sheets(content: bytes) -> list[str]:
    try:
        with pd.ExcelFile(BytesIO(content), engine='calamine') as book:
            return book.sheet_names
    except Exception as exc:
        raise DataError('Cannot read this Excel workbook. Export an unencrypted XLS/XLSX or CSV file.') from exc


def read_table(content: bytes, *, excel: bool = False, sheet: str | int = 0,
               header_row: int = 1, encoding: str = 'utf-8-sig', delimiter: str = ';',
               decimal: str = ',') -> pd.DataFrame:
    """Header row is one-based; every returned cell is editable text.

    Native Excel numbers follow the chosen display locale, whereas text cells
    (including identifiers with leading zeroes) retain their exact contents.
    """
    if header_row < 1:
        raise DataError('Header row must be at least 1.')
    try:
        if excel:
            raw = pd.read_excel(BytesIO(content), engine='calamine', sheet_name=sheet,
                                header=None, dtype=object, keep_default_na=False)
            def cell(value):
                if isinstance(value, (date, datetime)):
                    return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
                if isinstance(value, (int, float)):
                    return str(value).replace('.', decimal)
                return str(value).strip()
            rows = [[cell(value) for value in row] for row in raw.itertuples(index=False, name=None)]
        else:
            rows = list(csv.reader(StringIO(content.decode(encoding)), delimiter=delimiter, strict=True))
        if len(rows) < header_row:
            raise DataError('The selected header row is outside the table.')
        headers = [str(value).strip() for value in rows[header_row - 1]]
        if not headers or any(not value for value in headers) or len(set(headers)) != len(headers):
            raise DataError('Choose a header row with nonempty, unique column names.')
        body = rows[header_row:]
        if any(len(row) > len(headers) for row in body):
            raise DataError('Some rows have more fields than the header. Check the delimiter and header row.')
        body = [row + [''] * (len(headers) - len(row)) for row in body]
        return pd.DataFrame(body, columns=headers).astype(str)
    except DataError:
        raise
    except Exception as exc:
        raise DataError('Cannot read the table. Check its format, encoding and header row.') from exc


def normalized_header(value: str) -> str:
    value = value.casefold().replace('ä', 'ae').replace('ö', 'oe').replace('ü', 'ue').replace('ß', 'ss')
    text = unicodedata.normalize('NFKD', value)
    return re.sub(r'[^a-z0-9]', '', text)


# Suggestions only: this is not a verified FinanzManager export schema.
ALIASES = {
    'name': ('name', 'instrument', 'wertpapier', 'bezeichnung', 'wertpapiername'),
    'shares': ('shares', 'quantity', 'quantity held', 'stück', 'stückzahl', 'bestand', 'stück/nennwert',
               'stück/nennwert bank', 'stück/nennwert finanzmanager'),
    'isin': ('isin',), 'wkn': ('wkn',), 'ticker': ('ticker', 'yahoo ticker'),
    'account': ('account', 'depot', 'konto', 'depotname'),
    'price': ('price', 'kurs', 'aktueller kurs', 'unit price'),
    'price_currency': ('price currency', 'kurswährung', 'währung'),
    'price_date': ('price date', 'kursdatum', 'kurs vom'),
    'acquisition_price': ('average buy-in', 'einstandskurs', 'kaufkurs'),
    'total_cost': ('total buy-in', 'einstandswert', 'anschaffungswert'),
    'acquisition_currency': ('buy-in currency', 'einstandswährung'),
}


def suggest_mapping(columns) -> dict[str, str]:
    result = {}
    for field, aliases in ALIASES.items():
        candidates = [col for col in columns if normalized_header(col) in {normalized_header(a) for a in aliases}]
        # Multiple quantity sources must always be chosen explicitly.
        result[field] = candidates[0] if len(candidates) == 1 else ''
    return result
