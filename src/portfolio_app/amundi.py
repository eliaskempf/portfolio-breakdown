"""Narrow official-source adapter for the Amundi EUR overnight swap fund.

The provider's product-page widget uses adjustedWeight (fractions) for its
FUND_TOP10 table. These securities are a substitute basket, not economic equity
exposure. No product catalogue or universal Amundi import is attempted.
"""
from datetime import date
import json

import pandas as pd

from portfolio_app.etf import validate_constituents
from portfolio_app.holdings import DataError
from portfolio_app.instruments import valid_isin

ISIN = 'LU1190417599'
PRODUCT_URL = ('https://www.amundietf.lu/en/individual/products/fixed-income/'
               'amundi-smart-overnight-return-ucits-etf-acc/lu1190417599')
API_URL = 'https://www.amundietf.lu/mapi/ProductAPI/getProductsData'
BENCHMARK = 'ESTR Compounded Index'


def request_body():
    return {
        'productIds': [ISIN],
        'context': {'countryCode': 'LUX', 'languageCode': 'en', 'userProfileName': 'RETAIL'},
        'characteristics': ['ISIN', 'SHARE_MARKETING_NAME', 'BENCHMARK_NAME',
                            'REPLICATION_METHODOLOGY', 'BASE_CURRENCY', 'POSITION_AS_OF_DATE', 'WKN'],
        'metrics': [], 'historics': [], 'breakDown': {'aggregationFields': ['FUND_TOP10']},
    }


def product(content):
    try:
        products = json.loads(content)['products']
        if len(products) != 1:
            raise ValueError('Expected one exact product')
        item = products[0]
        facts = item['characteristics']
        if item['productId'] != ISIN or facts['ISIN'] != ISIN:
            raise ValueError('Provider identity does not match the requested ISIN')
        if (facts['BENCHMARK_NAME'] != BENCHMARK or facts['BASE_CURRENCY'] != 'EUR'
                or facts['REPLICATION_METHODOLOGY'] != 'Indirect (Unfunded swap)'):
            raise ValueError('Provider benchmark, currency or replication changed; review economic interpretation')
        if not isinstance(facts['SHARE_MARKETING_NAME'], str) or not facts['SHARE_MARKETING_NAME'].strip():
            raise ValueError('Missing product name')
        stamp = date.fromisoformat(facts['POSITION_AS_OF_DATE'])
        return item, facts, stamp
    except (KeyError, ValueError, TypeError, IndexError) as exc:
        raise DataError(f'Invalid Amundi product data: {exc}') from exc


def discover(fetch):
    from portfolio_app.etf_sources import Source
    _, facts, _ = product(fetch(API_URL, json_body=request_body()))
    return ISIN, Source('isin_' + ISIN.lower(), facts['SHARE_MARKETING_NAME'], (), 'Amundi',
                        API_URL, parse_basket, asset_class='money_market', replication='synthetic',
                        breakdown_basis='economic', product_url=PRODUCT_URL,
                        wkn=str(facts.get('WKN') or ''), request_json=request_body())


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
