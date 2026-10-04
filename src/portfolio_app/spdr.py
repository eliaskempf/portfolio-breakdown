"""State Street/SPDR European catalogue and daily holdings spreadsheets."""
from datetime import datetime
from functools import partial
from io import BytesIO
import json
import re
from urllib.parse import urljoin, urlparse

import pandas as pd

from portfolio_app.holdings import DataError
from portfolio_app.provider_data import ProductHTML, published_frame, security_id, weight

CATALOG = 'https://www.ssga.com/bin/v1/ssmp/fund/fundfinder?country=de&language=en_gb&role=intermediary&product=&ui=fund-finder'


def catalog(fetch):
    rows = json.loads(fetch(CATALOG))['data']['funds']['etfs']['datas']
    return [(set(re.findall(r'\b[A-Z]{2}[A-Z0-9]{9}\d\b', row['keywords'])),
             urljoin('https://www.ssga.com', row['fundUri'])) for row in rows]


def source(url, fetch, *, expected_isin=''):
    from portfolio_app.etf_sources import Source
    parsed = urlparse(url)
    if parsed.hostname != 'www.ssga.com' or not re.fullmatch(r'/[a-z]{2}/[a-z_]+/(?:intermediary|individual|institutional)/etfs/[a-z0-9-]+', parsed.path):
        raise DataError('Use an official State Street/SPDR ETF product page')
    page = ProductHTML(fetch(url))
    isin = page.meta.get('ISIN', '')
    security_id('spdr', isin, '')
    if expected_isin and isin != expected_isin:
        raise DataError('State Street identity does not match the requested ISIN')
    method = next((row[-1] for row in page.rows if row[0] == 'Replication Method'), '')
    if method not in {'Replicated', 'Stratified Sampling', 'Optimised'}:
        raise DataError('Only verified physical State Street/SPDR ETFs are supported')
    asset = {'Equity': 'equity', 'Fixed Income': 'fixed_income'}.get(page.meta.get('assetClass'))
    if not asset:
        raise DataError('Unsupported State Street fund asset class')
    urls = {urljoin(url, link) for link in page.links if re.search(r'/holdings-daily-emea-en-[a-z0-9-]+\.xlsx$', link)}
    if len(urls) != 1:
        raise DataError('No unique official State Street daily holdings export')
    holdings = urls.pop()
    if urlparse(holdings).hostname != 'www.ssga.com':
        raise DataError('Untrusted State Street holdings link')
    name = page.meta.get('fundName') or page.meta.get('title') or ''
    # The workbook also confirms the identity and provides the exact share-class name.
    if not name:
        raw = pd.read_excel(BytesIO(fetch(holdings)), engine='calamine', header=None, nrows=2).fillna('')
        if raw.iloc[0, 0] != 'Fund Name:' or raw.iloc[1, 1] != isin:
            raise DataError('State Street workbook identity mismatch')
        name = str(raw.iloc[0, 1])
    return isin, Source('isin_' + isin.lower(), name, (), 'State Street/SPDR', holdings,
        partial(parse_holdings, expected_isin=isin, asset_class=asset), asset_class=asset, product_url=url)


def parse_holdings(content, *, expected_isin, asset_class):
    try:
        raw = pd.read_excel(BytesIO(content), engine='calamine', header=None).fillna('')
        if raw.iloc[0, 0] != 'Fund Name:' or raw.iloc[1, 0] != 'ISIN:' or raw.iloc[1, 1] != expected_isin:
            raise ValueError('Workbook identity does not match the requested ISIN')
        if raw.iloc[3, 0] != 'Holdings As Of:':
            raise ValueError('Missing holdings date')
        stamp = datetime.strptime(raw.iloc[3, 1], '%d-%b-%Y').date()
        headers = raw.iloc[5].tolist()
        required = {'ISIN', 'SEDOL', 'Security Name', 'Percent of Fund'}
        required |= {'Currency', 'Trade Country Name', 'Sector Classification'} if asset_class == 'equity' else {'Currency Local', 'Maturity Date', 'Country of Issue'}
        if not required.issubset(headers) or len(headers) != len(set(headers)):
            raise ValueError('Unexpected spreadsheet columns')
        records, omitted, ended = [], False, False
        for values in raw.iloc[6:].values:
            row = dict(zip(headers, values, strict=True))
            if not any(values):
                ended = True
                continue
            if ended:
                if row['Security Name'] or row['Percent of Fund'] != '':
                    raise ValueError('Unexpected holdings after table end')
                continue  # Issuer legal footer, not a position.
            raw_isin = row['ISIN']
            if row['Percent of Fund'] == '-' and raw_isin in {'Unassigned', '-'}:
                omitted = True
                continue  # Provider explicitly withholds these weights.
            isin = '' if raw_isin in {'Unassigned', '-'} else raw_isin
            name = row['Security Name']
            identifier, isin = security_id('spdr', isin, row['SEDOL'] if row['SEDOL'] not in {'', '-'} else name)
            kind = ('cash' if name.startswith('Cash_') else 'unknown' if not isin else
                    'equity' if asset_class == 'equity' else 'bond')
            maturity = row.get('Maturity Date')
            maturity = datetime.strptime(maturity, '%d-%b-%Y').date().isoformat() if maturity and maturity != '-' else ''
            records.append(dict(constituent_id=identifier, isin=isin, ticker='', name=name,
                weight=weight(row['Percent of Fund']) / 100, instrument_type=kind,
                market_currency=row.get('Currency', row.get('Currency Local', '')),
                country=row.get('Country of Issue', ''), source_country=row.get('Trade Country Name', ''),
                sector=row.get('Sector Classification', ''), maturity=maturity))
        frame, notes = published_frame(records)
        if omitted:
            notes += ' Rows with unpublished weights (including cash/derivatives) are excluded; their exposure is not inferred.'
        return stamp, frame, notes
    except (KeyError, ValueError, TypeError, IndexError) as exc:
        raise DataError(f'Invalid State Street holdings export: {exc}') from exc
