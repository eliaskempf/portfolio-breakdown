"""Read the complete English iShares XML Spreadsheet Holdings worksheet.

These exports have an .xls suffix but are not binary Excel files. Their rounded
percentages can lose many small holdings. Use market values from the complete
All worksheet, validating each result against its published rounded weight.
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import re
import json
import xml.etree.ElementTree as ET

import pandas as pd

from portfolio_app.etf import validate_constituents
from portfolio_app.holdings import DataError

NS = 'urn:schemas-microsoft-com:office:spreadsheet'
CASH_CLASSES = {'Money Market', 'Cash', 'Cash Collateral and Margins', 'FX'}

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


def provider_date(value: str) -> date:
    value = value.replace('Sept', 'Sep')
    for fmt in ('%d/%b/%Y', '%d-%b-%Y', '%Y-%m-%d', '%b %d, %Y'):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError(f'Invalid provider date: {value}')


def parse_holdings(content: bytes) -> tuple[date, pd.DataFrame, str]:
    try:
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            # Some later sheets contain malformed disclaimer text. Retain the
            # original namespace declarations while isolating the complete
            # Holdings sheet; never repair its financial rows.
            workbook = re.search(rb'<(?P<prefix>(?:[A-Za-z_][\w.-]*:)?)Workbook\b[^>]*>', content)
            sheet = re.search(rb'<(?P<prefix>(?:[A-Za-z_][\w.-]*:)?)Worksheet\b[^>]*\b(?:\w+:)?Name=["\']Holdings["\'][^>]*>.*?</(?P=prefix)Worksheet>', content, re.S)
            if not workbook or not sheet:
                raise
            root = ET.fromstring(workbook[0] + sheet[0] + b'</' + workbook['prefix'] + b'Workbook>')
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
        starts = [i for i, row in enumerate(rows) if row == ['All']]
        if len(starts) != 1:
            raise ValueError('Expected complete All holdings and known English columns')
        rows = rows[starts[0]:]
        header = rows[2]
        required = {'Issuer Ticker', 'Name', 'Sector', 'Asset Class', 'Market Value', 'Weight (%)', 'Market Currency'}
        if rows[1][0] != 'as of' or not required.issubset(header) or len(set(header)) != len(header):
            raise ValueError('Expected complete All holdings and known English columns')
        as_of = provider_date(rows[1][1])
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
            row = dict(zip(header, row, strict=True))
            currency, amount = row['Market Value'].split(' ', 1)
            if not re.fullmatch('[A-Z]{3}', currency):
                raise ValueError('Missing market-value currency')
            parsed.append((row, currency, _number(amount), _number(row['Weight (%)'])))
        return _normalize_rows(parsed, as_of, Decimal('0.00501'))
    except (ET.ParseError, KeyError, ValueError, TypeError, IndexError, StopIteration, InvalidOperation) as exc:
        raise DataError(f'Invalid iShares holdings export: {exc}') from exc


def _normalize_rows(parsed, as_of, tolerance):
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
        if abs(weight * 100 - published_pct) > tolerance:
            raise ValueError('Market values disagree with published weights; export may be incomplete')
        kind = row['Asset Class']
        if kind in CASH_CLASSES:
            net_cash += amount
            continue
        if kind not in {'Equity', 'Fixed Income', 'Money Market'} and not (kind == 'Futures' and amount == 0):
            raise ValueError(f'Unsupported holding type: {kind}')
        if kind == 'Futures':
            continue  # Zero market value; notional exposure is not company allocation.
        if amount < 0:
            raise ValueError('Short security positions are not supported')
        # Local ticker alone does not identify an exchange listing. Keep it
        # as source metadata, never as a Yahoo ticker / implicit merge key.
        identity = '\0'.join((row['Issuer Ticker'], row['Name'], row['Market Currency']))
        if kind == 'Fixed Income':
            identity += '\0' + row.get('Maturity', '') + '\0' + row.get('Coupon (%)', '') + '\0' + row.get('Effective Date', '')
        nested_isin = NESTED_EQUITY_FUNDS.get((row['Issuer Ticker'], row['Name'], row['Market Currency']), '')
        isin = row.get('ISIN', '').strip()
        isin = isin if re.fullmatch('[A-Z]{2}[A-Z0-9]{9}[0-9]', isin) else nested_isin
        instrument_type = {'Equity': 'equity', 'Fixed Income': 'bond', 'Money Market': 'money_market'}[kind]
        record = {'constituent_id': f'isin:{isin}' if isin else 'ishares:' + sha256(identity.encode()).hexdigest()[:24],
                  'name': row['Name'], 'ticker': '', 'isin': isin, 'weight': float(weight),
                  'instrument_type': 'etf' if nested_isin else instrument_type,
                  'exposure_kind': 'equity' if kind == 'Equity' else 'non_equity',
                  'source_ticker': row['Issuer Ticker'], 'market_currency': row['Market Currency'],
                  'sector': row['Sector'], 'country': row.get('Location', ''),
                  'issuer': row.get('Issuer', ''), 'credit_rating': row.get('Credit Rating', ''),
                  'maturity': ''}
        if row.get('Maturity', '') not in {'', '-'}:
            record['maturity'] = provider_date(row['Maturity']).isoformat()
        # Issuer ticker is an official grouping identifier, not a price ticker.
        if instrument_type == 'bond' and not record['issuer']:
            record['issuer'] = row['Issuer Ticker']
        records.append(record)
    if net_cash < 0:
        raise ValueError('Net cash borrowing requires a signed exposure model')
    records.append({'constituent_id': 'ishares:net-cash', 'name': 'Net cash and cash equivalents',
                    'ticker': '', 'isin': '', 'weight': float(net_cash / total), 'instrument_type': 'cash'})
    notes = ('Weights calculated from all exported market values, including net cash, to avoid rounded-percentage losses. '
             'Cash, money-market holdings, collateral and FX form a net liquidity pool. Futures notional exposure is not allocated to companies. '
             'Reviewed nested equity funds remain unresolved fund exposure. Other source local tickers have no '
             'verified exchange; published ISINs are preserved. Issuer tickers are provider identifiers, not price listings.')
    return as_of, validate_constituents(pd.DataFrame(records)), notes


def holdings_url(product_id: str) -> str:
    return ('https://www.blackrock.com/varnish-api/uk-retail01-product-data/product-data/api/v2/get-product-data'
            '?appSubType=ISHARES&appType=PRODUCT_PAGE&component=holdings&locale=en_GB'
            f'&portfolioId={product_id}&targetSite=ishares-uk&userType=individual')


def parse_holdings_json(content: bytes) -> tuple[date, pd.DataFrame, str]:
    """Complete named arrays include security ISINs and unrounded market values."""
    try:
        doc = json.loads(content)
        node = doc['componentsByNameMap']['holdings']['containersByNameMap']['all']
        points = {k: v.get('value') for k, v in node['dataPointsByNameMap'].items()}
        mapping = {'ticker': 'Issuer Ticker', 'issueName': 'Name', 'sectorName': 'Sector',
                   'assetClass': 'Asset Class', 'marketCurrencyCode': 'Market Currency', 'isin': 'ISIN',
                   'countryOfRisk': 'Location', 'maturityDate': 'Maturity', 'issueDate': 'Effective Date',
                   'couponRate': 'Coupon (%)'}
        required = {'issueName', 'assetClass', 'marketValue', 'holdingPercent', 'ticker', 'marketCurrencyCode'}
        count = len(points['issueName'])
        if not count or any(not isinstance(points.get(k), list) or len(points[k]) != count for k in required):
            raise ValueError('Incomplete full holdings arrays')
        for key in mapping:
            if key in points and (not isinstance(points[key], list) or len(points[key]) != count):
                raise ValueError('Inconsistent holdings columns')
        as_of = datetime.strptime(str(points['asOfDate']), '%Y%m%d').date()
        currency = doc['currencyCode']
        if not re.fullmatch('[A-Z]{3}', currency):
            raise ValueError('Missing fund currency')
        parsed = []
        for i in range(count):
            row = {label: str(points.get(key, [''] * count)[i] or '').strip() for key, label in mapping.items()}
            for key in ('Maturity', 'Effective Date'):
                if row[key]:
                    row[key] = datetime.strptime(row[key], '%Y%m%d').date().isoformat()
            amount = _number(str(points['marketValue'][i]))
            percentage = _number(str(points['holdingPercent'][i]))
            parsed.append((row, currency, amount, percentage))
        return _normalize_rows(parsed, as_of, Decimal('0.000006'))
    except (KeyError, ValueError, TypeError, IndexError, InvalidOperation) as exc:
        raise DataError(f'Invalid iShares holdings JSON: {exc}') from exc
