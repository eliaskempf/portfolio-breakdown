"""The temporary public portfolio, with invented ownership and dated savings activity."""
import calendar
import csv
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from portfolio_app.demo_data.demo import create_demo_data, csv_content, LIVE_EXAMPLES, OFFLINE_PRICES
from portfolio_app.ledger.account_journal import Account, Event, Journal
from portfolio_app.ledger.history_commands import book_for
from portfolio_app.ledger.history_publication import publish_account_documents
from portfolio_app.ledger.security_history import Charge, SecurityEvent
from portfolio_app.performance.history_observations import Observation, ObservationStore, save_observations
from portfolio_app.portfolio.holdings import DataError, parse_holdings
from portfolio_app.workspace.display_preferences import save_overview_view
from portfolio_app.workspace.locking import write_lock
from portfolio_app.workspace.storage import revision
from portfolio_app.workspace.workspace_lock import write_context


SAVINGS = 5000
MARKER = '.public-demo'


def _months_before(day, months):
    year, month = divmod(day.year * 12 + day.month - 1 - months, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def _session(day, direction='previous'):
    # The sample trades German-listed securities. Choose completed trading dates
    # so that their real external flows can be matched to historical close prices.
    from portfolio_app.performance.history_valuation import exchange_calendar
    return exchange_calendar('XETR', day.year).date_to_session(day, direction=direction).date()


def _ownership(holdings, end):
    start = _session(_months_before(end, 30))
    first_purchase = _session(start + timedelta(days=1), direction='next')
    sold = _session(_months_before(end, 18))
    bought = _session(_months_before(end, 8))
    account = Account('demo-savings', 'Emergency fund', 'bank', 'Savings', 'EUR', 'cash')
    events = (
        Event('savings-opening', start.isoformat(), 'cash_open', account.id, 'EUR', 4400),
        Event('savings-deposit', _session(_months_before(end, 5)).isoformat(), 'cash_flow', account.id, 'EUR', 550),
        Event('savings-withdrawal', _session(_months_before(end, 2)).isoformat(), 'cash_flow', account.id, 'EUR', -50),
        Event('savings-interest', _session(end - timedelta(days=10)).isoformat(), 'interest', account.id, 'EUR', 100),
    )
    journal = Journal(version=7, accounts=(account,), events=events)
    books = []
    for _, row in holdings.iterrows():
        book = book_for(journal, row)
        quantity = Decimal(str(row.shares))
        basis = Decimal(str(row.acquisition_price))
        currency = row.acquisition_currency
        records = []
        initial = quantity
        if row.id == 'world':
            # External trade payments: the standalone bank account is savings,
            # not brokerage settlement cash. FIFO leaves the seed's unit basis.
            buy = quantity / 8
            sell = quantity / 20
            initial = quantity + sell - buy
            fee = min(Decimal('2'), buy * basis / 100)
            records.extend((
                SecurityEvent('world-sale', 'sell', sold.isoformat(), quantity=sell, price=basis * Decimal('1.08'),
                              currency=currency, charges=(Charge(fee, currency),), charges_known=True, source='synthetic_demo'),
                SecurityEvent('world-buy', 'buy', bought.isoformat(), quantity=buy, price=basis - fee / buy,
                              currency=currency, charges=(Charge(fee, currency),), charges_known=True, source='synthetic_demo'),
            ))
        elif row.id == 'ethereum':
            initial = Decimal('0')
            records.append(SecurityEvent('ethereum-buy', 'buy', _session(_months_before(end, 12)).isoformat(),
                                         quantity=quantity, price=basis, currency=currency, charges_known=True,
                                         source='synthetic_demo'))
        if initial:
            records.insert(0, SecurityEvent(row.id + '-first-buy', 'buy', first_purchase.isoformat(),
                                            quantity=initial, price=basis, currency=currency,
                                            charges_known=True, source='synthetic_demo'))
        # A funded opening snapshot cannot establish earlier realized income.
        # These are the complete invented books, including their first purchases.
        opening = SecurityEvent(row.id + '-opening', 'opening', start.isoformat(),
                                charges_known=True, source='confirmed_zero_start')
        records.insert(0, opening)
        books.append(replace(book, records=tuple(records), active_ids=tuple(e.id for e in records)))
    return replace(journal, securities=tuple(books)), start


def publish_history(directory, content, end):
    journal, start = _ownership(parse_holdings(content), end)
    path = directory / 'holdings.csv'
    publish_account_documents(directory, journal, None, {'holdings.csv': (content, revision(path))})
    return start


def _invented_prices(directory, start, end):
    path = directory / 'demo_prices.json'
    quotes = json.loads(path.read_text(encoding='utf-8'))
    for item in (*quotes['prices'].values(), *quotes['fx'].values()):
        item['observed_at'] = end.isoformat() + 'T12:00:00+00:00'
    path.write_text(json.dumps(quotes), encoding='utf-8')
    holdings = parse_holdings((directory / 'holdings.csv').read_text(encoding='utf-8'))
    days = (end - start).days
    dates = [(start + timedelta(days=offset)).isoformat() for offset in range(days + 1)]
    observations = []
    for index, row in enumerate(holdings.itertuples()):
        close = OFFLINE_PRICES[row.ticker]
        ratio = LIVE_EXAMPLES[row.id][1]
        volatility = .003 if row.id == 'money-market' else .035 + .007 * index
        for offset, stamp in enumerate(dates):
            progress = offset / days
            price = close * (ratio + (1 - ratio) * progress + volatility * math.sin(4 * math.pi * progress))
            observations.append(Observation(f'{row.id}-{stamp}', 'price', row.position_id, stamp,
                                            Decimal(str(round(price, 5))), 'EUR', listing=row.ticker, source='Invented demo close'))
    for currency, quote in quotes['fx'].items():
        observations.extend(Observation(f'{currency}-fx-{stamp}', 'fx', f'{currency}/EUR', stamp,
                                        Decimal(str(quote['price'])), currency, target='EUR',
                                        listing=f'{currency}EUR=X', source='Invented demo FX') for stamp in dates)
    save_observations(directory, ObservationStore(tuple(observations)), None)


def create_public_demo(directory: Path, *, live: bool = False, end: date | None = None) -> Path:
    """Build once on first selection. Publish the complete seed before other sessions see it."""
    directory = Path(directory)
    if (directory / MARKER).is_file():
        return directory
    end = end or date.today()
    with write_lock(directory.parent / ('.' + directory.name + '-seed.lock')):
        if (directory / MARKER).is_file():
            return directory
        if directory.exists() and any(directory.iterdir()):
            raise DataError('Use an empty directory for the public demo.')
        with TemporaryDirectory(dir=directory.parent, prefix='.public-demo-') as temporary:
            seed = create_demo_data(Path(temporary) / 'portfolio', live=live)
            path = seed / 'holdings.csv'
            with path.open(encoding='utf-8', newline='') as handle:
                reader = csv.DictReader(handle)
                columns, rows = ['position_key', *reader.fieldnames], list(reader)
            for row in rows:
                row['position_key'] = 'demo-' + row['id']
                if not live and row['id'] == 'money-market':
                    row['shares'] = str((LIVE_EXAMPLES[row['id']][0] - SAVINGS) / OFFLINE_PRICES[row['ticker']])
            content = csv_content(columns, rows)
            config = yaml.safe_load((seed / 'allocation.yaml').read_text(encoding='utf-8'))
            for bucket in config['buckets']:
                if bucket['id'] == 'money-market':
                    bucket['target'] = .20
            config['buckets'].append({'id': 'cash', 'name': 'Cash', 'target': .05})
            (seed / 'allocation.yaml').write_text(yaml.safe_dump(config, sort_keys=False), encoding='utf-8')
            # A tour can start inside a guarded personal-workspace callback.
            # Only this unpublished, temporary staging directory owns these writes.
            token = write_context.set(None)
            try:
                save_overview_view(seed, 'Allocation')
                if live:
                    path.write_bytes(content.encode('utf-8'))
                    (seed / '.live-demo').write_text(sha256(path.read_bytes()).hexdigest(), encoding='ascii')
                else:
                    start = publish_history(seed, content, end)
                    _invented_prices(seed, start, end)
            finally:
                write_context.reset(token)
            (seed / MARKER).write_text(end.isoformat(), encoding='ascii')
            if directory.exists():
                directory.rmdir()  # Windows rename requires an absent destination.
            seed.rename(directory)
    return directory
