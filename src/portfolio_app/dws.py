"""Narrow parser for the Xtrackers MSCI World public holdings JSON."""

from datetime import date, datetime
import json
import re

import pandas as pd

from portfolio_app.etf import validate_constituents
from portfolio_app.holdings import DataError

SOURCE = 'https://etf.dws.com/api/pdp/en-gb/etf/IE00BJ0KDQ92-msci-world-ucits-etf-1c/holdings'


def parse_holdings(content: bytes, *, allow_signed: bool = False) -> tuple[date, pd.DataFrame, str]:
    try:
        tables = json.loads(content)['tables']
        table = next(t for t in tables if t['id'] == 'securitiesheldtable-securitiesholding')
        headers = {c['key']: c['value'] for c in table['columns']}
        expected = {'header': 'ISIN', 'column_0': 'Name', 'column_1': '% Weight',
                    'column_3': 'Country', 'column_4': 'Industry', 'column_5': 'Asset class'}
        if any(headers.get(k) != v for k, v in expected.items()):
            raise ValueError('Unexpected columns')
        stamp = re.search(r'Source: DWS (\d{2}/\d{2}/\d{4})', ' '.join(d['text'] for d in table['disclaimers']))
        if stamp is None:
            raise ValueError('Missing source date')
        as_of = datetime.strptime(stamp[1], '%d/%m/%Y').date()
        records = []
        for row in table['values']:
            identifier = row['header']['value'].strip()
            isin = identifier if re.fullmatch(r'[A-Z]{2}[A-Z0-9]{9}\d', identifier) else ''
            kind = row['column_5']['value']
            records.append({
                'constituent_id': f'isin:{isin}' if isin else f'dws:{identifier}',
                'name': row['column_0']['value'], 'ticker': '', 'isin': isin,
                'weight': float(row['column_1']['sortValue']) / 100,
                'instrument_type': {'Equities': 'equity', 'Depository Receipts': 'equity', 'Cash': 'cash', 'Bonds': 'bond', 'Bond': 'bond', 'Government Bond': 'bond', 'Supranational Bond': 'bond', 'Corporate Bond': 'bond', 'Mutual Fund': 'etf', 'Fixed Income': 'bond', 'Money Market': 'money_market'}.get(kind, 'unknown'),
                'sector': row['column_4']['value'], 'country': row['column_3']['value'],
            })
        return as_of, validate_constituents(pd.DataFrame(records), allow_signed=allow_signed), 'Published full-precision weights; any rounding remainder stays in Other.'
    except (KeyError, ValueError, TypeError, StopIteration) as exc:
        raise DataError(f'Invalid Xtrackers holdings export: {exc}') from exc
