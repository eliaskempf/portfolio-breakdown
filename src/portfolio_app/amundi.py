"""Official Amundi product API: physical portfolios and reviewed overnight economics."""
from datetime import date
import json
from functools import partial
from urllib.parse import urlparse

from portfolio_app.provider_data import published_frame, security_id, weight

import pandas as pd

from portfolio_app.etf import validate_constituents
from portfolio_app.holdings import DataError
from portfolio_app.instruments import valid_isin

ISIN = 'LU1190417599'
PRODUCT_URL = ('https://www.amundietf.lu/en/individual/products/fixed-income/'
               'amundi-smart-overnight-return-ucits-etf-acc/lu1190417599')
API_URL = 'https://www.amundietf.lu/mapi/ProductAPI/getProductsData'
BENCHMARK = 'ESTR Compounded Index'


def request_body(isin=ISIN):
    return {
        'productIds': [isin],
        'context': {'countryCode': 'LUX', 'languageCode': 'en', 'userProfileName': 'RETAIL'},
        'characteristics': ['ISIN', 'SHARE_MARKETING_NAME', 'BENCHMARK_NAME',
                            'REPLICATION_METHODOLOGY', 'BASE_CURRENCY', 'POSITION_AS_OF_DATE', 'WKN', 'ASSET_CLASS'],
        'metrics': [], 'historics': [], 'breakDown': {'aggregationFields': ['FUND_TOP10']},
        **({'composition': {'compositionFields': ['date', 'type', 'bbg', 'isin', 'name', 'weight',
                'currency', 'sector', 'countryOfRisk']}} if isin != ISIN else {}),
    }


def product(content, expected_isin=ISIN):
    try:
        products = json.loads(content)['products']
        if len(products) != 1:
            raise ValueError('Expected one exact product')
        item = products[0]
        facts = item['characteristics']
        if item['productId'] != expected_isin or facts['ISIN'] != expected_isin:
            raise ValueError('Provider identity does not match the requested ISIN')
        if expected_isin == ISIN and (facts['BENCHMARK_NAME'] != BENCHMARK or facts['BASE_CURRENCY'] != 'EUR'
                or facts['REPLICATION_METHODOLOGY'] != 'Indirect (Unfunded swap)'):
            raise ValueError('Provider benchmark, currency or replication changed; review economic interpretation')
        if not isinstance(facts['SHARE_MARKETING_NAME'], str) or not facts['SHARE_MARKETING_NAME'].strip():
            raise ValueError('Missing product name')
        stamp = date.fromisoformat(facts['POSITION_AS_OF_DATE'])
        return item, facts, stamp
    except (KeyError, ValueError, TypeError, IndexError) as exc:
        raise DataError(f'Invalid Amundi product data: {exc}') from exc


def discover(fetch, isin=ISIN, *, product_url='', content=None):
    from portfolio_app.etf_sources import Source
    body = request_body(isin)
    _, facts, _ = product(content if content is not None else fetch(API_URL, json_body=body), isin)
    if isin == ISIN:
        return ISIN, Source('isin_' + ISIN.lower(), facts['SHARE_MARKETING_NAME'], (), 'Amundi',
                            API_URL, parse_basket, asset_class='money_market', replication='synthetic',
                            breakdown_basis='economic', product_url=PRODUCT_URL,
                            wkn=str(facts.get('WKN') or ''), request_json=body)
    if facts['REPLICATION_METHODOLOGY'] != 'Direct (Physical)':
        raise DataError('Economic breakdown is not yet supported for this Amundi synthetic strategy')
    asset = {'Equity': 'equity', 'Fixed Income': 'fixed_income'}.get(facts.get('ASSET_CLASS'))
    if not asset:
        raise DataError('Unsupported Amundi asset class')
    if not product_url:
        result = fetch('https://www.amundietf.lu/mapi/PageContentAPI/getProductPageUrlByIdAndContext',
                       json_body={'productPageId': isin, 'context': body['context']}).decode().strip()
        product_url = result if result.startswith('https://') else 'https://' + result
    parsed = urlparse(product_url)
    if parsed.hostname not in {'www.amundietf.lu', 'www.amundietf.com', 'www.amundietf.de', 'www.amundietf.co.uk'} or parsed.path.rstrip('/').split('/')[-1].upper() != isin:
        raise DataError('Amundi product URL does not confirm the requested ISIN')
    return isin, Source('isin_' + isin.lower(), facts['SHARE_MARKETING_NAME'], (), 'Amundi', API_URL,
        partial(parse_holdings, expected_isin=isin), asset_class=asset, product_url=product_url,
        wkn=str(facts.get('WKN') or ''), request_json=body)


def lookup(isin, fetch):
    content = fetch(API_URL, json_body=request_body(isin))
    doc = json.loads(content)
    if not doc.get('products'):
        return None
    return discover(fetch, isin, content=content)


def parse_holdings(content, *, expected_isin):
    try:
        item, facts, stamp = product(content, expected_isin)
        if facts['REPLICATION_METHODOLOGY'] != 'Direct (Physical)':
            raise ValueError('Replication method changed')
        composition = item['composition']
        rows = composition['compositionData']
        count = composition['totalNumberOfInstruments']
        if not rows or isinstance(count, bool) or not isinstance(count, int) or count != len(rows):
            raise ValueError('Incomplete holdings composition')
        kinds = {'EQUITY_ORDINARY': 'equity', 'PREFERENCE_SHARES': 'equity',
                 'DEPOSITORY_RECEIPT': 'equity', 'GOVERNMENT': 'bond', 'CORPORATE': 'bond',
                 'CASH': 'cash'}
        records, reason = [], ''
        for row in rows:
            props = row['compositionCharacteristics']
            if date.fromisoformat(props['date']) != stamp:
                raise ValueError('Inconsistent holdings dates')
            w = weight(row['weight'])
            if w != weight(props['weight']) or abs(w) > 1:
                raise ValueError('Inconsistent holdings weights')
            kind = props['type']
            if kind == 'CASH' and w < 0:
                reason = 'the full portfolio includes cash borrowing'
                continue
            if w < 0:
                raise ValueError('Short securities are unsupported')
            if kind not in kinds:
                reason = reason or 'the full portfolio includes unsupported security types'
                continue
            name = props.get('name') or ('Cash ' + str(props.get('currency') or '') if kind == 'CASH' else '')
            identifier, isin = security_id('amundi', props.get('isin'), name if kind == 'CASH' else props.get('bbg'))
            records.append(dict(constituent_id=identifier, isin=isin, ticker='', name=name,
                weight=w, instrument_type=kinds[kind], market_currency=props.get('currency') or '',
                sector=props.get('sector') or '', country=props.get('countryOfRisk') or '',
                source_ticker=props.get('bbg') or ''))
        frame, notes = published_frame(records, partial_reason=reason)
        return stamp, frame, notes
    except (KeyError, ValueError, TypeError, IndexError) as exc:
        raise DataError(f'Invalid Amundi holdings composition: {exc}') from exc


def parse_basket(content):
    item, _, stamp = product(content)
    try:
        tables = [table for table in item.get('breakDowns', []) if table['aggregationField'] == 'FUND_TOP10']
        if len(tables) > 1:
            raise ValueError('Ambiguous substitute basket')
        rows = tables[0]['breakDownData'] if tables else []
        records = []
        for row in rows:
            props = row['additionalProperties']
            isin = valid_isin(props.get('isin'))
            if not isin or not isinstance(row['adjustedWeight'], (int, float)) or isinstance(row['adjustedWeight'], bool):
                raise ValueError('Basket requires valid security ISINs and numeric fractional weights')
            records.append(dict(constituent_id='isin:' + isin, name=row['aggregationName'], isin=isin,
                ticker='', weight=row['adjustedWeight'], instrument_type='unknown',
                market_currency=props.get('currency', ''), source_ticker=props.get('bbg', '')))
        frame = pd.DataFrame(records, columns=['constituent_id', 'name', 'isin', 'ticker', 'weight',
                                             'instrument_type', 'market_currency', 'source_ticker'])
        if records:
            frame = validate_constituents(frame, allow_signed=True)
        notes = ('Partial substitute basket: provider top holdings at published weights, not rescaled. '
                 'Unreported basket holdings remain unspecified.' if records else 'Substitute basket unavailable from provider.')
        return stamp, frame, notes
    except (KeyError, ValueError, TypeError, IndexError) as exc:
        raise DataError(f'Invalid Amundi substitute basket: {exc}') from exc
