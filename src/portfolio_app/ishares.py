"""Read the complete English iShares XML Spreadsheet Holdings worksheet.

These exports have an .xls suffix but are not binary Excel files. Their rounded
percentages can lose many small holdings. Use market values from the complete
All worksheet, validating each result against its published rounded weight.
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import re
import xml.etree.ElementTree as ET

import pandas as pd

from portfolio_app.etf import validate_constituents
from portfolio_app.holdings import DataError

NS = 'urn:schemas-microsoft-com:office:spreadsheet'
CASH_CLASSES = {'Cash', 'Money Market', 'Cash Collateral and Margins', 'FX'}

# These fund securities appear as "Equity" in the provider's export. Keep
# their fund wrappers visible rather than count them as individual companies.
# Public issuer metadata: ishares.com product pages for these two ISINs.
NESTED_EQUITY_FUNDS = {
    ('4BRZ', 'ISHARES MSCI BRAZIL UCITS ET USDHA', 'USD'): 'DE000A0Q4R85',
    ('IKSA', 'ISHARES MSCI SAUDI ARABIA CAPPED', 'USD'): 'IE00BYYR0489',
}


def download_url(product_id: str) -> str:
    return ('https://www.blackrock.com/varnish-api/uk-retail01-product-data/product-data/api/v1/get-fund-document'
            '?appSubType=ISHARES&appType=PRODUCT_PAGE&component=fundDownloadV2&locale=en_GB'
            f'&portfolioId={product_id}&targetSite=ishares-uk&userType=individual')


def _number(text: str) -> Decimal:
    value = Decimal(text.replace(',', ''))
    if not value.is_finite():
        raise ValueError('Non-finite holding value')
    return value


def parse_holdings(content: bytes) -> tuple[date, pd.DataFrame, str]:
    try:
        root = ET.fromstring(content)
        sheet = next(s for s in root.findall(f'{{{NS}}}Worksheet') if s.get(f'{{{NS}}}Name') == 'Holdings')
        rows = []
        for node in sheet.findall(f'{{{NS}}}Table/{{{NS}}}Row'):
            row = []
            for cell in node.findall(f'{{{NS}}}Cell'):
                index = int(cell.get(f'{{{NS}}}Index', len(row) + 1)) - 1
                if index < len(row):
                    raise ValueError('Invalid cell index')
                row.extend([''] * (index - len(row)))
                data = cell.find(f'{{{NS}}}Data')
                row.append(''.join(data.itertext()).strip() if data is not None else '')
            rows.append(row)
        header = ['Issuer Ticker', 'Name', 'Sector', 'Asset Class', 'Market Value',
                  'Weight (%)', 'Notional Value', 'Nominal', 'Market Currency']
        if rows[0] != ['All'] or rows[1][0] != 'as of' or rows[2] != header:
            raise ValueError('Expected complete All holdings and known English columns')
        as_of = datetime.strptime(rows[1][1].replace('/Sept/', '/Sep/'), '%d/%b/%Y').date()
        parsed = []
        ended = False
        for row in rows[3:]:
            if not row or not any(row):
                ended = True
                continue
            if ended:
                if len(row) > 1 and any(row[1:]):
                    raise ValueError('Unexpected rows after holdings footer')
                continue
            if len(row) != len(header):
                raise ValueError('Incomplete holdings row')
            currency, amount = row[4].split(' ', 1)
            if not re.fullmatch('[A-Z]{3}', currency):
                raise ValueError('Missing market-value currency')
            parsed.append((row, currency, _number(amount), _number(row[5])))
        if not parsed or len({r[1] for r in parsed}) != 1:
            raise ValueError('Expected holdings valued in one fund currency')
        total = sum(r[2] for r in parsed)
        if total <= 0:
            raise ValueError('Nonpositive net assets')
        records, net_cash = [], Decimal(0)
        for row, _, amount, published_pct in parsed:
            weight = amount / total
            # Allow half a displayed percentage unit plus a tiny allowance for
            # provider double rounding near a display boundary.
            if abs(weight * 100 - published_pct) > Decimal('0.00501'):
                raise ValueError('Market values disagree with published weights; export may be incomplete')
            kind = row[3]
            if kind in CASH_CLASSES:
                net_cash += amount
                continue
            if kind != 'Equity' and not (kind == 'Futures' and amount == 0):
                raise ValueError(f'Unsupported holding type: {kind}')
            if kind == 'Futures':
                continue  # Zero market value; notional exposure is not company allocation.
            if amount < 0:
                raise ValueError('Short equity positions are not supported')
            # Local ticker alone does not identify an exchange listing. Keep it
            # as source metadata, never as a Yahoo ticker / implicit merge key.
            identity = '\0'.join((row[0], row[1], row[8]))
            nested_isin = NESTED_EQUITY_FUNDS.get((row[0], row[1], row[8]), '')
            records.append({'constituent_id': 'ishares:' + sha256(identity.encode()).hexdigest()[:24],
                            'name': row[1], 'ticker': '', 'isin': nested_isin, 'weight': float(weight),
                            'instrument_type': 'etf' if nested_isin else 'equity',
                            'exposure_kind': 'equity', 'source_ticker': row[0],
                            'market_currency': row[8], 'sector': row[2]})
        if net_cash < 0:
            raise ValueError('Net cash borrowing requires a signed exposure model')
        records.append({'constituent_id': 'ishares:net-cash', 'name': 'Net cash and cash equivalents',
                        'ticker': '', 'isin': '', 'weight': float(net_cash / total), 'instrument_type': 'cash'})
        notes = ('Weights calculated from all exported market values, including net cash, to avoid rounded-percentage losses. '
                 'Cash, money-market instruments, collateral and FX are netted. Futures notional exposure is not allocated to companies. '
                 'Reviewed nested equity funds remain unresolved fund exposure. Other source local tickers have no '
                 'ISIN or exchange; no name-based matching to other securities is performed.')
        return as_of, validate_constituents(pd.DataFrame(records)), notes
    except (ET.ParseError, KeyError, ValueError, TypeError, IndexError, StopIteration, InvalidOperation) as exc:
        raise DataError(f'Invalid iShares holdings export: {exc}') from exc
