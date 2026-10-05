"""Invented portfolios, with explicit live and deterministic offline data modes."""
import csv
import json
from hashlib import sha256
import math
from tempfile import NamedTemporaryFile
from pathlib import Path

import yaml

from portfolio_app.holdings import DataError


STAMP = '2026-09-04T20:00:00+00:00'

# Invented starting values and buy-in ratios, shared by both demo modes.
# Category weights: 65.37/21.94/8.54/4.15%; targets: 60/25/10/5%.
LIVE_EXAMPLES = {
    'world': (44963.27, .874), 'emerging': (15954.84, 1.092),
    'money-market': (20441.67, .9853), 'gold': (7958.32, .823),
    'bitcoin': (2264.71, .8929), 'ethereum': (1601.54, 1.20),
}
OFFLINE_PRICES = {'XDWD.DE': 100., 'IS3N.DE': 50., 'XEON.DE': 150.,
                  'EWG2.SG': 100., 'BTC-EUR': 70000., 'ETH-EUR': 2000.}


def _write_csv(path, columns, rows):
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)


def create_demo_data(directory: Path, *, live: bool = False) -> Path:
    """Create an isolated example; live quotes size the initial holdings on first use."""
    marker = directory / '.synthetic-demo'
    if marker.exists():
        return directory
    if directory.exists() and any(directory.iterdir()):
        raise DataError('The demo directory is not empty. Use an empty directory; existing data will not be replaced.')
    directory.mkdir(parents=True, exist_ok=True)
    # All ownership and costs are invented; public identities identify the instruments.
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
    if not live:
        instruments = [(*row[:4], LIVE_EXAMPLES[row[0]][0] / OFFLINE_PRICES[row[2]],
                        round(OFFLINE_PRICES[row[2]] * LIVE_EXAMPLES[row[0]][1], 4), *row[6:])
                       for row in instruments]
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
              for ticker, price in OFFLINE_PRICES.items()}
    if not live:
        (directory / 'demo_prices.json').write_text(json.dumps({'prices': prices, 'fx': {c: dict(price=r, currency='EUR', observed_at=STAMP) for c, r in [('USD', .9), ('GBP', 1.2)]}}), encoding='utf-8')

    classifications = {
        'world': {'asset_class': [['Equity', 'Developed markets']]},
        'emerging': {'asset_class': [['Equity', 'Emerging markets']]},
        'money-market': {'asset_class': [['Money market', 'EUR overnight']],
                         'geography': [['Money market']]},
        'gold': {'asset_class': [['Commodities', 'Precious metals', 'Gold']]},
        'bitcoin': {'asset_class': [['Crypto', 'Bitcoin']]},
        'ethereum': {'asset_class': [['Crypto', 'Ethereum']]},
    }
    # Deliberately invented allocations, not scaled samples of actual issuer
    # holdings. Public company metadata provides varied sectors and countries.
    # A small residual still demonstrates honest incomplete-coverage handling.
    snapshots = [
        ('world', 'xtrackers_world', [
            ('nvda', 'Nvidia', 'NVDA', 'US67066G1040', .135, 'Technology', 'United States'),
            ('msft', 'Microsoft', 'MSFT', 'US5949181045', .125, 'Technology', 'United States'),
            ('aapl', 'Apple', 'AAPL', 'US0378331005', .115, 'Technology', 'United States'),
            ('jpm', 'JPMorgan Chase', 'JPM', '', .105, 'Financials', 'United States'),
            ('novo', 'Novo Nordisk', 'NOVO-B.CO', '', .10, 'Health Care', 'Denmark'),
            ('nestle', 'Nestlé', 'NESN.SW', '', .095, 'Consumer Staples', 'Switzerland'),
            ('toyota', 'Toyota', '7203.T', '', .09, 'Consumer Discretionary', 'Japan'),
            ('siemens', 'Siemens', 'SIE.DE', '', .08, 'Industrials', 'Germany'),
            ('schneider', 'Schneider Electric', 'SU.PA', '', .075, 'Industrials', 'France'),
            ('bhp', 'BHP', 'BHP.AX', '', .065, 'Materials', 'Australia'),
        ]),
        ('emerging', 'ishares_em_imi', [
            ('tsmc', 'TSMC', '2330.TW', 'TW0002330008', .22, 'Technology', 'Taiwan'),
            ('tencent', 'Tencent', '0700.HK', 'KYG875721634', .15, 'Communication Services', 'China'),
            ('samsung', 'Samsung Electronics', '005930.KS', 'KR7005930003', .12, 'Technology', 'South Korea'),
            ('reliance', 'Reliance Industries', 'RELIANCE.NS', '', .11, 'Energy', 'India'),
            ('hdfc', 'HDFC Bank', 'HDFCBANK.NS', '', .10, 'Financials', 'India'),
            ('alibaba', 'Alibaba', '9988.HK', '', .09, 'Consumer Discretionary', 'China'),
            ('vale', 'Vale', 'VALE3.SA', '', .08, 'Materials', 'Brazil'),
            ('alrajhi', 'Al Rajhi Bank', '1120.SR', '', .065, 'Financials', 'Saudi Arabia'),
            ('naspers', 'Naspers', 'NPN.JO', '', .055, 'Consumer Discretionary', 'South Africa'),
        ]),
    ]
    etfs = directory / 'etfs'
    etfs.mkdir()
    by_id = {row[0]: row for row in instruments}
    for asset, fund_id, constituents in ([] if live else snapshots):
        _, name, ticker, isin, *_ = by_id[asset]
        manifest = dict(fund_id=fund_id, name=name, isin=isin, tickers=[ticker],
                        as_of=STAMP[:10], source='Synthetic demo — invented constituent weights',
                        equity_fund=True, asset_class='equity', replication='physical', holdings_file=f'{fund_id}.csv',
                        notes='Offline illustration only; weights are invented, not actual fund holdings. '
                              'The remaining weight is shown as Other.')
        (etfs / f'{fund_id}.yaml').write_text(yaml.safe_dump(manifest, sort_keys=False), encoding='utf-8')
        _write_csv(etfs / f'{fund_id}.csv',
                   ['constituent_id', 'name', 'ticker', 'isin', 'weight', 'sector', 'country'], constituents)
        for identity, _, _, _, _, sector, country in constituents:
            classifications[identity] = {
                'asset_class': [['Equity', 'Developed markets' if asset == 'world' else 'Emerging markets']],
                'sector': [[sector]],
                'geography': [[country]],
            }
    if not live:
        # Mirror the live integration's economic model; its substitute basket
        # is neither the investment exposure nor a company-country allocation.
        fund_id = 'xtrackers_overnight'
        _, name, ticker, isin, *_ = by_id['money-market']
        (etfs / f'{fund_id}.yaml').write_text(yaml.safe_dump(dict(
            fund_id=fund_id, name=name, isin=isin, tickers=[ticker], as_of=STAMP[:10],
            source='Synthetic demo — invented offline snapshot',
            holdings_file=f'{fund_id}.csv', asset_class='money_market',
            replication='synthetic', breakdown_basis='economic',
            notes='Offline illustration with invented position values. Economic exposure follows the '
                  'overnight-rate benchmark, not a deposit or the substitute basket.'), sort_keys=False), encoding='utf-8')
        _write_csv(etfs / f'{fund_id}.csv',
                   ['constituent_id', 'name', 'ticker', 'isin', 'weight', 'instrument_type',
                    'exposure_kind', 'market_currency'],
                   [('overnight:eur-estr-plus-8.5bp',
                     'EUR overnight rate · Solactive €STR +8.5 Daily Total Return Index',
                     '', '', 1., 'overnight_rate', 'non_equity', 'EUR')])
    (directory / 'classifications.yaml').write_text(yaml.safe_dump({
        asset: {'classifications': paths} for asset, paths in classifications.items()
    }), encoding='utf-8')
    marker.write_text('Invented demo positions; not a personal portfolio.\n', encoding='utf-8')
    if live:
        (directory / '.live-demo').write_text(sha256((directory / 'holdings.csv').read_bytes()).hexdigest(), encoding='ascii')
    return directory


def live_demo_pending(directory: Path) -> bool:
    """Only the unedited seed can be sized; later edits and refreshes are preserved."""
    marker = directory / '.live-demo'
    return (marker.exists() and (directory / '.synthetic-demo').exists()
            and marker.read_text(encoding='ascii') == sha256((directory / 'holdings.csv').read_bytes()).hexdigest())


def initialize_live_demo(directory: Path, valued) -> bool:
    """Size invented positions once all six real quotes/FX are available, atomically.

    No network access here. Never mix synthetic prices into the live cache and
    never change quantities again when prices move or the workspace is revisited.
    """
    from portfolio_app.locking import write_lock
    with write_lock(directory / '.holdings.csv.lock'):
        if not live_demo_pending(directory):
            return False
        prices = {row.id: row.current_price * row.fx_to_reporting for row in valued.itertuples()}
        if set(prices) != set(LIVE_EXAMPLES) or any(not math.isfinite(p) or p <= 0 for p in prices.values()):
            return False
        path = directory / 'holdings.csv'
        with path.open(encoding='utf-8', newline='') as handle:
            reader = csv.DictReader(handle)
            columns, rows = reader.fieldnames, list(reader)
        for row in rows:
            value, cost_ratio = LIVE_EXAMPLES[row['id']]
            price = prices[row['id']]
            row['shares'] = str(round(value / price, 6))
            row['acquisition_price'] = str(round(price * cost_ratio, 4))
        with NamedTemporaryFile(mode='w', encoding='utf-8', newline='', dir=directory, delete=False) as handle:
            temporary = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return True
