"""Vanguard European ETF discovery and paginated official GPX holdings."""
from datetime import date
from functools import partial
import json
import math
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

from portfolio_app.holdings import DataError
from portfolio_app.provider_data import published_frame, security_id, weight

API_URL = 'https://www.vanguard.co.uk/gpx/graphql'
SITEMAP = 'https://www.vanguard.co.uk/professional/sitemap.xml'
PROFILE = '''profile { portId fundFullName assetClassificationLevel1
    polarisPdtTypeIndicator etfReplicationMethodology
    identifiers(altIds: ["ISIN", "WKN Code"]) { altId altIdValue }
    listings { stockExchangeMarketIdentifierCode
      identifiers { altId altIdValue } } }'''
HOLDINGS = '''borHoldings(portIds: $ids) {
    holdings(limit: 1500, lastItemKey: $cursor) {
      items { isin sedol1 securityLongDescription marketValuePercentage securityType
        effectiveDate issuerName ticker gicsSectorDescription bloombergIsoCountry finalMaturity }
      totalHoldings lastItemKey } }'''


def query(fetch, text, variables):
    doc = json.loads(fetch(API_URL, json_body={'query': text, 'variables': variables},
                           request_headers={'x-consumer-id': 'uk2'}))
    if doc.get('errors') or not isinstance(doc.get('data'), dict):
        raise DataError('Vanguard returned an incomplete or invalid response')
    return doc['data']


def port_id(url):
    parsed = urlparse(url)
    match = re.fullmatch(r'/professional/product/etf/[^/]+/([A-Z0-9]+)/[^/]+/?', parsed.path)
    if parsed.scheme != 'https' or parsed.hostname != 'www.vanguard.co.uk' or not match:
        raise DataError('Use an official Vanguard UK professional ETF product page')
    return match[1]


def catalog(fetch):
    root = ET.fromstring(fetch(SITEMAP))
    urls = {}
    for node in root.iter():
        if node.tag.endswith('}loc') and node.text:
            try:
                urls[port_id(node.text)] = node.text
            except DataError:
                continue
    if not urls:
        raise DataError('Vanguard product catalogue is unavailable')
    data = query(fetch, 'query($ids:[String!]!) { funds(portIds:$ids) { ' + PROFILE + ' } }', {'ids': list(urls)})
    return [(item['profile'], urls.get(item['profile']['portId'], '')) for item in data['funds']]


def identity(profile):
    return {x['altId']: x['altIdValue'] for x in profile['identifiers']}


def checked_profile(profile, expected_isin=''):
    ids = identity(profile)
    isin = ids.get('ISIN', '')
    security_id('vanguard', isin, '')
    if expected_isin and isin != expected_isin:
        raise DataError('Vanguard identity does not match the requested ISIN')
    if profile['polarisPdtTypeIndicator'] != 'ETF' or profile['etfReplicationMethodology'] != 'Physical':
        raise DataError('Only physical Vanguard ETFs are supported')
    asset = {'Equity': 'equity', 'Bond': 'fixed_income'}.get(profile['assetClassificationLevel1'])
    if not asset or not profile['fundFullName']:
        raise DataError('Unsupported Vanguard fund asset class or missing name')
    return isin, asset, ids


def source(url, fetch, *, expected_isin=''):
    from portfolio_app.etf_sources import Source
    pid = port_id(url)
    items = query(fetch, 'query($ids:[String!]!) { funds(portIds:$ids) { ' + PROFILE + ' } }', {'ids': [pid]})['funds']
    if len(items) != 1 or items[0]['profile']['portId'] != pid:
        raise DataError('Ambiguous Vanguard product identity')
    profile = items[0]['profile']
    isin, asset, ids = checked_profile(profile, expected_isin)
    suffixes = {'Deutsche Boerse Ticker': '.DE', 'TIDM': '.L', 'NYSE Euronext Exchange Ticker': '.AS',
                'Borsa Italiana Ticker': '.MI', 'SIX Swiss Exchange Ticker': '.SW'}
    tickers = tuple(dict.fromkeys(x['altIdValue'] + suffixes[x['altId']]
        for listing in profile.get('listings', []) for x in listing['identifiers']
        if x['altId'] in suffixes and x['altIdValue']))
    return isin, Source('isin_' + isin.lower(), profile['fundFullName'], tickers, 'Vanguard', API_URL,
        partial(parse_holdings, expected_isin=isin), asset_class=asset, product_url=url,
        wkn=ids.get('WKN Code', ''), load_content=partial(load_holdings, pid, isin))


def load_holdings(pid, isin, fetch):
    pages, seen, cursor, total = [], set(), None, None
    for _ in range(40):
        data = query(fetch, 'query($ids:[String!]!, $cursor:String) { funds(portIds:$ids) { ' + PROFILE + ' } ' + HOLDINGS + ' }',
                     {'ids': [pid], 'cursor': cursor})
        if len(data['funds']) != 1 or data['funds'][0]['profile']['portId'] != pid or len(data['borHoldings']) != 1:
            raise DataError('Ambiguous Vanguard holdings identity')
        checked_profile(data['funds'][0]['profile'], isin)
        page = data['borHoldings'][0]['holdings']
        count = page['totalHoldings']
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0 or (total is not None and count != total):
            raise DataError('Vanguard holdings count changed during pagination')
        total = count
        if not page['items']:
            raise DataError('Empty Vanguard holdings page')
        pages.extend(page['items'])
        cursor = page['lastItemKey']
        if not cursor:
            if len(pages) != total:
                raise DataError('Incomplete Vanguard holdings export')
            return json.dumps({'isin': isin, 'items': pages}).encode()
        if cursor in seen:
            raise DataError('Repeated Vanguard holdings page')
        seen.add(cursor)
    raise DataError('Vanguard holdings pagination limit exceeded')


def parse_holdings(content, *, expected_isin):
    try:
        doc = json.loads(content)
        if doc['isin'] != expected_isin:
            raise ValueError('Holdings identity mismatch')
        rows = doc['items']
        dates = {date.fromisoformat(row['effectiveDate']) for row in rows}
        if len(dates) != 1:
            raise ValueError('Missing or inconsistent holdings dates')
        records, omitted, borrowing = [], False, False
        for row in rows:
            kind = row['securityType']
            w = weight(row['marketValuePercentage']) / 100
            if abs(w) > 1:
                raise ValueError('Invalid holdings weight')
            if kind in {'CT.SPOT', 'CT.FOREX', 'CT.PORTSWAP', 'DE.IND', 'DE.COMM', 'EQ.RIGHT', 'EQ.WRT'}:
                omitted = True
                continue
            if kind == 'CRNY' and w < 0:
                borrowing = True
                continue
            instrument = ('cash' if kind == 'CRNY' else 'bond' if kind.startswith('FI.') else 'money_market' if kind.startswith('MM.')
                          else 'etf' if kind in {'EQ.ETF', 'MF.MF'} else 'equity' if kind in
                          {'EQ.STOCK', 'EQ.DRCPT', 'EQ.PREF', 'EQ.REIT', 'EQ.FSH', 'EQ.PSH'} else 'unknown')
            if instrument == 'unknown':
                raise ValueError(f'Unsupported security type: {kind}')
            if w < 0:
                raise ValueError('Short securities are unsupported')
            identifier, isin = security_id('vanguard', row.get('isin'), row.get('sedol1') or (row.get('ticker') if kind == 'CRNY' else None))
            records.append(dict(constituent_id=identifier, isin=isin, ticker='',
                name=row['securityLongDescription'], weight=w,
                instrument_type=instrument, sector=row.get('gicsSectorDescription') or '',
                country='', source_country=row.get('bloombergIsoCountry') or '', source_ticker=row.get('ticker') or '',
                maturity=row.get('finalMaturity') or '', issuer=row.get('issuerName') or '',
                market_currency=row.get('ticker') if kind == 'CRNY' else ''))
        grouped = {}
        for record in records:
            key = record['constituent_id']
            if key not in grouped:
                grouped[key] = [record]
            else:
                grouped[key].append(record)
        records = []
        for lots in grouped.values():
            record = lots[0].copy()
            for key in record.keys() - {'constituent_id', 'isin', 'weight'}:
                if len({lot[key] for lot in lots}) > 1:
                    if key in {'name', 'instrument_type', 'maturity'}:
                        raise ValueError('Conflicting metadata for the same security ISIN')
                    record[key] = ''
            record['weight'] = math.fsum(lot['weight'] for lot in lots)
            records.append(record)
        reason = 'the full portfolio includes cash borrowing' if borrowing else ''
        frame, notes = published_frame(records, partial_reason=reason)
        if omitted:
            notes += ' Derivatives and rights are excluded; their economic exposure is not inferred.'
        notes += ' Provider trading-country labels are retained as source metadata, not company-country exposure.'
        return dates.pop(), frame, notes
    except (KeyError, ValueError, TypeError, IndexError) as exc:
        raise DataError(f'Invalid Vanguard holdings: {exc}') from exc
