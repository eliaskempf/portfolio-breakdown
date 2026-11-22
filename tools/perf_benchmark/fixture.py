"""Repeatable two-year portfolio. All identities and prices are invented."""
import csv
import json
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import yaml

from portfolio_app.ledger.account_journal import Journal, save_journal
from portfolio_app.ledger.history_commands import book_for, reconstruction_candidate
from portfolio_app.ledger.security_history import SecurityEvent
from portfolio_app.performance.history_observations import Observation, ObservationStore, save_observations
from portfolio_app.portfolio.positions import read_snapshot
from portfolio_app.workspace.display_preferences import save_overview_view


FUND_SIZES = (50, 500, 1500) * 8
DIRECT_EQUITIES = 24


def company(index):
    return f'company-{index}', f'Invented company {index}', f'ZZC-{index}', f'ZZ{index:010d}'


def holdings_and_funds(directory, profile, today):
    if profile == 'small':
        for index in range(12):
            yield f'invented-{index}', f'Invented holding {index}', '', '', 'other', 'non_equity'
        return
    if profile not in {'etf-heavy', 'mixed-history', 'multi-account'}:
        raise ValueError(f'Unknown portfolio profile {profile!r}')
    directory.mkdir()
    for index in range(DIRECT_EQUITIES):
        yield *company(index), 'equity', 'equity'
    for index, size in enumerate(FUND_SIZES):
        fund_id = f'fund-{index}'
        name, ticker, isin = f'Invented fund {index}', f'ZZF-{index}', f'ZZF{index:09d}'
        manifest = dict(fund_id=fund_id, name=name, isin=isin, tickers=[ticker], as_of=today,
                        source='Invented benchmark data', holdings_file=f'{fund_id}.csv', equity_fund=True)
        (directory / f'{fund_id}.yaml').write_text(yaml.safe_dump(manifest), encoding='utf-8')
        with (directory / manifest['holdings_file']).open('w', newline='', encoding='utf-8') as handle:
            writer = csv.writer(handle)
            sectors = profile in {'mixed-history', 'multi-account'}
            writer.writerow(['constituent_id', 'name', 'ticker', 'isin', 'weight', 'instrument_type', *(['sector'] if sectors else [])])
            # Half the constituents share identities across funds, including directly held equities.
            # The narrow snapshots cover 90%, exercising the residual allocation as well.
            coverage = .9 if size == 50 else 1.
            for row in range(size):
                identity = row if row < size // 2 else (index + 1) * max(FUND_SIZES) + row
                weight = coverage * (size - row) / (size * (size + 1) / 2)
                constituent = company(identity)
                if sectors and DIRECT_EQUITIES <= row < size // 2 and row % 4 == 0 and index % 3 != 0:
                    constituent = (f'local-{index}-{row}', constituent[1], '', '')
                sector = [f'Invented sector {identity % 24}'] if sectors else []
                writer.writerow([*constituent, weight, 'equity', *sector])
        yield fund_id, name, ticker, isin, 'etf', 'equity'


def create_workspace(directory: Path, profile='small', *, today=None):
    directory.mkdir()
    today = today or date.today()
    start = today - timedelta(days=730)
    def day(offset):
        return (start + timedelta(days=offset)).isoformat()

    columns = ('position_key id name ticker isin shares acquisition_price acquisition_currency bucket_id '
               'instrument_type exposure_kind quantity_unit manual_price manual_price_currency manual_price_date')
    multi_account = profile == 'multi-account'
    if multi_account:
        columns += ' account'
    with (directory / 'holdings.csv').open('w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(columns.split())
        for index, (identity, name, ticker, isin, kind, exposure) in enumerate(
                holdings_and_funds(directory / 'etfs', profile, today)):
            for account in range(4 if multi_account and kind == 'etf' else 1):
                position = f'{identity}-account-{account}' if multi_account else identity
                row = [position, identity, name, ticker, isin, 25, 100, 'EUR', f'group{index % 3}',
                       kind, exposure, 'units', 110 + index, 'EUR', today.isoformat()]
                if multi_account:
                    row.append(f'Invented account {account + 1}')
                writer.writerow(row)
    (directory / 'allocation.yaml').write_text('version: 2\nbuckets:\n' + ''.join(
        f'- id: group{i}\n  name: Invented group {i}\n  target: {target}\n'
        for i, target in enumerate((.4, .3, .3))), encoding='utf-8')
    rows = read_snapshot(directory / 'holdings.csv').holdings
    journal, books, observations = Journal(version=2), [], []
    instrument_order = {asset: i for i, asset in enumerate(rows.id.unique())}
    for index, (_, row) in enumerate(rows.iterrows()):
        book = book_for(journal, row)
        buys = tuple(SecurityEvent(f'b-{index}-{t}', 'buy', day(t * 30), quantity=Decimal(1),
            price=Decimal(100), currency='EUR', charges_known=True) for t in range(25))
        books.append(reconstruction_candidate(replace(book, records=buys),
            tuple(event.id for event in buys), starts_at_zero=True))
        price_change = 10 + instrument_order[row.id]
        observations.extend(Observation(f'o-{index}-{t}', 'price', book.position_id, day(t),
            100 + Decimal(price_change * t) / 730, 'EUR', source='invented') for t in range(731))
    save_journal(directory, replace(journal, securities=tuple(books)), None)
    save_observations(directory, ObservationStore(tuple(observations)), None)
    save_overview_view(directory, 'Allocation')

    if profile in {'mixed-history', 'multi-account'}:
        from portfolio_app.monitoring.monitoring import Evaluation, Rule, SeriesSpec
        from portfolio_app.monitoring.monitoring_store import save_rule, record_evaluations, delete_rule
        rule = Rule('invented-archived', 'Invented historical monitor', series=SeriesSpec('ZZ-ARCHIVED'))
        save_rule(directory, rule)
        checks = (Evaluation((today - timedelta(days=days)).isoformat(), 1.) for days in range(4000, 0, -1))
        record_evaluations(directory, rule, checks, now=datetime.combine(today, time.min, timezone.utc))
        delete_rule(directory, rule.id, rule.revision)

def benchmark_fundamentals(ticker, ratio):
    from portfolio_app.analytics.fundamentals import normalize_info

    if ticker.startswith('ZZC-'):
        return normalize_info(ticker, {'quoteType': 'EQUITY', 'trailingPE': ratio})
    if ticker.startswith('ZZF-'):
        return normalize_info(ticker, {'quoteType': 'ETF'}, {'fee': .0025})
    raise ValueError('Unexpected listing in synthetic fundamentals benchmark')


def prepare_fundamental_check(directory):
    from dataclasses import asdict
    from portfolio_app.analytics.analytics_cache import AnalyticsCache

    expired = datetime.now(timezone.utc) - timedelta(days=2)
    cache = AnalyticsCache(directory / '.cache' / 'fundamentals', now=lambda: expired)
    with (directory / 'holdings.csv').open(newline='', encoding='utf-8') as handle:
        tickers = dict.fromkeys(row['ticker'] for row in csv.DictReader(handle))
    for ticker in tickers:
        cache.get('fundamentals-v1:' + ticker, lambda: asdict(benchmark_fundamentals(ticker, 10.)))


def prepare_fund_check(directory):
    """Publish a stale synthetic snapshot whose next identical export can be checked."""
    from portfolio_app.funds.etf import load_funds
    from portfolio_app.funds.etf_sources import publish_snapshot
    manifest = directory / 'etfs' / 'fund-0.yaml'
    raw = yaml.safe_load(manifest.read_bytes())
    raw['as_of'] -= timedelta(days=1)
    manifest.write_text(yaml.safe_dump(raw), encoding='utf-8')
    fund = next(f for f in load_funds(manifest.parent) if f.manifest_path == manifest)
    publish_snapshot(fund, fund.constituents, fund.as_of, prior=manifest.read_bytes())


def prepare_quote_check(directory):
    """Stage two due equity quotes and a shared reporting-currency conversion."""
    holdings = directory / 'holdings.csv'
    with holdings.open(newline='', encoding='utf-8') as handle:
        rows = list(csv.DictReader(handle))
    total = sum(int(r['shares']) * int(r['manual_price']) for r in rows)
    corrected_total = total
    requests = []
    for row, currency in zip((r for r in rows if r['instrument_type'] == 'equity'), ('EUR', 'USD'), strict=False):
        price, quantity = int(row['manual_price']), int(row['shares'])
        corrected = price + 5
        final_rate = 1.25 if currency == 'USD' else 1.
        requests.append(dict(key='price:' + row['ticker'], initial=price, corrected=corrected, currency=currency))
        corrected_total += quantity * (corrected * final_rate - price)
        for column in ('manual_price', 'manual_price_currency', 'manual_price_date'):
            row[column] = ''
    requests.append(dict(key='fx:USD', initial=1., corrected=1.25, currency='EUR'))
    scenario = dict(requests=requests, currency='EUR', initial_total=total, corrected_total=corrected_total)
    (directory.parent / 'quote-check.json').write_text(json.dumps(scenario), encoding='utf-8')
    with holdings.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    cached = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
    entries = {r['key']: dict(quote=dict(price=r['initial'], currency=r['currency'], observed_at=cached),
                              attempted_at=cached, error='') for r in requests}
    path = directory / '.cache' / 'prices.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(entries), encoding='utf-8')
