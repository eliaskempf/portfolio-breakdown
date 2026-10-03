"""Deterministic demonstration portfolio; all financial values are invented.

Only instrument identities are public metadata. Prices, costs, quantities,
allocations and partial ETF weights are synthetic, never issuer observations.
"""
import csv
import json
from pathlib import Path

import yaml

from portfolio_app.holdings import DataError


STAMP = '2026-09-04T20:00:00+00:00'


def _write_csv(path, columns, rows):
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)


def create_demo_data(directory: Path) -> Path:
    """Create an isolated €100,000 example, preserving any existing workspace."""
    marker = directory / '.synthetic-demo'
    if marker.exists():
        return directory
    if directory.exists() and any(directory.iterdir()):
        raise DataError('The demo directory is not empty. Use an empty directory; existing data will not be replaced.')
    directory.mkdir(parents=True, exist_ok=True)
    # Actual sleeve values: 61/24/11/4 percent. Targets: 60/25/10/5.
    # Buy-ins deliberately give both gains and losses; nothing is sampled at runtime.
    instruments = [
        ('world', 'Xtrackers MSCI World UCITS ETF 1C', 'XDWD.DE', 'IE00BJ0KDQ92',
         450, 87.40, 'equity', .70, 'etf', 'equity'),
        ('emerging', 'iShares Core MSCI EM IMI UCITS ETF', 'IS3N.DE', 'IE00BKM4GZ66',
         320, 54.60, 'equity', .30, 'etf', 'equity'),
        ('money-market', 'Xtrackers II EUR Overnight Rate Swap UCITS ETF 1C', 'XEON.DE', 'LU0290358497',
         160, 147.80, 'money-market', 1., 'etf', 'non_equity'),
        ('gold', 'EUWAX Gold II', 'EWG2.SG', 'DE000EWG2LD7',
         110, 82.30, 'gold', 1., 'etc', 'non_equity'),
        ('bitcoin', 'Bitcoin', 'BTC-EUR', '', .04, 62500., 'crypto', .60, 'crypto', 'non_equity'),
        ('ethereum', 'Ethereum', 'ETH-EUR', '', .60, 2400., 'crypto', .40, 'crypto', 'non_equity'),
    ]
    _write_csv(directory / 'holdings.csv',
               ['id', 'name', 'ticker', 'isin', 'shares', 'acquisition_price', 'bucket_id',
                'within_bucket_target', 'instrument_type', 'exposure_kind', 'acquisition_currency', 'account'],
               [(*row, 'EUR', 'Demo wallet' if row[6] == 'crypto' else 'Demo depot') for row in instruments])
    (directory / 'allocation.yaml').write_text(yaml.safe_dump({'version': 2, 'buckets': [
        dict(id='equity', name='Equities', target=.60),
        dict(id='money-market', name='Money market', target=.25),
        dict(id='gold', name='Gold', target=.10),
        dict(id='crypto', name='Crypto', target=.05),
    ]}, sort_keys=False), encoding='utf-8')
    prices = {ticker: dict(price=price, currency='EUR', observed_at=STAMP)
              for ticker, price in [('XDWD.DE', 100.), ('IS3N.DE', 50.), ('XEON.DE', 150.),
                                    ('EWG2.SG', 100.), ('BTC-EUR', 70000.), ('ETH-EUR', 2000.)]}
    (directory / 'demo_prices.json').write_text(json.dumps({'prices': prices, 'fx': {}}), encoding='utf-8')

    classifications = {
        'world': {'asset_class': [['Equity', 'Developed markets']]},
        'emerging': {'asset_class': [['Equity', 'Emerging markets']]},
        'money-market': {'asset_class': [['Money market', 'EUR overnight']]},
        'gold': {'asset_class': [['Commodities', 'Precious metals', 'Gold']]},
        'bitcoin': {'asset_class': [['Crypto', 'Bitcoin']]},
        'ethereum': {'asset_class': [['Crypto', 'Ethereum']]},
    }
    # Small, deliberately partial snapshots exercise constituents and residual
    # Other without shipping provider exports or needing a network connection.
    snapshots = [
        ('world', 'xtrackers_world', [
            ('nvda', 'Nvidia', 'NVDA', 'US67066G1040', .08, 'Semiconductors', 'United States'),
            ('msft', 'Microsoft', 'MSFT', 'US5949181045', .06, 'Software', 'United States'),
            ('aapl', 'Apple', 'AAPL', 'US0378331005', .05, 'Hardware', 'United States'),
        ]),
        ('emerging', 'ishares_em_imi', [
            ('tsmc', 'TSMC', '2330.TW', 'TW0002330008', .12, 'Semiconductors', 'Taiwan'),
            ('tencent', 'Tencent', '0700.HK', 'KYG875721634', .05, 'Internet services', 'China'),
            ('samsung', 'Samsung Electronics', '005930.KS', 'KR7005930003', .04, 'Hardware', 'South Korea'),
        ]),
    ]
    etfs = directory / 'etfs'
    etfs.mkdir()
    by_id = {row[0]: row for row in instruments}
    for asset, fund_id, constituents in snapshots:
        _, name, ticker, isin, *_ = by_id[asset]
        manifest = dict(fund_id=fund_id, name=name, isin=isin, tickers=[ticker],
                        as_of=STAMP[:10], source='Synthetic demo — invented partial constituent weights',
                        equity_fund=True, holdings_file=f'{fund_id}.csv',
                        notes='Offline illustration only; weights are invented, not actual fund holdings. '
                              'The remaining weight is shown as Other.')
        (etfs / f'{fund_id}.yaml').write_text(yaml.safe_dump(manifest, sort_keys=False), encoding='utf-8')
        _write_csv(etfs / f'{fund_id}.csv',
                   ['constituent_id', 'name', 'ticker', 'isin', 'weight', 'sector', 'country'], constituents)
        for identity, _, _, _, _, sector, country in constituents:
            classifications[identity] = {
                'asset_class': [['Equity', 'Developed markets' if asset == 'world' else 'Emerging markets']],
                'sector': [['Technology', sector]],
                'geography': [['North America' if country == 'United States' else 'Asia', country]],
            }
    (directory / 'classifications.yaml').write_text(yaml.safe_dump({
        asset: {'classifications': paths} for asset, paths in classifications.items()
    }), encoding='utf-8')
    marker.write_text('Invented demo data; never use as actual portfolio or fund holdings.\n', encoding='utf-8')
    return directory
